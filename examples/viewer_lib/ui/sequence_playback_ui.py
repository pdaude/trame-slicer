from dataclasses import dataclass, field

from trame_server.utils.typed_state import TypedState
from trame_vuetify.widgets.vuetify3 import VProgressLinear
from undo_stack import Signal

from trame_slicer.ui import Slider, SliderState

from .control_button import ControlButton
from .flex_container import FlexContainer


@dataclass
class SequencePlaybackState:
    frame_slider: SliderState = field(default_factory=SliderState)
    is_sequence_loaded: bool = False
    is_playing: bool = False
    is_loop_enabled: bool = True


class SequencePlaybackUI(FlexContainer):
    play_clicked = Signal()
    pause_clicked = Signal()
    loop_clicked = Signal()

    def __init__(self, **kwargs):
        super().__init__(row=True, align="center", classes="ga-2", **kwargs)
        self._typed_state = TypedState(self.state, SequencePlaybackState)

        slider_state = self._typed_state.get_sub_state(self._typed_state.name.frame_slider)

        with self:
            ControlButton(
                icon="mdi-play",
                name="Play sequence",
                click=self.play_clicked,
                disabled=(
                    f"!{self._typed_state.name.is_sequence_loaded} || {self._typed_state.name.is_playing}",
                ),
            )
            ControlButton(
                icon="mdi-pause",
                name="Pause sequence",
                click=self.pause_clicked,
                disabled=(
                    f"!{self._typed_state.name.is_sequence_loaded} || !{self._typed_state.name.is_playing}",
                ),
            )
            ControlButton(
                icon="mdi-repeat",
                name="{{ " + f"{self._typed_state.name.is_loop_enabled} ? 'Disable loop playback' : 'Enable loop playback'" + " }}",
                click=self.loop_clicked,
                active=(self._typed_state.name.is_loop_enabled,),
                disabled=(f"!{self._typed_state.name.is_sequence_loaded}",),
            )
            with FlexContainer(classes="px-2", style="min-width: 320px; max-width: 420px;"):
                Slider(
                    typed_state=slider_state,
                    thumb_label="always",
                    classes="mt-1",
                )
                VProgressLinear(
                    model_value=(
                        f"{slider_state.name.max_value} > 0 ? (100 * {slider_state.name.value} / {slider_state.name.max_value}) : 0",
                    ),
                    color="primary",
                    height=2,
                    rounded=True,
                )
