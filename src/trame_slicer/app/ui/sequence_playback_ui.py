from dataclasses import dataclass, field

from trame.widgets.vuetify3 import VProgressLinear
from trame_server.utils.typed_state import TypedState
from undo_stack import Signal

from .slider import Slider, SliderState

from ..logic.sequence_playback_config import (
    PLAYBACK_FPS_DEFAULT,
    PLAYBACK_FPS_MAX,
    PLAYBACK_FPS_MIN,
    PLAYBACK_FPS_STEP,
)
from .control_button import ControlButton
from .flex_container import FlexContainer
from .text_components import Text, TextField


@dataclass
class SequencePlaybackState:
    frame_slider: SliderState = field(default_factory=SliderState)
    is_sequence_loaded: bool = False
    is_playing: bool = False
    is_loop_enabled: bool = True
    is_fast_playback_enabled: bool = False
    playback_fps: float = PLAYBACK_FPS_DEFAULT


class SequencePlaybackUI(FlexContainer):
    play_clicked = Signal()
    pause_clicked = Signal()
    loop_clicked = Signal()
    fast_playback_clicked = Signal()

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
            ControlButton(
                icon="mdi-fast-forward",
                name="{{ " + f"{self._typed_state.name.is_fast_playback_enabled} ? 'Disable fast playback' : 'Enable fast playback'" + " }}",
                click=self.fast_playback_clicked,
                active=(self._typed_state.name.is_fast_playback_enabled,),
                disabled=(f"!{self._typed_state.name.is_sequence_loaded}",),
            )
            with FlexContainer(classes="px-2", style="min-width: 320px; max-width: 420px;"):
                Slider(
                    typed_state=slider_state,
                    thumb_label="always",
                    classes="mt-1",
                )

            with FlexContainer(row=True, align="center", classes="ga-2 px-2", style="min-width: 180px;"):
                Text("FPS", subtitle=True, classes="text-no-wrap")
                TextField(
                    type="number",
                    density="compact",
                    style="max-width: 110px;",
                    v_model=(self._typed_state.name.playback_fps,),
                    min=PLAYBACK_FPS_MIN,
                    max=PLAYBACK_FPS_MAX,
                    step=PLAYBACK_FPS_STEP,
                    disabled=(f"!{self._typed_state.name.is_sequence_loaded}",),
                )
