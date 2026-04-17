from trame_server import Server

from trame_slicer.core import SlicerApp

from ..ui import DatasetListItemState, DatasetManagerState, DatasetManagerUI
from .astrolit_load_volume_logic import AstroLITLoadVolumeLogic
from .base_logic import BaseLogic


class DatasetManagerLogic(BaseLogic[DatasetManagerState]):
    def __init__(self, server: Server, slicer_app: SlicerApp, load_logic: AstroLITLoadVolumeLogic):
        super().__init__(server, slicer_app, DatasetManagerState)
        self._load_logic = load_logic
        self._load_logic.datasets_changed.connect(self._on_datasets_changed)

    def set_ui(self, ui: DatasetManagerUI):
        ui.toggle_dataset_visibility_clicked.connect(self._load_logic.toggle_dataset_visibility)
        ui.remove_dataset_clicked.connect(self._load_logic.remove_dataset)
        ui.move_dataset_up_clicked.connect(self._load_logic.move_dataset_up)
        ui.move_dataset_down_clicked.connect(self._load_logic.move_dataset_down)

    def _on_datasets_changed(self, datasets):
        self.data.datasets = [
            DatasetListItemState(
                dataset_id=dataset.dataset_id,
                name=dataset.name,
                kind=dataset.kind,
                is_visible=dataset.is_visible,
            )
            for dataset in datasets
        ]
