from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import ismrmrd
import numpy as np
import slicer.util
from slicer import vtkMRMLSequenceBrowserNode, vtkMRMLSequenceNode, vtkMRMLVolumeNode
from trame_server import Server
from undo_stack import Signal
from vtkmodules.vtkCommonMath import vtkMatrix4x4

from trame_slicer.core import SlicerApp
from trame_slicer.utils import write_client_files_to_dir

from .load_volume_logic import LoadVolumeLogic


@dataclass
class ObservatoryLoadContext:
    subject_id: str
    reconstruction_name: str
    reconstruction_path: Path
    load_mode: str
    selected_images: list[str]


@dataclass
class H5SeriesSummary:
    label: str
    key_img: str
    frame_count: int
    matrix_size: str
    first_header: dict[str, Any] | None = None


@dataclass
class PendingH5Selection:
    h5_path: Path
    display_name: str
    series_rows: list[H5SeriesSummary]
    temp_dir: TemporaryDirectory[str] | None = None


@dataclass
class ManagedDataset:
    dataset_id: str
    name: str
    kind: str
    visible_node_ids: list[str]
    removal_node_ids: list[str]
    is_visible: bool = True


class AstroLITLoadVolumeLogic(LoadVolumeLogic):
    datasets_changed = Signal(object)

    def __init__(self, server: Server, slicer_app: SlicerApp):
        super().__init__(server, slicer_app)
        self._managed_datasets: dict[str, ManagedDataset] = {}
        self._dataset_counter = 0
        self._pending_h5_selection: PendingH5Selection | None = None

    def set_ui(self, ui):
        super().set_ui(ui)
        self.state[self.name.show_local_path_button] = True
        ui.on_h5_load_selected.connect(self._on_h5_load_selected)
        ui.on_h5_load_all.connect(self._on_h5_load_all)
        ui.on_h5_dialog_cancel.connect(self._on_h5_dialog_cancel)

    def load_from_observatory_query(self, search: str) -> bool:
        context = self._resolve_observatory_context(search)
        if context is None:
            print(f"[AstroLIT viewer] no observatory load context in query: {search}")
            return False

        print(
            "[AstroLIT viewer] loading context:",
            {
                "subject_id": context.subject_id,
                "reconstruction": context.reconstruction_name,
                "path": context.reconstruction_path.as_posix(),
                "load_mode": context.load_mode,
                "selected_images": context.selected_images,
            },
        )

        if context.reconstruction_path.suffix.lower() != ".h5":
            print(f"[AstroLIT viewer] unsupported reconstruction type: {context.reconstruction_path}")
            return False

        self._cleanup_pending_h5_selection()
        self._prepare_new_scene()
        return self._load_reconstruction_h5_context(context)

    def _load_volume_files(self, files: list[dict]) -> None:
        self._cleanup_pending_h5_selection()
        if not files:
            return

        tmp_dir: TemporaryDirectory[str] | None = TemporaryDirectory()
        try:
            loaded_files = write_client_files_to_dir(files, tmp_dir.name)
            if len(loaded_files) == 1:
                loaded_path = Path(loaded_files[0])
                if loaded_path.suffix.lower() == ".mrb":
                    self._prepare_new_scene()
                    self._on_load_scene(loaded_files[0])
                    return
                if loaded_path.suffix.lower() == ".h5":
                    print(f"[AstroLIT viewer] scanning uploaded h5: {loaded_path}")
                    if self._present_h5_selection_dialog(loaded_path, loaded_path.name, temp_dir=tmp_dir):
                        tmp_dir = None
                        return
                    print(f"[AstroLIT viewer] failed to scan uploaded h5: {loaded_path}")
                    return

            self._prepare_new_scene()
            self._on_load_volume_files(loaded_files)
        finally:
            if tmp_dir is not None:
                tmp_dir.cleanup()

    def _load_local_path(self, path_value: str) -> None:
        self._cleanup_pending_h5_selection()
        resolved_path = Path((path_value or "").strip()).expanduser()
        if not resolved_path.exists():
            print(f"[AstroLIT viewer] local path not found: {resolved_path}")
            return

        if resolved_path.suffix.lower() == ".h5":
            print(f"[AstroLIT viewer] scanning local h5: {resolved_path}")
            if not self._present_h5_selection_dialog(resolved_path, resolved_path.name):
                print(f"[AstroLIT viewer] failed to scan local h5: {resolved_path}")
            return

        self._prepare_new_scene()
        if resolved_path.suffix.lower() == ".mrb":
            self._on_load_scene(str(resolved_path))
            return

        self._on_load_volume_files([str(resolved_path)])

    def _show_largest_volume(self, volumes):
        if not volumes:
            return

        self._register_volume_datasets(volumes)
        self._clear_active_sequence_context()

        def bounds_volume(v):
            b = [0] * 6
            v.GetImageData().GetBounds(b)
            return (b[1] - b[0]) * (b[3] - b[2]) * (b[5] - b[4])

        volumes = sorted(volumes, key=bounds_volume)
        volume_node = volumes[-1]
        self._show_volume_in_slices(volume_node, do_reset_views=True)
        self._apply_dataset_stack(do_reset_views=False)

    def _activate_sequence_browser(
        self,
        browser_node: vtkMRMLSequenceBrowserNode,
        sequence_node: vtkMRMLSequenceNode,
        proxy_volume: vtkMRMLVolumeNode,
        do_reset_views: bool = True,
    ) -> bool:
        browser_node.SetSelectedItemNumber(0)
        browser_node.SetPlaybackActive(False)
        browser_node.SetPlaybackLooped(True)
        self._slicer_app.sequences_logic.UpdateProxyNodesFromSequences(browser_node)

        self._active_sequence_browser = browser_node
        self._active_sequence_node = sequence_node
        self._active_proxy_volume = proxy_volume
        self.sequence_loaded(browser_node, sequence_node, proxy_volume)

        self._register_sequence_dataset(browser_node, sequence_node, proxy_volume)
        self._show_volume_in_slices(proxy_volume, do_reset_views=do_reset_views)
        self._apply_dataset_stack(do_reset_views=False)
        return True

    def toggle_dataset_visibility(self, dataset_id: str) -> None:
        dataset = self._managed_datasets.get(dataset_id)
        if dataset is None:
            return

        dataset.is_visible = not dataset.is_visible
        self._emit_datasets_changed()
        self._apply_dataset_stack(do_reset_views=False)

    def move_dataset_up(self, dataset_id: str) -> None:
        self._move_dataset(dataset_id, -1)

    def move_dataset_down(self, dataset_id: str) -> None:
        self._move_dataset(dataset_id, 1)

    def remove_dataset(self, dataset_id: str) -> None:
        dataset = self._managed_datasets.pop(dataset_id, None)
        if dataset is None:
            return

        if self._active_sequence_node and self._active_sequence_node.GetID() in dataset.removal_node_ids:
            self._clear_active_sequence_context()

        for node_id in reversed(dataset.removal_node_ids):
            node = self.scene.GetNodeByID(node_id)
            if node is not None:
                self.scene.RemoveNode(node)

        self._emit_datasets_changed()
        self._apply_dataset_stack(do_reset_views=False)

    def _resolve_observatory_context(self, search: str) -> ObservatoryLoadContext | None:
        query = parse_qs((search or "").lstrip("?"), keep_blank_values=False)
        subject_id = self._sanitize_single_query_value(query.get("subjectId"), r"[A-Za-z0-9_-]+")
        reconstruction_name = self._sanitize_single_query_value(query.get("reconstruction"), r"[A-Za-z0-9._-]+")
        load_mode = self._sanitize_single_query_value(query.get("loadMode"), r"all|selected") or "all"
        selected_images_value = self._sanitize_single_query_value(query.get("selectedImages"), r"[A-Za-z0-9._,-]+")

        if not subject_id or not reconstruction_name:
            return None

        selected_images = [item for item in (selected_images_value or "").split(",") if item]
        subject_index_path = self._resolve_astrolit_data_root() / "index" / "subjects" / f"{subject_id}.json"
        if not subject_index_path.is_file():
            print(f"[AstroLIT viewer] missing subject index: {subject_index_path}")
            return None

        subject_record = json.loads(subject_index_path.read_text())
        reconstruction_path = None
        for file_record in subject_record.get("reconstruction_files", []):
            if file_record.get("file_name") == reconstruction_name:
                reconstruction_path = Path(file_record["file_path"])
                break

        if reconstruction_path is None:
            print(f"[AstroLIT viewer] reconstruction not found for subject {subject_id}: {reconstruction_name}")
            return None

        return ObservatoryLoadContext(
            subject_id=subject_id,
            reconstruction_name=reconstruction_name,
            reconstruction_path=reconstruction_path,
            load_mode=load_mode,
            selected_images=selected_images,
        )

    def _sanitize_single_query_value(self, values: list[str] | None, pattern: str) -> str | None:
        if not values or len(values) != 1:
            return None
        value = values[0]
        if not re.fullmatch(pattern, value):
            return None
        return value

    def _resolve_astrolit_data_root(self) -> Path:
        configured = os.getenv("ASTROLIT_DATA_ROOT")
        if configured:
            return Path(configured)
        return Path(__file__).resolve().parents[4] / "AstroLIT" / "data"

    def _prepare_new_scene(self) -> None:
        self._reset_managed_datasets()
        self._clear_active_sequence_context()
        self._slicer_app.scene.Clear()

    def _load_uploaded_h5_file(self, h5_path: Path) -> bool:
        return self._load_reconstruction_h5_file(
            reconstruction_path=h5_path,
            reconstruction_name=h5_path.name,
            load_mode="all",
            selected_images=[],
        )

    def _load_reconstruction_h5_context(self, context: ObservatoryLoadContext) -> bool:
        return self._load_reconstruction_h5_file(
            reconstruction_path=context.reconstruction_path,
            reconstruction_name=context.reconstruction_name,
            load_mode=context.load_mode,
            selected_images=context.selected_images,
        )

    def _present_h5_selection_dialog(
        self,
        h5_path: Path,
        display_name: str,
        temp_dir: TemporaryDirectory[str] | None = None,
    ) -> bool:
        series_rows = self._scan_reconstruction_series(h5_path)
        if not series_rows:
            if temp_dir is not None:
                temp_dir.cleanup()
            return False

        self._pending_h5_selection = PendingH5Selection(
            h5_path=h5_path,
            display_name=display_name,
            series_rows=series_rows,
            temp_dir=temp_dir,
        )
        self.state[self.name.h5_selection_dialog_title] = f"Select image series from {display_name}"
        self.state[self.name.h5_selection_dialog_path] = h5_path.as_posix()
        self.state[self.name.h5_series_rows] = [
            {
                "label": row.label,
                "frame_count": str(row.frame_count),
                "matrix_size": row.matrix_size,
            }
            for row in series_rows
        ]
        self.state[self.name.h5_selected_labels] = [row.label for row in series_rows]
        self.state[self.name.h5_selection_dialog_visible] = True
        return True

    def _on_h5_load_selected(self, selected_labels: list[str]) -> None:
        labels = [str(label) for label in (selected_labels or [])]
        try:
            if not labels:
                print("[AstroLIT viewer] no H5 series selected")
                return
            self._finalize_pending_h5_selection(load_mode="selected", selected_labels=labels)
        finally:
            self._hide_loading_dialog()

    def _on_h5_load_all(self) -> None:
        try:
            self._finalize_pending_h5_selection(load_mode="all", selected_labels=[])
        finally:
            self._hide_loading_dialog()

    def _on_h5_dialog_cancel(self) -> None:
        self._hide_loading_dialog()
        self._cleanup_pending_h5_selection()

    def _finalize_pending_h5_selection(self, load_mode: str, selected_labels: list[str]) -> None:
        pending = self._pending_h5_selection
        if pending is None:
            return

        self._hide_h5_selection_dialog()
        self._prepare_new_scene()
        try:
            selected_set = set(selected_labels)
            only_selected = load_mode == "selected" and bool(selected_set)
            matching_series = self._read_reconstruction_series_from_scan(
                pending.h5_path,
                pending.series_rows,
                selected_set if only_selected else None,
            )
            self._build_loaded_reconstruction_series(
                pending.display_name,
                pending.h5_path,
                matching_series,
                selected_set,
                only_selected,
            )
        finally:
            self._cleanup_pending_h5_selection()

    def _hide_h5_selection_dialog(self) -> None:
        self.state[self.name.h5_selection_dialog_visible] = False
        self.state[self.name.h5_selection_dialog_title] = ""
        self.state[self.name.h5_selection_dialog_path] = ""
        self.state[self.name.h5_series_rows] = []
        self.state[self.name.h5_selected_labels] = []

    def _cleanup_pending_h5_selection(self) -> None:
        pending = self._pending_h5_selection
        self._pending_h5_selection = None
        self._hide_h5_selection_dialog()
        if pending is not None and pending.temp_dir is not None:
            pending.temp_dir.cleanup()

    def _load_reconstruction_h5_file(
        self,
        reconstruction_path: Path,
        reconstruction_name: str,
        load_mode: str,
        selected_images: list[str],
    ) -> bool:
        selected_labels = set(selected_images)
        only_selected = load_mode == "selected" and bool(selected_labels)
        matching_series = self._read_reconstruction_series(
            reconstruction_path,
            selected_labels if only_selected else None,
        )

        return self._build_loaded_reconstruction_series(
            reconstruction_name,
            reconstruction_path,
            matching_series,
            selected_labels,
            only_selected,
        )

    def _build_loaded_reconstruction_series(
        self,
        reconstruction_name: str,
        reconstruction_path: Path,
        matching_series: list[tuple[str, dict[str, Any]]],
        selected_labels: set[str],
        only_selected: bool,
    ) -> bool:

        print(
            "[AstroLIT viewer] selected series labels:",
            [label for label, _series in matching_series],
        )

        if only_selected and not matching_series:
            print(
                f"[AstroLIT viewer] no matching image series for {reconstruction_name}: {sorted(selected_labels)}"
            )
            return False

        if not matching_series:
            print(f"[AstroLIT viewer] no reconstruction data loaded from {reconstruction_path}")
            return False

        sequence_activated = False
        created_scalar_volumes: list[vtkMRMLVolumeNode] = []
        for label, series in matching_series:
            print(f"[AstroLIT viewer] building MRML nodes for series {label}")
            if self._series_frame_count(series) > 1 and not sequence_activated:
                if self._create_sequence_from_h5_series(reconstruction_name, label, series):
                    print(f"[AstroLIT viewer] activated sequence browser for series {label}")
                    sequence_activated = True
                    continue

            volume_node = self._create_scalar_volume_from_h5_series(reconstruction_name, label, series)
            if volume_node is not None:
                print(f"[AstroLIT viewer] created scalar volume for series {label}: {volume_node.GetName()}")
                created_scalar_volumes.append(volume_node)
                self._register_volume_datasets([volume_node])

        if sequence_activated:
            self._apply_dataset_stack(do_reset_views=False)
            return True

        if not created_scalar_volumes:
            return False

        self._show_largest_volume(created_scalar_volumes)
        return True

    def _show_volume_in_slices(self, volume_node: vtkMRMLVolumeNode, do_reset_views: bool) -> None:
        if volume_node is None:
            return

        self._hide_volume_rendering(volume_node)
        self._slicer_app.display_manager.show_volume_in_slice_background(volume_node, view_group=None)
        self._slicer_app.display_manager.show_volume_in_slice_foreground(None, view_group=None)
        self._slicer_app.display_manager.set_node_visible_in_group(volume_node, view_group=None)
        if do_reset_views:
            self._slicer_app.display_manager.reset_views(view_group=None)
        self.volume_loaded(volume_node)

    def _hide_volume_rendering(self, volume_node: vtkMRMLVolumeNode) -> None:
        vr_display = self._slicer_app.volume_rendering.get_vr_display_node(volume_node)
        if vr_display is not None:
            vr_display.SetVisibility(False)

    def _move_dataset(self, dataset_id: str, delta: int) -> None:
        items = list(self._managed_datasets.items())
        current_index = next((index for index, (key, _value) in enumerate(items) if key == dataset_id), None)
        if current_index is None:
            return

        target_index = current_index + delta
        if target_index < 0 or target_index >= len(items):
            return

        item = items.pop(current_index)
        items.insert(target_index, item)
        self._managed_datasets = dict(items)
        self._emit_datasets_changed()
        self._apply_dataset_stack(do_reset_views=False)

    def _apply_dataset_stack(self, do_reset_views: bool) -> None:
        for dataset in self._managed_datasets.values():
            for node_id in dataset.visible_node_ids:
                node = self.scene.GetNodeByID(node_id)
                if not isinstance(node, vtkMRMLVolumeNode):
                    continue
                if dataset.is_visible:
                    node.SetDisplayVisibility(True)
                    self._hide_volume_rendering(node)
                    self._slicer_app.display_manager.set_node_visible_in_group(node, view_group=None)
                else:
                    self._slicer_app.display_manager.hide_volume(node, view_group=None)

        visible_nodes: list[vtkMRMLVolumeNode] = []
        for dataset in self._managed_datasets.values():
            if not dataset.is_visible:
                continue
            for node_id in dataset.visible_node_ids:
                node = self.scene.GetNodeByID(node_id)
                if isinstance(node, vtkMRMLVolumeNode):
                    visible_nodes.append(node)
                    break

        self._slicer_app.display_manager.show_volume_in_slice_background(None, view_group=None)
        self._slicer_app.display_manager.show_volume_in_slice_foreground(None, view_group=None)
        for view in self._slicer_app.view_manager.get_slice_views():
            view.set_foreground_opacity(0.0)

        if not visible_nodes:
            return

        background_node = visible_nodes[-1]
        self._slicer_app.display_manager.show_volume_in_slice_background(background_node, view_group=None)

        if len(visible_nodes) > 1:
            foreground_node = visible_nodes[0]
            self._slicer_app.display_manager.show_volume_in_slice_foreground(foreground_node, view_group=None)
            for view in self._slicer_app.view_manager.get_slice_views():
                view.set_foreground_opacity(0.45)

        self.volume_loaded(background_node)
        if do_reset_views:
            self._slicer_app.display_manager.reset_views(view_group=None)

    def _read_reconstruction_series(
        self,
        h5_path: Path,
        selected_labels: set[str] | None,
    ) -> list[tuple[str, dict[str, Any]]]:
        scan_rows = self._scan_reconstruction_series(h5_path)
        print(
            "[AstroLIT viewer] loading series from scan:",
            [row.label for row in scan_rows if selected_labels is None or row.label in selected_labels],
        )
        return self._read_reconstruction_series_from_scan(h5_path, scan_rows, selected_labels)

    def _read_reconstruction_series_from_scan(
        self,
        h5_path: Path,
        scan_rows: list[H5SeriesSummary],
        selected_labels: set[str] | None,
    ) -> list[tuple[str, dict[str, Any]]]:
        row_by_label = {row.label: row for row in scan_rows}
        ordered_rows = (
            [row_by_label[label] for label in selected_labels if label in row_by_label]
            if selected_labels is not None
            else scan_rows
        )
        matching_series: list[tuple[str, dict[str, Any]]] = []
        with ismrmrd.File(h5_path, "r") as mrd:
            for row in ordered_rows:
                image_group = mrd[row.key_img].images
                print(f"[AstroLIT viewer] reading H5 payload for series {row.label} ({row.key_img})")
                image_data = np.array(image_group.data).T
                if len(image_data.dtype) == 2:
                    image_data = image_data["real"] + 1j * image_data["imag"]
                print(
                    f"[AstroLIT viewer] loaded series {row.label}: shape={image_data.shape} dtype={image_data.dtype}"
                )
                matching_series.append(
                    (
                        row.label,
                        {
                            "volume": image_data,
                            "header": [row.first_header] if row.first_header else [],
                            "frame_count": row.frame_count,
                        },
                    )
                )
        return matching_series

    def _scan_reconstruction_series(self, h5_path: Path) -> list[H5SeriesSummary]:
        series_rows: list[H5SeriesSummary] = []
        with ismrmrd.File(h5_path, "r") as mrd:
            image_keys = list(mrd.find_images())
            print(
                f"[AstroLIT viewer] reconstruction series discovered: {len(image_keys)} for {h5_path.name}"
            )
            for fallback_index, key_img in enumerate(image_keys):
                image_group = mrd[key_img].images
                header = self._first_series_header(image_group)
                label = self._series_label({"header": [header] if header else []}, fallback_index)
                raw_shape = tuple(int(value) for value in image_group.data.shape)
                volume_shape = tuple(reversed(raw_shape))
                frame_count = self._frame_count_from_shape(volume_shape, image_group.headers.shape[0])
                series_rows.append(
                    H5SeriesSummary(
                        label=label,
                        key_img=key_img,
                        frame_count=frame_count,
                        matrix_size=self._matrix_size_string(volume_shape, frame_count, header),
                        first_header=header,
                    )
                )
        return series_rows

    def _first_series_header(self, image_group: Any) -> dict[str, Any] | None:
        if image_group.headers.shape[0] <= 0:
            return None
        header_record = image_group.headers[0]
        return dict(zip(header_record.dtype.names, header_record))

    def _frame_count_from_shape(self, volume_shape: tuple[int, ...], header_count: int) -> int:
        if header_count > 0:
            return int(header_count)
        squeezed_shape = [dim for dim in volume_shape if dim != 1]
        if len(squeezed_shape) >= 4:
            return int(squeezed_shape[-1])
        return 1

    def _matrix_size_string(
        self,
        volume_shape: tuple[int, ...],
        frame_count: int,
        header: dict[str, Any] | None,
    ) -> str:
        if header:
            matrix_size = np.asarray(header.get("matrix_size", []), dtype=int).reshape(-1)
            if matrix_size.size >= 3:
                return " x ".join(str(int(value)) for value in matrix_size[:3])

        squeezed_shape = [int(dim) for dim in volume_shape if int(dim) != 1]
        if frame_count > 1 and len(squeezed_shape) >= 4 and squeezed_shape[-1] == frame_count:
            squeezed_shape = squeezed_shape[:-1]
        matrix_dims = squeezed_shape[:3] if squeezed_shape else [1]
        return " x ".join(str(value) for value in matrix_dims)

    def _series_label(self, series: dict[str, Any], fallback_index: int) -> str:
        headers = series.get("header") or []
        if headers and isinstance(headers[0], dict):
            value = headers[0].get("image_series_index")
            if value is not None:
                return str(value)
        return str(fallback_index)

    def _series_frame_count(self, series: dict[str, Any]) -> int:
        headers = series.get("header") or []
        if headers:
            return len(headers)
        volume = np.asarray(series.get("volume"))
        if volume.ndim >= 4:
            return int(volume.shape[-1])
        return 1

    def _create_sequence_from_h5_series(
        self,
        reconstruction_name: str,
        series_label: str,
        series: dict[str, Any],
    ) -> bool:
        volume = np.asarray(series.get("volume"))
        frame_count = self._series_frame_count(series)
        if frame_count <= 1 or volume.ndim < 4:
            return False

        sequence_node = self.scene.AddNewNodeByClass(
            "vtkMRMLSequenceNode",
            f"{Path(reconstruction_name).stem} series {series_label} sequence",
        )
        sequence_node.SetIndexName("frame")
        sequence_node.SetIndexUnit("")

        header = self._series_header(series)
        matrix = self._create_ijk_to_ras_matrix(header)
        for frame_index in range(frame_count):
            frame_node = self.scene.AddNewNodeByClass(
                "vtkMRMLScalarVolumeNode",
                f"{Path(reconstruction_name).stem} series {series_label} frame {frame_index + 1}",
            )
            if matrix is not None:
                frame_node.SetIJKToRASMatrix(matrix)
            slicer.util.updateVolumeFromArray(frame_node, self._to_slicer_spatial_array(volume[..., frame_index]))
            frame_node.CreateDefaultDisplayNodes()
            sequence_node.SetDataNodeAtValue(frame_node, str(frame_index))

        browser_node = self._ensure_sequence_browser(sequence_node)
        if browser_node is None:
            self.scene.RemoveNode(sequence_node)
            return False

        proxy_volume = browser_node.GetProxyNode(sequence_node)
        if proxy_volume is None and sequence_node.GetNumberOfDataNodes() > 0:
            proxy_volume = browser_node.AddProxyNode(sequence_node.GetNthDataNode(0), sequence_node, True)

        if not isinstance(proxy_volume, vtkMRMLVolumeNode):
            self.scene.RemoveNode(sequence_node)
            return False

        return self._activate_sequence_browser(browser_node, sequence_node, proxy_volume)

    def _create_scalar_volume_from_h5_series(
        self,
        reconstruction_name: str,
        series_label: str,
        series: dict[str, Any],
    ) -> vtkMRMLVolumeNode | None:
        volume = np.asarray(series.get("volume"))
        if volume.ndim >= 4:
            volume = volume[..., 0]
        volume = np.squeeze(volume)
        if volume.ndim not in {2, 3}:
            return None

        node = self.scene.AddNewNodeByClass(
            "vtkMRMLScalarVolumeNode",
            f"{Path(reconstruction_name).stem} series {series_label}",
        )
        matrix = self._create_ijk_to_ras_matrix(self._series_header(series))
        if matrix is not None:
            node.SetIJKToRASMatrix(matrix)
        slicer.util.updateVolumeFromArray(node, self._to_slicer_spatial_array(volume))
        node.CreateDefaultDisplayNodes()
        return node

    def _series_header(self, series: dict[str, Any]) -> dict[str, Any] | None:
        headers = series.get("header") or []
        if headers and isinstance(headers[0], dict):
            return headers[0]
        return None

    def _create_ijk_to_ras_matrix(self, header: dict[str, Any] | None) -> vtkMatrix4x4 | None:
        if not header:
            return None

        field_of_view = np.asarray(header.get("field_of_view", []), dtype=float).reshape(-1)
        matrix_size = np.asarray(header.get("matrix_size", []), dtype=float).reshape(-1)
        if field_of_view.size < 3 or matrix_size.size < 3:
            return None

        matrix = vtkMatrix4x4()
        matrix.Identity()
        for axis in range(3):
            size = matrix_size[axis]
            spacing = float(field_of_view[axis] / size) if size else 1.0
            matrix.SetElement(axis, axis, spacing)
        return matrix

    def _to_slicer_spatial_array(self, volume: np.ndarray) -> np.ndarray:
        array = np.asarray(volume)
        if np.iscomplexobj(array):
            array = np.abs(array)
        array = np.squeeze(array)
        array = np.asarray(array, dtype=np.float32)
        if array.ndim not in {2, 3}:
            raise ValueError(f"Unsupported array shape for Slicer volume: {array.shape}")
        axes = tuple(reversed(range(array.ndim)))
        return np.transpose(array, axes=axes).copy()

    def _register_volume_datasets(self, volumes: list[vtkMRMLVolumeNode]) -> None:
        changed = False
        for volume_node in volumes:
            if volume_node is None or volume_node.GetID() is None:
                continue
            if self._has_managed_node_id(volume_node.GetID()):
                continue
            dataset = self._create_dataset(
                name=volume_node.GetName(),
                kind="volume",
                visible_node_ids=[volume_node.GetID()],
                removal_node_ids=[volume_node.GetID()],
            )
            self._managed_datasets[dataset.dataset_id] = dataset
            changed = True
        if changed:
            self._emit_datasets_changed()

    def _register_sequence_dataset(
        self,
        browser_node: vtkMRMLSequenceBrowserNode,
        sequence_node: vtkMRMLSequenceNode,
        proxy_volume: vtkMRMLVolumeNode,
    ) -> None:
        if proxy_volume.GetID() is None or self._has_managed_node_id(proxy_volume.GetID()):
            return

        removal_node_ids = [
            node_id
            for node_id in [browser_node.GetID(), sequence_node.GetID(), proxy_volume.GetID()]
            if node_id is not None
        ]
        for index in range(sequence_node.GetNumberOfDataNodes()):
            data_node = sequence_node.GetNthDataNode(index)
            if data_node is not None and data_node.GetID() is not None:
                removal_node_ids.append(data_node.GetID())

        dataset = self._create_dataset(
            name=proxy_volume.GetName(),
            kind="sequence",
            visible_node_ids=[proxy_volume.GetID()],
            removal_node_ids=removal_node_ids,
        )
        self._managed_datasets[dataset.dataset_id] = dataset
        self._emit_datasets_changed()

    def _create_dataset(
        self,
        *,
        name: str,
        kind: str,
        visible_node_ids: list[str],
        removal_node_ids: list[str],
    ) -> ManagedDataset:
        self._dataset_counter += 1
        return ManagedDataset(
            dataset_id=f"dataset-{self._dataset_counter}",
            name=name,
            kind=kind,
            visible_node_ids=list(dict.fromkeys(visible_node_ids)),
            removal_node_ids=list(dict.fromkeys(removal_node_ids)),
            is_visible=True,
        )

    def _has_managed_node_id(self, node_id: str) -> bool:
        return any(node_id in dataset.removal_node_ids for dataset in self._managed_datasets.values())

    def _reset_managed_datasets(self) -> None:
        self._managed_datasets = {}
        self._dataset_counter = 0
        self._emit_datasets_changed()

    def _emit_datasets_changed(self) -> None:
        self.datasets_changed(list(self._managed_datasets.values()))

    def _show_first_visible_dataset(self, do_reset_views: bool) -> None:
        for dataset in self._managed_datasets.values():
            if not dataset.is_visible or not dataset.visible_node_ids:
                continue
            self._show_managed_volume(node_id=dataset.visible_node_ids[0], do_reset_views=do_reset_views)
            return

    def _show_managed_volume(self, node_id: str, do_reset_views: bool) -> None:
        node = self.scene.GetNodeByID(node_id)
        if not isinstance(node, vtkMRMLVolumeNode):
            return
        self._show_volume_in_slices(node, do_reset_views=do_reset_views)
