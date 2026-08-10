import logging
from dataclasses import dataclass, field
from pathlib import Path

from trame.widgets import client, html
from trame.widgets.vuetify3 import VDataTable
from trame_server.utils.typed_state import TypedState
from trame_vuetify.widgets.vuetify3 import (
    VBtn,
    VCard,
    VCardActions,
    VCardText,
    VCardTitle,
    VDialog,
    VFileInput,
    VProgressCircular,
    VProgressLinear,
    VTooltip,
)
from undo_stack import Signal

from .flex_container import FlexContainer
from .text_components import TextField

CHUNK_LOADER_SCRIPT = Path(__file__).with_name("chunk_loader.js")


@dataclass
class LoadVolumeItemsState:
    loading_busy: bool = False
    button_tooltip: bool = False


@dataclass
class LoadVolumeState:
    file_button: LoadVolumeItemsState = field(default_factory=LoadVolumeItemsState)
    dir_button: LoadVolumeItemsState = field(default_factory=LoadVolumeItemsState)
    local_path_button: LoadVolumeItemsState = field(default_factory=LoadVolumeItemsState)
    show_local_path_button: bool = False
    local_path_dialog_visible: bool = False
    local_path_value: str = ""
    h5_selection_dialog_visible: bool = False
    h5_selection_dialog_title: str = ""
    h5_selection_dialog_path: str = ""
    h5_series_rows: list[dict] = field(default_factory=list)
    h5_selected_labels: list[str] = field(default_factory=list)
    loading_dialog_visible: bool = False
    loading_dialog_title: str = "Loading dataset"
    loading_dialog_message: str = "Reading data..."
    path_error_dialog_visible: bool = False
    path_error_dialog_message: str = ""


class LoadVolumeUI(FlexContainer):
    on_load_volume = Signal(list[dict], str)
    on_load_local_path = Signal(str, str)
    on_h5_load_selected = Signal(list[str])
    on_h5_load_all = Signal()
    on_h5_dialog_cancel = Signal()

    def __init__(self, **kwargs):
        super().__init__(row=True, **kwargs)

        typed_state = TypedState(self.state, LoadVolumeState)

        with self:
            client.Style(".v-input__prepend .v-icon { opacity: 1.0; }")
            self.load_volume_files_button = LoadVolumeButton(
                name="Open Files",
                load_directory=False,
                icon="mdi-file-upload",
                typed_state=typed_state.get_sub_state(typed_state.name.file_button),
            )
            self.load_volume_dir_button = LoadVolumeButton(
                name="Open Directory",
                load_directory=True,
                icon="mdi-folder-upload",
                typed_state=typed_state.get_sub_state(typed_state.name.dir_button),
            )
            self.load_local_path_button = LocalPathButton(typed_state=typed_state)
            self._build_h5_selection_dialog(typed_state)
            self._build_loading_dialog(typed_state)
            self._build_path_error_dialog(typed_state)

        self.load_volume_files_button.on_load_volume.connect(self.on_load_volume)
        self.load_volume_dir_button.on_load_volume.connect(self.on_load_volume)
        self.load_local_path_button.on_load_local_path.connect(self.on_load_local_path)

    def _build_h5_selection_dialog(self, typed_state: TypedState[LoadVolumeState]) -> None:
        selected_name = typed_state.name.h5_selected_labels
        rows_name = typed_state.name.h5_series_rows
        headers = """[
            { title: 'File', key: 'source_name' },
            { title: 'Series', key: 'label' },
            { title: 'Frames', key: 'frame_count' },
            { title: 'Matrix Size', key: 'matrix_size' }
        ]"""
        with VDialog(v_model=(typed_state.name.h5_selection_dialog_visible,), max_width="920"):
            with VCard():
                html.Div((typed_state.name.h5_selection_dialog_title,), classes="text-h6 px-6 pt-6")
                with VCardText(classes="pt-2"):
                    html.Div((typed_state.name.h5_selection_dialog_path,), classes="text-caption text-medium-emphasis mb-4")
                    VDataTable(
                        v_model=(selected_name,),
                        headers=(headers,),
                        items=(rows_name,),
                        item_value="item_value",
                        show_select=True,
                        return_object=False,
                        density="compact",
                        items_per_page=-1,
                        fixed_header=True,
                        height="60vh",
                        hide_default_footer=True,
                    )
                with VCardActions(classes="justify-end ga-2"):
                    VBtn(
                        text="Cancel",
                        prepend_icon="mdi-close",
                        click=self.on_h5_dialog_cancel,
                    )
                    VBtn(
                        text="Load Selected",
                        prepend_icon="mdi-check",
                        variant="tonal",
                        click=(
                            f"{typed_state.name.loading_dialog_title} = 'Loading H5 series'; "
                            f"{typed_state.name.loading_dialog_message} = 'Reading the selected H5 payloads...'; "
                            f"{typed_state.name.loading_dialog_visible} = true; "
                            "trigger('"
                            f"{self.server.controller.trigger_name(self.on_h5_load_selected.async_emit)}"
                            f"', [{selected_name}])"
                        ),
                    )
                    VBtn(
                        text="Load All",
                        prepend_icon="mdi-format-list-bulleted",
                        variant="tonal",
                        click=(
                            f"{typed_state.name.loading_dialog_title} = 'Loading H5 series'; "
                            f"{typed_state.name.loading_dialog_message} = 'Reading all H5 payloads...'; "
                            f"{typed_state.name.loading_dialog_visible} = true; "
                            "trigger('"
                            f"{self.server.controller.trigger_name(self.on_h5_load_all.async_emit)}"
                            "')"
                        ),
                    )

    def _build_loading_dialog(self, typed_state: TypedState[LoadVolumeState]) -> None:
        with VDialog(v_model=(typed_state.name.loading_dialog_visible,), persistent=True, max_width="520"):
            with VCard():
                VCardTitle((typed_state.name.loading_dialog_title,))
                with VCardText(classes="pt-2"):
                    html.Div((typed_state.name.loading_dialog_message,), classes="text-body-2 mb-4")
                    VProgressLinear(indeterminate=True, color="primary")

    def _build_path_error_dialog(self, typed_state: TypedState[LoadVolumeState]) -> None:
        with VDialog(v_model=(typed_state.name.path_error_dialog_visible,), max_width="640"):
            with VCard():
                VCardTitle("Path Not Found")
                with VCardText():
                    html.Div((typed_state.name.path_error_dialog_message,), classes="text-body-2")
                with VCardActions(classes="justify-end"):
                    VBtn(
                        text="Close",
                        prepend_icon="mdi-close",
                        click=f"{typed_state.name.path_error_dialog_visible} = false; {typed_state.name.path_error_dialog_message} = ''",
                    )


class LoadVolumeButton(FlexContainer):
    on_load_volume = Signal(list[dict], str)

    def __init__(
        self,
        name: str,
        load_directory: bool,
        icon: str,
        typed_state: TypedState[LoadVolumeItemsState],
        **kwargs,
    ):
        kwargs = {"justify": "center", "row": True, "style": "width: 50px; height: 50px;", **kwargs}
        super().__init__(**kwargs)
        client.register_external_script(
            name="load_files_by_chunks",
            script_file_path=CHUNK_LOADER_SCRIPT,
            function_names=["load_files_by_chunks"],
        )

        self._loading_state_name = typed_state.name.loading_busy
        self.files = []

        with (
            self,
            client.Handler(
                function="load_files_by_chunks",
                inputs=(f"{{ trigger_name: '{self.server.trigger_name(self.load_chunk)}' }}",),
                completed=(
                    self.on_load_end,
                    "[$event.type, $event.outputs.errorMsg]",
                ),
            ) as client_handler,
        ):
            VTooltip(
                v_model=(typed_state.name.button_tooltip,),
                text=name,
                activator="parent",
                transition="slide-y-transition",
                location="bottom start",
            )
            VFileInput(
                v_if=(f"!{typed_state.name.loading_busy}",),
                change=(
                    f"{typed_state.name.loading_busy} = true; "
                    f"{typed_state.name.button_tooltip} = false; "
                    "load_volume_items_buttons_loading_dialog_title = 'Loading dataset'; "
                    "load_volume_items_buttons_loading_dialog_message = 'Preparing the selected files...'; "
                    "load_volume_items_buttons_loading_dialog_visible = true;"
                    f"{client_handler.run('$event.target.files')}"
                ),
                prepend_icon=icon,
                multiple=not load_directory,
                hide_input=True,
                raw_attrs=["webkitdirectory"] if load_directory else [],
            )
            VProgressCircular(v_else=True, indeterminate=True, size=24)

    async def load_chunk(self, files: list[dict]) -> None:
        if files:
            self.files.extend(files)
        elif self.files:
            await self.on_load_volume.async_emit(self.files, self._loading_state_name)
            self.files = []

    def on_load_end(self, type: str, error_message: str) -> None:
        if type == "success":
            logging.info("Succeeded to load files")
            return

        logging.error("Failed to load files: %s", error_message)
        self.state[self._loading_state_name] = False


class LocalPathButton(FlexContainer):
    on_load_local_path = Signal(str, str)

    def __init__(self, typed_state: TypedState[LoadVolumeState]):
        item_state = typed_state.get_sub_state(typed_state.name.local_path_button)
        super().__init__(
            justify="center",
            row=True,
            style="width: 50px; height: 50px;",
            v_if=(typed_state.name.show_local_path_button,),
        )

        with self:
            VTooltip(
                v_model=(item_state.name.button_tooltip,),
                text="Open Local Path",
                activator="parent",
                transition="slide-y-transition",
                location="bottom start",
            )
            VBtn(
                v_if=(f"!{item_state.name.loading_busy}",),
                icon="mdi-file-document-edit-outline",
                variant="text",
                click=(
                    f"{typed_state.name.local_path_dialog_visible} = true; "
                    f"{item_state.name.button_tooltip} = false"
                ),
            )
            VProgressCircular(v_else=True, indeterminate=True, size=24)

        with VDialog(v_model=(typed_state.name.local_path_dialog_visible,), max_width="680"):
            with VCard():
                VCardTitle("Open Local Path")
                with VCardText():
                    TextField(
                        v_model=(typed_state.name.local_path_value,),
                        label="Server-side path",
                        placeholder="/path/to/file.h5",
                    )
                with VCardActions(classes="justify-end ga-2"):
                    VBtn(
                        text="Cancel",
                        prepend_icon="mdi-close",
                        click=(
                            f"{typed_state.name.local_path_dialog_visible} = false; "
                            f"{typed_state.name.local_path_value} = ''"
                        ),
                    )
                    VBtn(
                        text="Open",
                        prepend_icon="mdi-folder-open",
                        variant="tonal",
                        click=(
                            f"{item_state.name.loading_busy} = true; "
                            f"{typed_state.name.local_path_dialog_visible} = false; "
                            f"{typed_state.name.loading_dialog_title} = 'Loading local path'; "
                            f"{typed_state.name.loading_dialog_message} = 'Opening the requested server-side path...'; "
                            f"{typed_state.name.loading_dialog_visible} = true; "
                            "trigger('"
                            f"{self.server.controller.trigger_name(self.on_load_local_path.async_emit)}"
                            f"', [{typed_state.name.local_path_value}, '{item_state.name.loading_busy}'])"
                        ),
                    )
