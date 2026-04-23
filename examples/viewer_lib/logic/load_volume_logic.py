from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from slicer import vtkMRMLSequenceBrowserNode, vtkMRMLSequenceNode, vtkMRMLVolumeNode
from trame_server import Server
from undo_stack import Signal
from vtkmodules.vtkCommonMath import vtkMatrix4x4
from vtkmodules.vtkImagingCore import vtkImageExtractComponents

from trame_slicer.core import SlicerApp
from trame_slicer.utils import write_client_files_to_dir

from ..ui import (
    LoadVolumeState,
    LoadVolumeUI,
)
from .base_logic import BaseLogic


class LoadVolumeLogic(BaseLogic[LoadVolumeState]):
    volume_loaded = Signal(vtkMRMLVolumeNode)
    sequence_loaded = Signal(object, object, object)

    def __init__(self, server: Server, slicer_app: SlicerApp):
        super().__init__(server, slicer_app, LoadVolumeState)
        self._active_sequence_browser: vtkMRMLSequenceBrowserNode | None = None
        self._active_sequence_node: vtkMRMLSequenceNode | None = None
        self._active_proxy_volume: vtkMRMLVolumeNode | None = None
        self._ui: LoadVolumeUI | None = None

    def set_ui(self, ui: LoadVolumeUI):
        self._ui = ui
        ui.on_load_volume.connect(self._on_load_volume)
        ui.on_load_local_path.connect(self._on_load_local_path)

    def _on_load_volume(self, files: list[dict], is_loading_state_name: str) -> None:
        try:
            self._load_volume_files(files)
        finally:
            self.state[is_loading_state_name] = False
            self._hide_loading_dialog()

    def _on_load_local_path(self, path_value: str, is_loading_state_name: str) -> None:
        try:
            self._load_local_path(path_value)
        finally:
            self.state[is_loading_state_name] = False
            self.state[self.name.local_path_value] = ""
            self._hide_loading_dialog()

    def _load_volume_files(self, files: list[dict]) -> None:
        if not files:
            return

        self._clear_active_sequence_context()
        self._slicer_app.scene.Clear()

        with TemporaryDirectory() as tmp_dir:
            loaded_files = write_client_files_to_dir(files, tmp_dir)
            if len(loaded_files) == 1 and loaded_files[0].endswith(".mrb"):
                self._on_load_scene(loaded_files[0])
            else:
                self._on_load_volume_files(loaded_files)

    def _load_local_path(self, path_value: str) -> None:
        resolved_path = Path((path_value or "").strip()).expanduser()
        if not resolved_path.exists():
            print(f"[Load Volume] local path not found: {resolved_path}")
            self._show_path_error_dialog(resolved_path)
            return

        self._clear_active_sequence_context()
        self._slicer_app.scene.Clear()

        if resolved_path.suffix.lower() == ".mrb":
            self._on_load_scene(str(resolved_path))
            return

        self._on_load_volume_files([str(resolved_path)])


    def _hide_loading_dialog(self) -> None:
        self.state[self.name.loading_dialog_visible] = False
        self.state[self.name.loading_dialog_title] = 'Loading dataset'
        self.state[self.name.loading_dialog_message] = 'Reading data...'

    def _show_path_error_dialog(self, path_value: Path, message: str | None = None) -> None:
        self.state[self.name.path_error_dialog_message] = message or f'The path does not exist: {path_value}'
        self.state[self.name.path_error_dialog_visible] = True

    def _on_load_scene(self, scene_file):
        self._slicer_app.io_manager.load_scene(scene_file)
        self._show_largest_volume(list(self._slicer_app.scene.GetNodesByClass("vtkMRMLVolumeNode")))

    def _on_load_volume_files(self, loaded_files):
        if len(loaded_files) == 1 and self._is_sequence_candidate(loaded_files[0]):
            if self._try_load_sequence(loaded_files[0]):
                return
            self._slicer_app.scene.Clear()

        volumes = self._slicer_app.io_manager.load_volumes(loaded_files)
        if not volumes:
            return

        if len(loaded_files) == 1 and self._is_sequence_candidate(loaded_files[0]):
            if self._try_build_sequence_from_loaded_volumes(volumes, loaded_files[0]):
                return

        self._show_largest_volume(volumes)

    def _show_largest_volume(self, volumes):
        if not volumes:
            return

        self._clear_active_sequence_context()

        def bounds_volume(v):
            b = [0] * 6
            v.GetImageData().GetBounds(b)
            return (b[1] - b[0]) * (b[3] - b[2]) * (b[5] - b[4])

        volumes = sorted(volumes, key=bounds_volume)
        volume_node = volumes[-1]

        self._slicer_app.display_manager.show_volume(
            volume_node,
            do_reset_views=True,
        )

        self.volume_loaded(volume_node)

    def _is_sequence_candidate(self, file_path: str) -> bool:
        suffixes = [suffix.lower() for suffix in Path(file_path).suffixes]
        return any(suffix in {".seq", ".nrrd", ".nhdr"} for suffix in suffixes)

    def _try_load_sequence(self, sequence_file: str) -> bool:
        try:
            sequence_node = self._slicer_app.sequences_logic.AddSequence(sequence_file)
        except Exception:
            return False

        if not isinstance(sequence_node, vtkMRMLSequenceNode):
            return False

        item_count = int(sequence_node.GetNumberOfDataNodes())
        if item_count <= 1:
            return False

        browser_node = self._ensure_sequence_browser(sequence_node)
        if browser_node is None:
            return False

        proxy_volume = browser_node.GetProxyNode(sequence_node)
        if proxy_volume is None and sequence_node.GetNumberOfDataNodes() > 0:
            proxy_volume = browser_node.AddProxyNode(sequence_node.GetNthDataNode(0), sequence_node, True)

        if not isinstance(proxy_volume, vtkMRMLVolumeNode):
            return False

        return self._activate_sequence_browser(browser_node, sequence_node, proxy_volume)

    def _try_build_sequence_from_loaded_volumes(self, volumes: list[vtkMRMLVolumeNode], source_file: str) -> bool:
        if len(volumes) != 1:
            return False

        source_volume = volumes[0]
        image_data = source_volume.GetImageData()
        point_data = image_data.GetPointData() if image_data else None
        scalars = point_data.GetScalars() if point_data else None
        component_count = int(scalars.GetNumberOfComponents()) if scalars is not None else 0

        if component_count <= 1:
            return False

        sequence_node = self.scene.AddNewNodeByClass(
            "vtkMRMLSequenceNode",
            f"{Path(source_file).stem} sequence",
        )
        sequence_node.SetIndexName("frame")
        sequence_node.SetIndexUnit("")

        ijk_to_ras = vtkMatrix4x4()
        source_volume.GetIJKToRASMatrix(ijk_to_ras)

        for frame_index in range(component_count):
            extractor = vtkImageExtractComponents()
            extractor.SetInputData(image_data)
            extractor.SetComponents(frame_index)
            extractor.Update()

            frame_node = self.scene.AddNewNodeByClass(
                "vtkMRMLScalarVolumeNode",
                f"{source_volume.GetName()} frame {frame_index + 1}",
            )
            frame_node.SetIJKToRASMatrix(ijk_to_ras)
            frame_node.SetAndObserveImageData(extractor.GetOutput())
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

        self.scene.RemoveNode(source_volume)
        return self._activate_sequence_browser(browser_node, sequence_node, proxy_volume)

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

        self._slicer_app.display_manager.show_volume(proxy_volume, do_reset_views=do_reset_views)
        self.volume_loaded(proxy_volume)
        return True

    def _ensure_sequence_browser(self, sequence_node: vtkMRMLSequenceNode) -> vtkMRMLSequenceBrowserNode | None:
        sequences_logic = self._slicer_app.sequences_logic
        browser_node = sequences_logic.GetFirstBrowserNodeForSequenceNode(sequence_node)
        if browser_node is None:
            browser_node = self.scene.AddNewNodeByClass(
                "vtkMRMLSequenceBrowserNode",
                f"{sequence_node.GetName()} browser",
            )
            browser_node.SetAndObserveMasterSequenceNodeID(sequence_node.GetID())
            if sequence_node.GetNumberOfDataNodes() > 0:
                browser_node.AddProxyNode(sequence_node.GetNthDataNode(0), sequence_node, True)
        return browser_node

    def _clear_active_sequence_context(self):
        had_sequence = any(
            item is not None
            for item in [
                self._active_sequence_browser,
                self._active_sequence_node,
                self._active_proxy_volume,
            ]
        )
        self._active_sequence_browser = None
        self._active_sequence_node = None
        self._active_proxy_volume = None
        if had_sequence:
            self.sequence_loaded(None, None, None)
