from dataclasses import dataclass, field

from trame.widgets.html import Span
from trame.widgets.vuetify3 import Template, VCard, VCardText, VImg, VListItem, VSelect
from trame_server.utils.typed_state import TypedState
from undo_stack import Signal

from trame_slicer.ui import RangeSlider, RangeSliderState, Slider, SliderState

from .control_button import ControlButton
from .flex_container import FlexContainer
from .text_components import Text


@dataclass
class Preset:
    title: str
    props: dict[str, str]


@dataclass
class VolumePropertyState:
    active_dataset_options: list[dict[str, str]] = field(default_factory=list)
    active_dataset_id: str | None = None
    window_level_slider: RangeSliderState = field(default_factory=RangeSliderState)
    vr_shift_slider: SliderState = field(default_factory=SliderState)
    presets_3d: list[Preset] = field(default_factory=list)
    preset_3d_name: str = "CT-Coronary-Arteries-3"
    presets_2d: list[Preset] = field(default_factory=list)
    preset_2d_name: str | None = None
    volume_crop_active: bool = False
    volume_rendering_visible: bool = False


class VolumePropertyUI(VCard):
    auto_window_level_clicked = Signal()
    vr_crop_button_clicked = Signal()
    vr_visibility_clicked = Signal()

    def __init__(self, **kwargs):
        super().__init__(variant="flat", **kwargs)
        self._typed_state = TypedState(self.state, VolumePropertyState)

        with self, VCardText(), FlexContainer():
            Text("Active Dataset", subtitle=True)
            VSelect(
                items=(self._typed_state.name.active_dataset_options,),
                v_model=(self._typed_state.name.active_dataset_id,),
                item_title="title",
                item_value="value",
                density="compact",
                hide_details=True,
                disabled=(f"{self._typed_state.name.active_dataset_options}.length === 0",),
            )

            with FlexContainer(row=True, align="center", classes="ga-2"):
                Text("3D Rendering", subtitle=True)
                ControlButton(
                    icon=(
                        "{{ " + f"{self._typed_state.name.volume_rendering_visible} ? 'mdi-cube' : 'mdi-cube-off-outline'" + " }}",
                    ),
                    name=(
                        "{{ "
                        + f"{self._typed_state.name.volume_rendering_visible} ? 'Hide 3D rendering' : 'Show 3D rendering'"
                        + " }}"
                    ),
                    active=(self._typed_state.name.volume_rendering_visible,),
                    click=self.vr_visibility_clicked,
                )

            Text("3D Preset", subtitle=True)
            with VSelect(
                items=(self._typed_state.name.presets_3d,),
                v_model=(self._typed_state.name.preset_3d_name,),
            ):
                with (
                    Template(v_slot_item="{props}"),
                    VListItem(v_bind="props"),
                    Template(v_slot_prepend=""),
                ):
                    VImg(src=("props.data",), height=64, width=64)

                with Template(v_slot_selection="{item}"):
                    VImg(src=("item.props.data",), height=32, width=32)
                    Span("{{item.title}}", classes="pl-2")

            Text("2D Preset", subtitle=True)
            with VSelect(
                items=(self._typed_state.name.presets_2d,),
                v_model=(self._typed_state.name.preset_2d_name,),
            ):
                with (
                    Template(v_slot_item="{props}"),
                    VListItem(v_bind="props"),
                    Template(v_slot_prepend=""),
                ):
                    VImg(src=("props.data",), height=64, width=64)

                with Template(v_slot_selection="{item}"):
                    VImg(src=("item.props.data",), height=32, width=32)
                    Span("{{item.title}}", classes="pl-2")

            Text("Window / level", subtitle=True)
            with (
                RangeSlider(typed_state=self._typed_state.get_sub_state(self._typed_state.name.window_level_slider)),
                Template(v_slot_append=True),
            ):
                ControlButton(
                    name="Auto Window/Level",
                    icon="mdi-refresh-auto",
                    click=self.auto_window_level_clicked,
                )

            Text("Volume Rendering Shift", subtitle=True)
            with (
                Slider(typed_state=self._typed_state.get_sub_state(self._typed_state.name.vr_shift_slider)),
                Template(v_slot_append=True),
            ):
                ControlButton(
                    icon="mdi-crop",
                    name="Crop volume rendering",
                    active=(self._typed_state.name.volume_crop_active,),
                    click=self.vr_crop_button_clicked,
                    disabled=(f"!{self._typed_state.name.volume_rendering_visible}",),
                    **kwargs,
                )
