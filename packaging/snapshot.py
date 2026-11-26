"""Dev tool: screenshot the real Morph window in different states (light + dark).

Usage:  .venv/bin/python packaging/snapshot.py <out_dir> [image_for_upscale]
Writes PNGs like convert-empty-light.png into <out_dir>.
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import AppKit  # noqa: E402
import Foundation  # noqa: E402
import Quartz  # noqa: E402
from PyObjCTools import AppHelper  # noqa: E402

import desktop  # noqa: E402


def set_appearance(dark: bool) -> None:
    name = AppKit.NSAppearanceNameDarkAqua if dark else AppKit.NSAppearanceNameAqua
    AppHelper.callAfter(lambda: AppKit.NSApplication.sharedApplication().setAppearance_(
        AppKit.NSAppearance.appearanceNamed_(name)))
    time.sleep(0.6)


def capture(window, path: Path) -> None:
    time.sleep(0.35)
    wid = window.native.windowNumber()
    img = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull, Quartz.kCGWindowListOptionIncludingWindow, wid,
        Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageBestResolution)
    if img is None:
        raise RuntimeError("window capture failed (screen recording permission?)")
    url = Foundation.NSURL.fileURLWithPath_(str(path))
    dest = Quartz.CGImageDestinationCreateWithURL(url, "public.png", 1, None)
    Quartz.CGImageDestinationAddImage(dest, img, None)
    Quartz.CGImageDestinationFinalize(dest)
    print("captured", path.name, flush=True)


def js(window, code: str):
    return window.evaluate_js(code)


def wait_js(window, cond: str, timeout: float = 60) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if js(window, f"!!({cond})"):
            return True
        time.sleep(0.15)
    return False


def drop(window, selector: str, name: str, mime: str, b64: str) -> None:
    js(window, f"""
      (() => {{
        const bin = atob({json.dumps(b64)}); const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const dt = new DataTransfer(); dt.items.add(new File([bytes], {json.dumps(name)}, {{type: {json.dumps(mime)}}}));
        document.querySelector({json.dumps(selector)}).dispatchEvent(new DragEvent('drop', {{dataTransfer: dt, bubbles: true, cancelable: true}}));
      }})(); 'ok'""")


def keep_rendering(window) -> None:
    """WebKit stops painting windows hidden behind others; keep it drawing for captures."""
    def apply():
        view = window.native.contentView()
        for v in [view, *view.subviews()]:
            if v.respondsToSelector_("_setWindowOcclusionDetectionEnabled:"):
                v._setWindowOcclusionDetectionEnabled_(False)
    AppHelper.callAfter(apply)


def driver(window, out: Path, image: Path | None) -> None:
    try:
        keep_rendering(window)
        window.events.loaded.wait(30)
        wait_js(window, "document.querySelector('.matrix-grid, #c-matrix *')", 20)
        time.sleep(0.8)
        md = base64.b64encode(b"# Quarterly report\n\nSome **bold** text.\n").decode()
        img_b64 = base64.b64encode(image.read_bytes()).decode() if image else None

        for dark in (False, True):
            mode = "dark" if dark else "light"
            set_appearance(dark)
            js(window, "location.hash=''; location.reload(); 'ok'")
            time.sleep(1.5)
            wait_js(window, "document.readyState === 'complete'", 20)
            time.sleep(0.8)
            js(window, "window.switchTab('convert'); 'ok'")
            capture(window, out / f"convert-empty-{mode}.png")

            drop(window, "#c-drop", "Quarterly report.md", "text/markdown", md)
            time.sleep(0.6)
            capture(window, out / f"convert-file-{mode}.png")

            js(window, "document.querySelector('#c-go').click(); 'ok'")
            wait_js(window, "!document.querySelector('#c-result').classList.contains('is-hidden')", 30)
            capture(window, out / f"convert-done-{mode}.png")

            js(window, "window.switchTab('compress'); 'ok'")
            time.sleep(0.4)
            capture(window, out / f"compress-empty-{mode}.png")
            if img_b64:
                drop(window, "#k-drop", image.name, "image/jpeg", img_b64)
                time.sleep(0.8)
                capture(window, out / f"compress-file-{mode}.png")
                js(window, "document.querySelector('#k-go').click(); 'ok'")
                wait_js(window, "!document.querySelector('#k-result').classList.contains('is-hidden') || "
                                "document.querySelector('#k-status').textContent.length > 20", 60)
                capture(window, out / f"compress-done-{mode}.png")

            js(window, "window.switchTab('upscale'); 'ok'")
            time.sleep(0.4)
            capture(window, out / f"upscale-empty-{mode}.png")

            if img_b64:
                drop(window, "#u-drop", image.name, "image/jpeg", img_b64)
                time.sleep(1.2)
                js(window, """(() => {
                  for (const [id, v] of [['#u-method', 'photo'], ['#u-preset', '4k']]) {
                    const s = document.querySelector(id); s.value = v; s.dispatchEvent(new Event('change')); }
                })(); 'ok'""")
                time.sleep(0.5)
                capture(window, out / f"upscale-ready-{mode}.png")
                js(window, "document.querySelector('#u-go').click(); 'ok'")
                wait_js(window, "!document.querySelector('#u-result').classList.contains('is-hidden')", 120)
                js(window, "document.querySelector('#u-view [data-view=\"compare\"]').click(); 'ok'")
                time.sleep(0.8)
                capture(window, out / f"upscale-compare-{mode}.png")

                # extra states: model not downloaded + size warning, drag-over, error message
                js(window, """(() => { app.ai.models.art.installed = false;
                  const m = document.querySelector('#u-method'); m.value = 'art'; m.dispatchEvent(new Event('change'));
                  const s = document.querySelector('#u-slider'); s.value = 300; s.dispatchEvent(new Event('input'));
                })(); 'ok'""")
                time.sleep(0.4)
                capture(window, out / f"upscale-model-{mode}.png")
                js(window, "document.body.classList.add('is-dragging'); 'ok'")
                capture(window, out / f"upscale-dragging-{mode}.png")
                js(window, "document.body.classList.remove('is-dragging'); window.switchTab('convert');"
                           "setActivity(C, 'Couldn’t read that image — it may be damaged or not really an image.', undefined, true); 'ok'")
                capture(window, out / f"convert-error-{mode}.png")
    except Exception as exc:  # pragma: no cover - dev tool
        print("snapshot failed:", exc, flush=True)
    finally:
        window.destroy()


if __name__ == "__main__":
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    img = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    desktop.run_window(driver, (out_dir, img))
