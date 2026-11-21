"""Morph as a Mac app: the converter in its own window, no browser needed.

This is the entry point PyInstaller builds into Morph.app. It starts the
converter's web server on localhost in the background and shows it in a native
window (WebKit, via pywebview). Closing the window quits everything.

Environment switches (for testing the built app):
  MORPH_HEADLESS=<port>   run only the server on that port, no window
  MORPH_SELFTEST=<dir>    open the window, drive a conversion + save, write
                          results to <dir>/selftest.json, then quit
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from werkzeug.serving import make_server

from app import app

SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "Morph"

_window = None  # the app window, for "saved" notifications


def _call_page(func: str, *args) -> None:
    """Call a page function (from a worker thread, so the UI thread never blocks)."""
    import json

    if _window is not None:
        js = f"window.{func} && window.{func}({', '.join(json.dumps(a) for a in args)})"
        threading.Thread(target=lambda: _window.evaluate_js(js), daemon=True).start()


def install_download_handler(auto_save_dir: Path | None = None) -> None:
    """Handle Save… clicks: native Save dialog, replacing existing files, "Saved to …" in the page.

    Replaces pywebview's handler, which fails silently when you choose to
    replace an existing file. With ``auto_save_dir`` (self-test) files are saved
    there directly, without showing the dialog.
    """
    import AppKit
    import Foundation
    from webview.platforms import cocoa

    class MorphDownloadDelegate(AppKit.NSObject):
        def download_decideDestinationUsingResponse_suggestedFilename_completionHandler_(
            self, download, response, suggested, completion
        ):
            if auto_save_dir is not None:
                target = auto_save_dir / str(suggested)
            else:
                panel = AppKit.NSSavePanel.savePanel()
                downloads = Path.home() / "Downloads"
                panel.setDirectoryURL_(Foundation.NSURL.fileURLWithPath_(str(downloads)))
                panel.setNameFieldStringValue_(suggested)
                panel.setCanCreateDirectories_(True)
                if panel.runModal() != AppKit.NSModalResponseOK or panel.URL() is None:
                    completion(None)
                    return
                target = Path(str(panel.URL().path()))
            # The dialog already asked "Replace?"; WebKit needs the old file gone.
            if target.exists():
                try:
                    target.unlink()
                except OSError:
                    _call_page("hud", f"Couldn't replace “{target.name}”.")
                    completion(None)
                    return
            self.target = target
            completion(Foundation.NSURL.fileURLWithPath_(str(target)))

        def downloadDidFinish_(self, download):
            target = getattr(self, "target", None)
            if target is not None:
                folder = Foundation.NSFileManager.defaultManager().displayNameAtPath_(str(target.parent))
                _call_page("morphSaved", str(target), str(folder))

        def download_didFailWithError_resumeData_(self, download, error, resume_data):
            _call_page("hud", f"The file couldn't be saved. {error.localizedDescription()}")

    cocoa.BrowserView.DownloadDelegate = MorphDownloadDelegate


def start_server(port: int = 8765):
    """Serve the app on localhost; fall back to any free port if 8765 is taken."""
    try:
        server = make_server("127.0.0.1", port, app, threaded=True)
    except OSError:
        server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def dark_mode() -> bool:
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                             capture_output=True, text=True, timeout=2).stdout
        return "Dark" in out
    except Exception:
        return False


def run_window(driver=None, driver_args: tuple = (), auto_save: Path | None = None) -> None:
    """Open the Morph window. ``driver(window, *driver_args)`` runs in a thread (tests)."""
    global _window
    import webview

    import mac_window

    server = start_server()
    url = f"http://127.0.0.1:{server.server_port}/"

    webview.settings["ALLOW_DOWNLOADS"] = True  # Save… buttons open a native Save dialog
    install_download_handler(auto_save)
    bridge = mac_window.NativeBridge()
    window = _window = webview.create_window(
        "Morph", url, js_api=bridge, width=1120, height=780, min_size=(760, 560),
        background_color="#1e1e1e" if dark_mode() else "#ffffff",
    )
    mac_window.install(window, bridge)

    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    args = (window, *driver_args) if driver else None
    webview.start(driver, args, private_mode=False, storage_path=str(SUPPORT_DIR / "webview"))

    server.shutdown()
    os._exit(0)  # stop any background jobs too


def main() -> None:
    headless = os.environ.get("MORPH_HEADLESS")
    if headless:
        server = start_server(int(headless))
        print(f"Morph server (headless) on http://127.0.0.1:{server.server_port}", flush=True)
        threading.Event().wait()
        return

    selftest_dir = os.environ.get("MORPH_SELFTEST")
    if selftest_dir:
        from selftest import run

        auto_save = Path(selftest_dir) / "saved"
        auto_save.mkdir(parents=True, exist_ok=True)
        run_window(run, (Path(selftest_dir),), auto_save)
    else:
        run_window()


if __name__ == "__main__":
    # Needed so torch/multiprocessing helpers don't relaunch the whole app when frozen.
    import multiprocessing

    multiprocessing.freeze_support()
    main()
