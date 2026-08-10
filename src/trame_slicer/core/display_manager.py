from __future__ import annotations

from slicer import vtkMRMLDisplayableNode, vtkMRMLVolumeNode

from trame_slicer.views import SliceLayer

from .view_manager import ViewManager
from .volume_rendering import VolumeRendering


class DisplayManager:
    """
    Helper class to display volume nodes in given view group
    """

    def __init__(self, view_manager: ViewManager, volume_rendering: VolumeRendering):
        self._view_manager = view_manager
        self._vr = volume_rendering

    def show_volume(
        self,
        volume_node: vtkMRMLVolumeNode,
        view_group: int | None = None,
        vr_preset: str = "",
        do_reset_views: bool = False,
    ) -> None:
        if not volume_node:
            return

        self.show_volume_in_slice_background(volume_node, view_group)
        self.show_volume_in_slice_foreground(None, view_group)

        if self._supports_volume_rendering(volume_node):
            vr_display = (
                self._vr.create_display_node(volume_node, vr_preset)
                if not self._vr.has_vr_display_node(volume_node)
                else self._vr.get_vr_display_node(volume_node)
            )

            if vr_preset:
                self._vr.apply_preset(vr_display, vr_preset)

            vr_display.SetVisibility(True)
        else:
            vr_display = self._vr.get_vr_display_node(volume_node)
            if vr_display:
                vr_display.SetVisibility(False)

        self.set_node_visible_in_group(volume_node, view_group)

        if do_reset_views:
            self.reset_views(view_group)

    def hide_volume(self, volume_node: vtkMRMLVolumeNode, view_group: int | None = None):
        if not volume_node:
            return

        display_node = self._vr.get_vr_display_node(volume_node)
        if display_node:
            display_node.SetVisibility(False)

        for view in self._view_manager.get_slice_views(view_group):
            for layer in SliceLayer:
                if view.get_layer_volume_id(layer) == volume_node.GetID():
                    view.set_layer_volume_id(layer, None)

    def reset_views(self, view_group: int | None = None):
        for view in self._view_manager.get_views(view_group):
            view.reset_view()

    def show_volume_in_slice_background(
        self,
        volume_node: vtkMRMLVolumeNode | None,
        view_group: int | None = None,
    ):
        for view in self._view_manager.get_slice_views(view_group):
            view.set_background_volume_id(volume_node.GetID() if volume_node else None)

    def show_volume_in_slice_foreground(
        self,
        volume_node: vtkMRMLVolumeNode | None,
        view_group: int | None = None,
    ):
        for view in self._view_manager.get_slice_views(view_group):
            view.set_foreground_volume_id(volume_node.GetID() if volume_node else None)

    def set_node_visible_in_group(
        self,
        node: vtkMRMLDisplayableNode,
        view_group: int | None = None,
    ):
        view_node_ids = [view.get_view_node_id() for view in self._view_manager.get_views(view_group)]

        for i_display in range(node.GetNumberOfDisplayNodes()):
            display = node.GetNthDisplayNode(i_display)
            if not display or display.GetDisplayableNode() != node:
                continue

            display.SetViewNodeIDs(view_node_ids)

    @staticmethod
    def _supports_volume_rendering(volume_node: vtkMRMLVolumeNode) -> bool:
        image_data = volume_node.GetImageData() if volume_node else None
        point_data = image_data.GetPointData() if image_data else None
        scalars = point_data.GetScalars() if point_data else None
        if scalars is None:
            return False
        component_count = int(scalars.GetNumberOfComponents())
        return 1 <= component_count <= 4
