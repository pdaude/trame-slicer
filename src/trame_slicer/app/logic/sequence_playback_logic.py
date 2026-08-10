import asyncio

from slicer import vtkMRMLSequenceBrowserNode, vtkMRMLSequenceNode, vtkMRMLVolumeNode
from trame_server import Server
from trame_server.utils import asynchronous

from trame_slicer.core import SlicerApp

from ..ui import SequencePlaybackState, SequencePlaybackUI
from .base_logic import BaseLogic
from .sequence_playback_config import (
    FAST_PLAYBACK_FRAME_STRIDE,
    PLAYBACK_FPS_DEFAULT,
    PLAYBACK_FPS_MAX,
    PLAYBACK_FPS_MIN,
    PLAYBACK_FPS_STEP,
)


class SequencePlaybackLogic(BaseLogic[SequencePlaybackState]):
    DEFAULT_PLAYBACK_FPS = PLAYBACK_FPS_DEFAULT
    MIN_PLAYBACK_FPS = PLAYBACK_FPS_MIN
    MAX_PLAYBACK_FPS = PLAYBACK_FPS_MAX
    PLAYBACK_FPS_STEP = PLAYBACK_FPS_STEP
    FAST_PLAYBACK_FRAME_STRIDE = FAST_PLAYBACK_FRAME_STRIDE

    def __init__(self, server: Server, slicer_app: SlicerApp):
        super().__init__(server, slicer_app, SequencePlaybackState)
        self._browser_node: vtkMRMLSequenceBrowserNode | None = None
        self._sequence_node: vtkMRMLSequenceNode | None = None
        self._proxy_volume: vtkMRMLVolumeNode | None = None
        self._is_playing = False
        self._bind_changes()
        asynchronous.create_task(self._sync_browser_state_loop())
        asynchronous.create_task(self._playback_loop())

    def _bind_changes(self):
        self.bind_changes(
            {
                self.name.frame_slider.value: self._on_frame_slider_changed,
                self.name.playback_fps: self._on_playback_fps_changed,
            }
        )
        self.data.frame_slider.step = 1
        self.data.frame_slider.is_disabled = True
        self.data.is_fast_playback_enabled = False
        self.data.playback_fps = self.DEFAULT_PLAYBACK_FPS

    def set_ui(self, ui: SequencePlaybackUI):
        ui.play_clicked.connect(self._play)
        ui.pause_clicked.connect(self._pause)
        ui.loop_clicked.connect(self._toggle_loop)
        ui.fast_playback_clicked.connect(self._toggle_fast_playback)

    def on_sequence_changed(
        self,
        browser_node: vtkMRMLSequenceBrowserNode | None,
        sequence_node: vtkMRMLSequenceNode | None,
        proxy_volume: vtkMRMLVolumeNode | None,
    ):
        self._browser_node = browser_node
        self._sequence_node = sequence_node
        self._proxy_volume = proxy_volume
        self._is_playing = False
        if browser_node is not None:
            fps = self._sanitize_playback_fps(self.data.playback_fps)
            browser_node.SetPlaybackActive(False)
            browser_node.SetPlaybackRateFps(fps)
        self._sync_state_from_browser(force=True)

    async def _sync_browser_state_loop(self):
        while True:
            self._sync_state_from_browser()
            await asyncio.sleep(0.15)

    async def _playback_loop(self):
        while True:
            browser_node = self._browser_node
            sequence_node = self._sequence_node
            if browser_node is None or sequence_node is None or not self._is_playing:
                await asyncio.sleep(0.05)
                continue

            frame_count = int(browser_node.GetNumberOfItems())
            if frame_count <= 1:
                self._pause()
                await asyncio.sleep(0.05)
                continue

            fps = self._sanitize_playback_fps(browser_node.GetPlaybackRateFps())
            browser_node.SetPlaybackRateFps(fps)

            current_frame = int(browser_node.GetSelectedItemNumber())
            next_frame = current_frame + self._frame_stride()
            if next_frame >= frame_count:
                if bool(browser_node.GetPlaybackLooped()):
                    next_frame = next_frame % frame_count
                else:
                    self._pause()
                    await asyncio.sleep(0.05)
                    continue

            browser_node.SetSelectedItemNumber(next_frame)
            self._update_proxy_volume()
            self._sync_state_from_browser(force=True)
            await asyncio.sleep(1.0 / fps)

    def _sync_state_from_browser(self, force: bool = False):
        browser_node = self._browser_node
        sequence_node = self._sequence_node
        if browser_node is None or sequence_node is None:
            if force or self.data.is_sequence_loaded:
                with self.state:
                    self.data.is_sequence_loaded = False
                    self.data.is_playing = False
                    self.data.is_loop_enabled = True
                    self.data.is_fast_playback_enabled = False
                    self.data.playback_fps = self.DEFAULT_PLAYBACK_FPS
                    self.data.frame_slider.min_value = 0
                    self.data.frame_slider.max_value = 0
                    self.data.frame_slider.value = 0
                    self.data.frame_slider.step = 1
                    self.data.frame_slider.is_disabled = True
            return

        frame_count = max(int(browser_node.GetNumberOfItems()), 0)
        current_frame = min(max(int(browser_node.GetSelectedItemNumber()), 0), max(frame_count - 1, 0))
        playback_fps = self._sanitize_playback_fps(browser_node.GetPlaybackRateFps())
        browser_node.SetPlaybackActive(self._is_playing)

        needs_update = force or any(
            [
                not self.data.is_sequence_loaded,
                bool(self.data.is_playing) != self._is_playing,
                bool(self.data.is_loop_enabled) != bool(browser_node.GetPlaybackLooped()),
                float(self.data.playback_fps) != playback_fps,
                int(self.data.frame_slider.max_value) != max(frame_count - 1, 0),
                int(round(self.data.frame_slider.value)) != current_frame,
                bool(self.data.frame_slider.is_disabled) != (frame_count <= 1),
            ]
        )
        if not needs_update:
            return

        with self.state:
            self.data.is_sequence_loaded = True
            self.data.is_playing = self._is_playing
            self.data.is_loop_enabled = bool(browser_node.GetPlaybackLooped())
            self.data.playback_fps = playback_fps
            self.data.frame_slider.min_value = 0
            self.data.frame_slider.max_value = max(frame_count - 1, 0)
            self.data.frame_slider.value = current_frame
            self.data.frame_slider.step = 1
            self.data.frame_slider.is_disabled = frame_count <= 1

    def _on_frame_slider_changed(self, frame_value: float):
        browser_node = self._browser_node
        if browser_node is None:
            return

        frame_index = int(round(frame_value))
        if frame_index == int(browser_node.GetSelectedItemNumber()):
            return

        browser_node.SetSelectedItemNumber(frame_index)
        self._update_proxy_volume()
        self._sync_state_from_browser(force=True)

    def _on_playback_fps_changed(self, fps_value: float):
        browser_node = self._browser_node
        sanitized = self._sanitize_playback_fps(fps_value)
        if float(self.data.playback_fps) != sanitized:
            self.data.playback_fps = sanitized
        if browser_node is not None:
            browser_node.SetPlaybackRateFps(sanitized)

    def _play(self):
        browser_node = self._browser_node
        if browser_node is None:
            return

        browser_node.SetPlaybackRateFps(self._sanitize_playback_fps(self.data.playback_fps))
        self._is_playing = True
        browser_node.SetPlaybackActive(True)
        self._sync_state_from_browser(force=True)

    def _pause(self):
        browser_node = self._browser_node
        self._is_playing = False
        if browser_node is None:
            self._sync_state_from_browser(force=True)
            return

        browser_node.SetPlaybackActive(False)
        self._sync_state_from_browser(force=True)

    def _toggle_loop(self):
        browser_node = self._browser_node
        if browser_node is None:
            return

        browser_node.SetPlaybackLooped(not bool(browser_node.GetPlaybackLooped()))
        self._sync_state_from_browser(force=True)

    def _toggle_fast_playback(self):
        self.data.is_fast_playback_enabled = not bool(self.data.is_fast_playback_enabled)
        self._sync_state_from_browser(force=True)

    def _frame_stride(self) -> int:
        return self.FAST_PLAYBACK_FRAME_STRIDE if bool(self.data.is_fast_playback_enabled) else 1

    def _update_proxy_volume(self):
        browser_node = self._browser_node
        if browser_node is None:
            return

        self._slicer_app.sequences_logic.UpdateProxyNodesFromSequences(browser_node)

        for view in self._slicer_app.view_manager.get_slice_views():
            view.schedule_render()

    def _sanitize_playback_fps(self, fps_value: float | int | None) -> float:
        try:
            fps = float(fps_value)
        except (TypeError, ValueError):
            fps = self.DEFAULT_PLAYBACK_FPS
        fps = max(self.MIN_PLAYBACK_FPS, min(self.MAX_PLAYBACK_FPS, fps))
        remainder = fps % self.PLAYBACK_FPS_STEP
        if remainder:
            lower = fps - remainder
            upper = lower + self.PLAYBACK_FPS_STEP
            fps = upper if (fps - lower) >= (upper - fps) else lower
        return max(self.MIN_PLAYBACK_FPS, min(self.MAX_PLAYBACK_FPS, fps))
