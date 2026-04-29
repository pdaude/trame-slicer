from slicer import vtkMRMLDisplayableNode, vtkMRMLVolumeNode
from trame_server import Server

from trame_slicer.core import SlicerApp, VolumeWindowLevel
from trame_slicer.resources import (
    get_volume_display_presets_icon_url,
    get_volume_rendering_presets_icon_url,
)

from ..ui import Preset, VolumePropertyState, VolumePropertyUI
from .base_logic import BaseLogic


class VolumePropertyLogic(BaseLogic[VolumePropertyState]):
    def __init__(self, server: Server, slicer_app: SlicerApp, dataset_provider=None):
        super().__init__(server, slicer_app, VolumePropertyState)
        self._volume_node = None
        self._dataset_provider = dataset_provider
        self._populate_presets_2d()
        self._populate_presets_3d()

        self._typed_state.bind_changes(
            {
                self.name.active_dataset_id: self._on_active_dataset_changed,
                self.name.vr_shift_slider.value: self._set_vr_shift_value,
                self.name.window_level_slider.value: self._set_window_level_value,
                self.name.preset_2d_name: self._set_preset_2d,
                self.name.preset_3d_name: self._set_preset_3d,
            }
        )
        if self._dataset_provider is not None:
            self._dataset_provider.datasets_changed.connect(self._on_datasets_changed)

    def set_ui(self, ui: VolumePropertyUI):
        ui.auto_window_level_clicked.connect(self._auto_window_level)
        ui.vr_crop_button_clicked.connect(self._toggle_vr_crop)
        ui.vr_visibility_clicked.connect(self._toggle_volume_rendering_visibility)

    @property
    def _volume_rendering(self):
        return self._slicer_app.volume_rendering

    @property
    def _volumes_logic(self):
        return self._slicer_app.volumes_logic

    def _get_presets_from_name_and_image(self, presets_icon_url: list[tuple[str, str]]) -> list[Preset]:
        return [Preset(title=name, props={"data": data}) for name, data in presets_icon_url]

    def _populate_presets_2d(self):
        presets_icon_url = get_volume_display_presets_icon_url(
            icons_folder=(self._slicer_app.share_directory / "presets_icons" / "2d"),
            volumes_logic=self._volumes_logic,
        )
        self.data.presets_2d = self._get_presets_from_name_and_image(presets_icon_url)

    def _populate_presets_3d(self):
        presets_icon_url = get_volume_rendering_presets_icon_url(
            icons_folder=(self._slicer_app.share_directory / "presets_icons" / "3d"),
            volume_rendering=self._volume_rendering,
        )
        self.data.presets_3d = self._get_presets_from_name_and_image(presets_icon_url)

    def on_volume_changed(self, volume_node: vtkMRMLVolumeNode):
        if self._dataset_provider is not None:
            active_dataset_id = self.data.active_dataset_id
            if active_dataset_id:
                dataset_volume = self._dataset_provider.get_dataset_volume_node(active_dataset_id)
                if dataset_volume is not None:
                    volume_node = dataset_volume

        self._volume_node = volume_node

        if self._volume_node is None or self._volume_node.GetScene() is None:
            return

        self._volume_node.AddObserver(vtkMRMLDisplayableNode.DisplayModifiedEvent, self._update_window_level_slider)

        self._init_preset()
        self._init_window_level_slider()
        self._set_volume_rendering_visible(False)

    def _on_datasets_changed(self, datasets) -> None:
        options = [
            {
                "title": dataset.name,
                "value": dataset.dataset_id,
            }
            for dataset in datasets
            if dataset.is_visible
            and self._dataset_provider.get_dataset_volume_node(dataset.dataset_id) is not None
        ]
        self.data.active_dataset_options = options

        current_id = self.data.active_dataset_id
        valid_ids = {option["value"] for option in options}
        if current_id not in valid_ids:
            current_id = self._dataset_provider.get_highest_visible_dataset_id()
            self.data.active_dataset_id = current_id

        if current_id:
            node = self._dataset_provider.get_dataset_volume_node(current_id)
            if node is not None:
                self.on_volume_changed(node)
                return

        self._volume_node = None
        self.data.volume_rendering_visible = False
        self.data.volume_crop_active = False

    def _on_active_dataset_changed(self, dataset_id: str | None) -> None:
        if self._dataset_provider is None or not dataset_id:
            return
        volume_node = self._dataset_provider.get_dataset_volume_node(dataset_id)
        if volume_node is None:
            return
        self.on_volume_changed(volume_node)

    def _init_preset(self):
        self._set_preset_3d(self.data.preset_3d_name)

    def _auto_window_level(self):
        if not self._has_valid_volume_node():
            return

        self.data.window_level_slider.value = list(VolumeWindowLevel.get_volume_auto_min_max_range(self._volume_node))

    def _toggle_vr_crop(self):
        was_active = self._typed_state.data.volume_crop_active
        if not self._has_valid_volume_node():
            return

        display_node = self._volume_rendering.get_vr_display_node(self._volume_node)
        if display_node is None:
            self.data.volume_crop_active = False
            return
        roi_node = display_node.GetROINode()

        roi_node = self._volume_rendering.set_cropping_enabled(self._volume_node, roi_node, True)
        is_active = not was_active
        roi_node.SetDisplayVisibility(is_active)
        self.data.volume_crop_active = is_active

    def _init_window_level_slider(self):
        if not self._has_valid_volume_node():
            return

        scalar_min, scalar_max = VolumeWindowLevel.get_volume_scalar_range(self._volume_node)
        display_node = self._volume_node.GetDisplayNode()

        if display_node is None:
            self.data.window_level_slider.min_value = scalar_min
            self.data.window_level_slider.max_value = scalar_max
            self._auto_window_level()
            return

        display_min, display_max = VolumeWindowLevel.get_volume_display_range(self._volume_node)
        self.data.window_level_slider.min_value = min(scalar_min, display_min)
        self.data.window_level_slider.max_value = max(scalar_max, display_max)
        self.data.window_level_slider.value = [display_min, display_max]

    def _init_vr_shift_slider(self):
        if self._volume_rendering.get_vr_display_node(self._volume_node) is None:
            self.data.vr_shift_slider.min_value = 0
            self.data.vr_shift_slider.max_value = 0
            self.data.vr_shift_slider.value = 0
            return
        self.data.vr_shift_slider.min_value, self.data.vr_shift_slider.max_value = (
            self._volume_rendering.get_preset_vr_shift_range(self.data.preset_3d_name)
        )
        self.data.vr_shift_slider.value = 0

    def _set_preset_2d(self, preset_name: str):
        if not self._has_valid_volume_node() or preset_name is None:
            return
        volume_display_node = VolumeWindowLevel.get_volume_display_node(self._volume_node)
        if volume_display_node is None:
            return
        self._slicer_app.volumes_logic.ApplyVolumeDisplayPreset(volume_display_node, preset_name)

    def _set_preset_3d(self, preset_name: str):
        if not self._has_valid_volume_node():
            return

        vr_node = self._volume_rendering.get_vr_display_node(self._volume_node)
        if vr_node is None:
            self._init_vr_shift_slider()
            return
        self._volume_rendering.apply_preset(vr_node, preset_name)
        self._init_vr_shift_slider()

    def _set_window_level_value(self, window_level: list[float]):
        if not self._has_valid_volume_node():
            return

        min_value, max_value = window_level
        VolumeWindowLevel.set_volume_node_display_min_max_range(self._volume_node, min_value, max_value)

    def _update_window_level_slider(self, *_args, **_kwargs):
        if not self._has_valid_volume_node():
            return

        min_value, max_value = VolumeWindowLevel.get_volume_display_range(self._volume_node)
        current_min_value = self._typed_state.data.window_level_slider.min_value
        self._typed_state.data.window_level_slider.min_value = min(
            current_min_value,
            min_value,
        )
        current_max_value = self._typed_state.data.window_level_slider.max_value
        self._typed_state.data.window_level_slider.max_value = max(
            current_max_value,
            max_value,
        )
        self._typed_state.data.window_level_slider.value = [min_value, max_value]

    def _set_vr_shift_value(self, vr_shift_value: float):
        if not self._has_valid_volume_node():
            return
        if self._volume_rendering.get_vr_display_node(self._volume_node) is None:
            return

        self._volume_rendering.set_absolute_vr_shift_from_preset(
            self._volume_node,
            self.data.preset_3d_name,
            vr_shift_value,
        )

    def _toggle_volume_rendering_visibility(self):
        self._set_volume_rendering_visible(not bool(self.data.volume_rendering_visible))

    def _set_volume_rendering_visible(self, is_visible: bool):
        if not self._has_valid_volume_node():
            self.data.volume_rendering_visible = False
            self.data.volume_crop_active = False
            self._init_vr_shift_slider()
            return

        vr_node = self._volume_rendering.get_vr_display_node(self._volume_node)
        if is_visible:
            if vr_node is None:
                vr_node = self._volume_rendering.create_display_node(self._volume_node, self.data.preset_3d_name)
            else:
                self._volume_rendering.apply_preset(vr_node, self.data.preset_3d_name)
            if vr_node is not None:
                vr_node.SetVisibility(True)
            self.data.volume_rendering_visible = True
        else:
            if vr_node is not None:
                vr_node.SetVisibility(False)
            self.data.volume_rendering_visible = False
            self.data.volume_crop_active = False

        self._init_vr_shift_slider()

    def _has_valid_volume_node(self) -> bool:
        return (
            self._volume_node is not None
            and self._volume_node.GetScene() is not None
            and self._volume_node.GetImageData() is not None
        )
