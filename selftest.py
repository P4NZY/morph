"""End-to-end self-test of the Morph window (used only when MORPH_SELFTEST is set).

Drives the real app window: converts a Markdown file to Word, saves it through
the native Save dialog, runs an AI upscale, then writes selftest.json and quits.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

JS_CONVERT = r"""
window.__t = {stage: 'convert'};
(async () => {
  try {
    const f = new File(["# Self-test\n\nHello **world**.\n"], "selftest notes.md", {type: "text/markdown"});
    const dt = new DataTransfer(); dt.items.add(f);
    window.switchTab('convert');
    document.querySelector('#c-drop').dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true}));
    await new Promise(r => setTimeout(r, 200));
    const fmt = document.querySelector('#c-format'); fmt.value = 'docx'; fmt.dispatchEvent(new Event('change'));
    document.querySelector('#c-go').click();
    for (let i = 0; i < 200 && document.querySelector('#c-result').classList.contains('is-hidden'); i++)
      await new Promise(r => setTimeout(r, 100));
    window.__t.convert = {
      name: document.querySelector('#c-result-name').textContent,
      meta: document.querySelector('#c-result-meta').textContent,
      status: document.querySelector('#c-status').textContent,
      button: document.querySelector('#c-download').textContent,
      desktop: document.documentElement.classList.contains('desktop'),
    };
    window.__t.stage = 'converted';
  } catch (e) { window.__t.error = String(e); window.__t.stage = 'failed'; }
})();
'started'
"""

JS_UPSCALE = r"""
window.__t.stage = 'upscale';
(async () => {
  try {
    window.switchTab('upscale');
    const c = document.createElement('canvas'); c.width = 240; c.height = 160;
    const g = c.getContext('2d'); g.fillStyle = '#0f766e'; g.fillRect(0, 0, 240, 160);
    g.fillStyle = '#fbbf24'; g.beginPath(); g.arc(120, 80, 50, 0, 7); g.fill();
    const blob = await new Promise(r => c.toBlob(r, 'image/png'));
    const dt = new DataTransfer(); dt.items.add(new File([blob], 'selftest.png', {type: 'image/png'}));
    document.querySelector('#u-drop').dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true}));
    await new Promise(r => setTimeout(r, 600));
    const m = document.querySelector('#u-method'); m.value = 'photo'; m.dispatchEvent(new Event('change'));
    const p = document.querySelector('#u-preset'); p.value = 'x2'; p.dispatchEvent(new Event('change'));
    document.querySelector('#u-go').click();
    for (let i = 0; i < 600 && document.querySelector('#u-result').classList.contains('is-hidden'); i++) {
      if (document.querySelector('#u-status').classList.contains('is-error')) break;
      await new Promise(r => setTimeout(r, 100));
    }
    document.querySelector('#u-view [data-view="compare"]').click();
    await new Promise(r => setTimeout(r, 300));
    window.__t.upscale = {
      name: document.querySelector('#u-result-name').textContent,
      meta: document.querySelector('#u-result-meta').textContent,
      status: document.querySelector('#u-status').textContent,
      compareImage: document.querySelector('#u-after').naturalWidth,
      device: (typeof app !== 'undefined' && app.ai) ? app.ai.device : null,
    };
    window.__t.stage = 'done';
  } catch (e) { window.__t.error = String(e); window.__t.stage = 'failed'; }
})();
'started'
"""


JS_COMPRESS = r"""
window.__t.stage = 'compress';
(async () => {
  try {
    window.switchTab('compress');
    const c = document.createElement('canvas'); c.width = 1600; c.height = 1000;
    const g = c.getContext('2d');
    for (let i = 0; i < 400; i++) { g.fillStyle = `hsl(${i * 37 % 360},60%,${30 + i % 40}%)`; g.fillRect((i * 97) % 1600, (i * 53) % 1000, 120, 90); }
    const blob = await new Promise(r => c.toBlob(r, 'image/png'));
    const dt = new DataTransfer(); dt.items.add(new File([blob], 'selftest-big.png', {type: 'image/png'}));
    document.querySelector('#k-drop').dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true}));
    await new Promise(r => setTimeout(r, 500));
    const f = document.querySelector('#k-format'); f.value = 'webp'; f.dispatchEvent(new Event('change'));
    document.querySelector('#k-go').click();
    for (let i = 0; i < 300 && document.querySelector('#k-result').classList.contains('is-hidden'); i++) {
      if (document.querySelector('#k-activity').classList.contains('is-error')) break;
      await new Promise(r => setTimeout(r, 100));
    }
    window.__t.compress = {
      name: document.querySelector('#k-result-name').textContent,
      meta: document.querySelector('#k-result-meta').textContent,
      saving: document.querySelector('#k-saving').textContent,
      status: document.querySelector('#k-status').textContent,
      button: document.querySelector('#k-download').textContent,
    };
    window.__t.stage = 'compressed';
  } catch (e) { window.__t.error = String(e); window.__t.stage = 'failed'; }
})();
'started'
"""


def _wait_stage(window, stages, timeout):
    end = time.time() + timeout
    while time.time() < end:
        state = window.evaluate_js("JSON.stringify(window.__t || {})")
        state = json.loads(state or "{}")
        if state.get("stage") in stages:
            return state
        time.sleep(0.25)
    return json.loads(window.evaluate_js("JSON.stringify(window.__t || {})") or "{}")


def run(window, outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    saved_dir = outdir / "saved"
    saved_dir.mkdir(exist_ok=True)
    report: dict = {}
    try:
        window.events.loaded.wait(30)
        time.sleep(1.0)
        report["title"] = window.evaluate_js("document.title")
        report["pywebview"] = window.evaluate_js("!!window.pywebview")

        window.evaluate_js(JS_CONVERT)
        report["convert_state"] = _wait_stage(window, {"converted", "failed"}, 60)

        # desktop.py saves straight into <outdir>/saved in self-test mode (no dialog).
        window.evaluate_js("document.querySelector('#c-download').click(); 'clicked'")
        for _ in range(50):
            files = [p for p in saved_dir.iterdir() if p.suffix == ".docx"]
            if files and files[0].stat().st_size > 0:
                break
            time.sleep(0.2)
        report["saved_files"] = {p.name: p.stat().st_size for p in saved_dir.iterdir()}
        time.sleep(0.5)
        report["saved_note"] = window.evaluate_js(
            "document.querySelector('#c-result-meta').textContent + ' | reveal shown: ' + "
            "!document.querySelector('#c-reveal').classList.contains('is-hidden')")

        window.evaluate_js(JS_UPSCALE)
        report["upscale_state"] = _wait_stage(window, {"done", "failed"}, 120)

        window.evaluate_js(JS_COMPRESS)
        report["compress_state"] = _wait_stage(window, {"compressed", "failed"}, 90)
    except Exception:
        report["exception"] = traceback.format_exc()
    finally:
        (outdir / "selftest.json").write_text(json.dumps(report, indent=2))
        window.destroy()
