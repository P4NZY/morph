"""Native macOS window chrome for Morph.

Adds a real AppKit unified toolbar with a centred segmented control
(Convert | Upscale | Compress), like Activity Monitor or Preview. The page below it is
told which tool to show; when the page switches tool by itself (keyboard
shortcut, restored state), it reports back so the toolbar stays in sync.
"""

from __future__ import annotations

import json
import threading

import AppKit
import objc
from PyObjCTools import AppHelper

TABS = [("convert", "Convert"), ("upscale", "Upscale"), ("compress", "Compress")]
TOOLBAR_ID = "MorphToolbar"
TABS_ITEM_ID = "MorphTabs"

_keep_alive: list = []  # AppKit holds toolbar delegates weakly


class ToolbarController(AppKit.NSObject):
    def initWithPyWindow_(self, pywindow):
        self = objc.super(ToolbarController, self).init()
        if self is None:
            return None
        self.pywindow = pywindow
        self.segmented = None
        return self

    # -- NSToolbarDelegate -------------------------------------------------
    def toolbarDefaultItemIdentifiers_(self, toolbar):
        return [AppKit.NSToolbarFlexibleSpaceItemIdentifier, TABS_ITEM_ID,
                AppKit.NSToolbarFlexibleSpaceItemIdentifier]

    def toolbarAllowedItemIdentifiers_(self, toolbar):
        return self.toolbarDefaultItemIdentifiers_(toolbar)

    def toolbar_itemForItemIdentifier_willBeInsertedIntoToolbar_(self, toolbar, identifier, flag):
        if identifier != TABS_ITEM_ID:
            return None
        seg = AppKit.NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            [label for _, label in TABS], AppKit.NSSegmentSwitchTrackingSelectOne, self, "tabChanged:")
        seg.setSegmentDistribution_(AppKit.NSSegmentDistributionFillEqually)
        seg.setSelectedSegment_(0)
        seg.setToolTip_("Switch tool (⌘1, ⌘2, ⌘3)")
        seg.sizeToFit()
        frame = seg.frame()
        frame.size.width = max(frame.size.width, 100 * len(TABS))
        seg.setFrame_(frame)
        self.segmented = seg

        item = AppKit.NSToolbarItem.alloc().initWithItemIdentifier_(TABS_ITEM_ID)
        item.setView_(seg)
        item.setLabel_("Tool")
        return item

    # -- actions -----------------------------------------------------------
    def tabChanged_(self, sender):
        name = TABS[sender.selectedSegment()][0]
        js = f"window.switchTab && window.switchTab({json.dumps(name)}, {{fromNative: true}})"
        threading.Thread(target=lambda: self.pywindow.evaluate_js(js), daemon=True).start()

    def selectTab_(self, name):
        if self.segmented is None:
            return
        for i, (tab, _) in enumerate(TABS):
            if tab == name:
                self.segmented.setSelectedSegment_(i)


class NativeBridge:
    """Exposed to the page as window.pywebview.api (only public methods are visible)."""

    def __init__(self) -> None:
        self._controller: ToolbarController | None = None

    def tab_changed(self, name: str) -> None:
        if self._controller is not None:
            AppHelper.callAfter(self._controller.selectTab_, name)

    def reveal(self, path: str) -> None:
        """Show a saved file in Finder."""
        url = AppKit.NSURL.fileURLWithPath_(str(path))
        AppHelper.callAfter(
            lambda: AppKit.NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_([url]))


def install(pywindow, bridge: NativeBridge) -> None:
    """Give the window a unified toolbar before it is first shown."""

    def before_show(window):
        ns = window.native
        controller = ToolbarController.alloc().initWithPyWindow_(window)
        toolbar = AppKit.NSToolbar.alloc().initWithIdentifier_(TOOLBAR_ID)
        toolbar.setDelegate_(controller)
        toolbar.setDisplayMode_(AppKit.NSToolbarDisplayModeIconOnly)
        toolbar.setAllowsUserCustomization_(False)
        if toolbar.respondsToSelector_("setCenteredItemIdentifiers:"):
            toolbar.setCenteredItemIdentifiers_(AppKit.NSSet.setWithObject_(TABS_ITEM_ID))
        ns.setToolbar_(toolbar)
        ns.setToolbarStyle_(AppKit.NSWindowToolbarStyleUnified)
        ns.setTitleVisibility_(AppKit.NSWindowTitleVisible)
        ns.setTabbingMode_(AppKit.NSWindowTabbingModeDisallowed)
        bridge._controller = controller
        _keep_alive.extend([controller, toolbar])

    pywindow.events.before_show += before_show
