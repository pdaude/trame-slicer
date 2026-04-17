from trame_server import Server

from trame_slicer.core import LayoutManager, SlicerApp
from trame_slicer.rca_view import register_rca_factories

from ..ui import (
    AstroLITViewerUI,
    DatasetManagerUI,
    SegmentEditorUI,
    SequencePlaybackUI,
    ViewerLayoutState,
    VolumePropertyUI,
)
from .astrolit_load_volume_logic import AstroLITLoadVolumeLogic
from .base_logic import BaseLogic
from .dataset_manager_logic import DatasetManagerLogic
from .layout_button_logic import LayoutButtonLogic
from .markups_button_logic import MarkupsButtonLogic
from .mpr_interaction_button_logic import MprInteractionButtonLogic
from .segmentation import SegmentEditorLogic
from .sequence_playback_logic import SequencePlaybackLogic
from .slab_logic import SlabLogic
from .volume_property_logic import VolumePropertyLogic


class AstroLITViewerLogic(BaseLogic[ViewerLayoutState]):
    def __init__(self, server: Server, slicer_app: SlicerApp):
        super().__init__(server, slicer_app, ViewerLayoutState)

        register_rca_factories(self._slicer_app.view_manager, self._server)

        self._segment_editor_logic = SegmentEditorLogic(server, slicer_app)
        self._volume_properties_logic = VolumePropertyLogic(server, slicer_app)
        self._layout_button_logic = LayoutButtonLogic(server, slicer_app)
        self._markups_logic = MarkupsButtonLogic(server, slicer_app)
        self._load_files_logic = AstroLITLoadVolumeLogic(server, slicer_app)
        self._dataset_manager_logic = DatasetManagerLogic(server, slicer_app, self._load_files_logic)
        self._sequence_playback_logic = SequencePlaybackLogic(server, slicer_app)
        self._slab_logic = SlabLogic(server, slicer_app)
        self._mpr_logic = MprInteractionButtonLogic(server, slicer_app)
        self._last_observatory_search: str | None = None

        self._load_files_logic.volume_loaded.connect(self._on_volume_changed)
        self._load_files_logic.volume_loaded.connect(self._volume_properties_logic.on_volume_changed)
        self._load_files_logic.volume_loaded.connect(self._segment_editor_logic.on_volume_changed)
        self._load_files_logic.sequence_loaded.connect(self._sequence_playback_logic.on_sequence_changed)

        self.server.state["trame__title"] = "Observatory"

    @property
    def layout_manager(self) -> LayoutManager:
        return self._layout_button_logic.layout_manager

    def set_ui(self, ui: AstroLITViewerUI):
        self._segment_editor_logic.set_ui(ui.tool_registry[SegmentEditorUI])
        self._volume_properties_logic.set_ui(ui.tool_registry[VolumePropertyUI])
        self._dataset_manager_logic.set_ui(ui.tool_registry[DatasetManagerUI])
        self._layout_button_logic.set_ui(ui.layout_button)
        self._markups_logic.set_ui(ui.markups_button)
        self._load_files_logic.set_ui(ui.load_volume_items_buttons)
        self._sequence_playback_logic.set_ui(ui.sequence_playback_controls)
        self._slab_logic.set_ui(ui.slab_button)
        self._mpr_logic.set_ui(ui.mpr_interaction_button)
        ui.observatory_context_requested.connect(self._on_observatory_context_requested)

    def _on_volume_changed(self, *_args):
        self.data.is_volume_loaded = True

    def _on_observatory_context_requested(self, search: str | None):
        normalized_search = search or ""
        if normalized_search == self._last_observatory_search:
            return
        self._last_observatory_search = normalized_search
        self._load_files_logic.load_from_observatory_query(normalized_search)
