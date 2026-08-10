from trame.app import TrameApp

from trame_slicer.app.logic import AstroLITViewerLogic
from trame_slicer.app.ui import AstroLITViewerUI
from trame_slicer.core import SlicerApp


class AstroLITViewerApp(TrameApp):
    def __init__(self, server=None):
        super().__init__(server)
        self._slicer_app = SlicerApp()
        self._logic = AstroLITViewerLogic(self.server, self._slicer_app)
        self._ui = AstroLITViewerUI(self.server, self._logic.layout_manager)
        self._logic.set_ui(self._ui)


def main(server=None, **kwargs):
    app = AstroLITViewerApp(server)
    app.server.start(**kwargs)


if __name__ == "__main__":
    main()
