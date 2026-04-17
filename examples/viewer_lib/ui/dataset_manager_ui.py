from dataclasses import dataclass, field

from trame.widgets import client
from trame.widgets.html import Div, Span
from trame.widgets.vuetify3 import Template, VCard, VCardText, VIcon, VList, VListItem
from trame_server.utils.typed_state import TypedState
from undo_stack import Signal

from .text_components import Text


@dataclass
class DatasetListItemState:
    dataset_id: str = ""
    name: str = ""
    kind: str = ""
    is_visible: bool = True


@dataclass
class DatasetManagerState:
    datasets: list[DatasetListItemState] = field(default_factory=list)


class DatasetManagerUI(VCard):
    toggle_dataset_visibility_clicked = Signal(str)
    remove_dataset_clicked = Signal(str)
    move_dataset_up_clicked = Signal(str)
    move_dataset_down_clicked = Signal(str)

    def __init__(self, **kwargs):
        super().__init__(variant="flat", **kwargs)
        self._typed_state = TypedState(self.state, DatasetManagerState)
        self._build_ui()

    def _build_ui(self):
        with self, VCardText():
            Text("Datasets", subtitle=True)
            client.Style(".astrolit-dataset-name { font-weight: 500; }")
            with VList(density="compact"):
                with VListItem(
                    v_for=f"(item, i) in {self._typed_state.name.datasets}",
                    key="i",
                    value="item",
                ):
                    with Template(v_slot_default=True):
                        with Div(classes="d-flex flex-column"):
                            Span("{{ item.name }}", classes="astrolit-dataset-name")
                            Span("{{ item.kind }}", classes="text-caption text-medium-emphasis")

                    with Template(v_slot_append=True):
                        VIcon(
                            icon="mdi-arrow-up",
                            classes="mr-2",
                            click=self._server_trigger(self.move_dataset_up_clicked),
                        )
                        VIcon(
                            icon="mdi-arrow-down",
                            classes="mr-3",
                            click=self._server_trigger(self.move_dataset_down_clicked),
                        )
                        VIcon(
                            icon=("item.is_visible ? 'mdi-eye-outline' : 'mdi-eye-off-outline'",),
                            classes="mr-3",
                            click=self._server_trigger(self.toggle_dataset_visibility_clicked),
                        )
                        VIcon(
                            icon="mdi-delete-outline",
                            click=self._server_trigger(self.remove_dataset_clicked),
                        )

    def _server_trigger(self, signal: Signal) -> str:
        return f"trigger('{self.server.trigger_name(signal)}', [item.dataset_id]);"
