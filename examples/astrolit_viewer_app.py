from trame.app import get_server
from trame.app.testing import enable_testing
from trame.decorators import TrameApp

try:
    from viewer_lib import AstroLITViewerLogic, AstroLITViewerUI
except ModuleNotFoundError:
    from .viewer_lib import AstroLITViewerLogic, AstroLITViewerUI

from trame_slicer.core import SlicerApp


@TrameApp()
class AstroLITViewerApp:
    def __init__(self, server=None):
        self._server = get_server(server, client_type="vue3")
        self._slicer_app = SlicerApp()

        self._logic = AstroLITViewerLogic(self._server, self._slicer_app)
        self._ui = AstroLITViewerUI(self._server, self._logic.layout_manager)
        self._logic.set_ui(self._ui)

    @property
    def server(self):
        return self._server


def main(server=None, **kwargs):
    app = AstroLITViewerApp(server)
    enable_testing(app.server)
    app.server.start(**kwargs)


if __name__ == "__main__":
    main()
