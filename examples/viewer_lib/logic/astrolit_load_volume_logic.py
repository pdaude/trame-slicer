from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
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

from .load_volume_logic import LoadVolumeLogic


@dataclass
class ObservatoryLoadContext:
    subject_id: str
    reconstruction_name: str
    reconstruction_path: Path
    load_mode: str
    selected_images: list[str]


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

        self._reset_managed_datasets()
        self._clear_active_sequence_context()
        self._slicer_app.scene.Clear()
        return self._load_reconstruction_h5_context(context)

    def _load_volume_files(self, files: list[dict]) -> None:
        self._reset_managed_datasets()
        super()._load_volume_files(files)

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

    def _load_reconstruction_h5_context(self, context: ObservatoryLoadContext) -> bool:
        selected_labels = set(context.selected_images)
        only_selected = context.load_mode == "selected" and bool(selected_labels)
        matching_series = self._read_reconstruction_series(
            context.reconstruction_path,
            selected_labels if only_selected else None,
        )

        print(
            "[AstroLIT viewer] selected series labels:",
            [label for label, _series in matching_series],
        )

        if only_selected and not matching_series:
            print(
                f"[AstroLIT viewer] no matching image series for {context.reconstruction_name}: {sorted(selected_labels)}"
            )
            return False

        if not matching_series:
            print(f"[AstroLIT viewer] no reconstruction data loaded from {context.reconstruction_path}")
            return False

        sequence_activated = False
        created_scalar_volumes: list[vtkMRMLVolumeNode] = []
        for label, series in matching_series:
            print(f"[AstroLIT viewer] building MRML nodes for series {label}")
            if self._series_frame_count(series) > 1 and not sequence_activated:
                if self._create_sequence_from_h5_series(context.reconstruction_name, label, series):
                    print(f"[AstroLIT viewer] activated sequence browser for series {label}")
                    sequence_activated = True
                    continue

            volume_node = self._create_scalar_volume_from_h5_series(context.reconstruction_name, label, series)
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
        matching_series: list[tuple[str, dict[str, Any]]] = []
        with ismrmrd.File(h5_path, "r") as mrd:
            image_keys = list(mrd.find_images())
            print(
                f"[AstroLIT viewer] reconstruction series discovered: {len(image_keys)} for {h5_path.name}"
            )
            for fallback_index, key_img in enumerate(image_keys):
                image_group = mrd[key_img].images
                headers = [
                    dict(zip(image_group.headers[i].dtype.names, image_group.headers[i]))
                    for i in range(image_group.headers.shape[0])
                ]
                label = self._series_label({"header": headers}, fallback_index)
                if selected_labels is not None and label not in selected_labels:
                    continue

                print(f"[AstroLIT viewer] reading H5 payload for series {label} ({key_img})")
                image_data = np.array(image_group.data).T
                if len(image_data.dtype) == 2:
                    image_data = image_data["real"] + 1j * image_data["imag"]
                print(
                    f"[AstroLIT viewer] loaded series {label}: shape={image_data.shape} dtype={image_data.dtype}"
                )
                matching_series.append((label, {"volume": image_data, "header": headers}))
        return matching_series

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
