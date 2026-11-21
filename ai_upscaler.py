"""AI upscaling with Real-ESRGAN, run locally (Apple GPU via Metal when available).

Model weights are downloaded once, on request, from the official Real-ESRGAN
GitHub releases into ~/Library/Application Support/Morph/models (shared by the
Morph.app and the start.command version). They're loaded with spandrel, which
unpickles .pth files in a restricted mode (tensors only, no arbitrary code).
"""

from __future__ import annotations

import hashlib
import os
import ssl
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable

import certifi
import numpy as np
from PIL import Image

from converters import ConversionError

MODELS_DIR = Path(os.environ.get("MORPH_MODELS_DIR")
                  or Path.home() / "Library" / "Application Support" / "Morph" / "models")

MODELS = {
    "photo": {
        "label": "AI · Photo",
        "name": "Real-ESRGAN x4plus",
        "file": "RealESRGAN_x4plus.pth",
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
        "size": 67_040_989,
        "sha256": "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1",
        "about": "Best for photos and general images. Very small text can come out garbled.",
    },
    "art": {
        "label": "AI · Art",
        "name": "Real-ESRGAN x4plus anime",
        "file": "RealESRGAN_x4plus_anime_6B.pth",
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth",
        "size": 17_938_799,
        "sha256": "f872d837d3c90ed2e05227bed711af5671a6fd1c9f7d7e91c911a61f155e99da",
        "about": "Best for drawings, anime, logos and flat-colour art.",
    },
}

MODEL_SCALE = 4
AI_MAX_INPUT_PIXELS = 8_000_000  # ~8 MP in; the 4x intermediate is then ≤ 128 MP
TILE_PAD = 48  # overlap between tiles, in input pixels (hides seams)

Report = Callable[[float, str], None]

_infer_lock = threading.Lock()   # one inference at a time (GPU memory)
_load_lock = threading.Lock()
_download_lock = threading.Lock()
_loaded: dict[str, object] = {}
_torch_ok: bool | None = None


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------

_torch_error: str | None = None


def torch_available() -> bool:
    global _torch_ok, _torch_error
    if _torch_ok is None:
        try:
            import torch  # noqa: F401
            import spandrel  # noqa: F401
            _torch_ok = True
        except Exception as exc:
            import traceback

            traceback.print_exc()
            _torch_ok, _torch_error = False, f"{type(exc).__name__}: {exc}"
    return _torch_ok


def _device():
    import torch

    return torch.device("mps") if torch.backends.mps.is_available() else torch.device("cpu")


def device_label() -> str:
    if not torch_available():
        return "unavailable"
    return "Apple GPU (Metal)" if _device().type == "mps" else "CPU"


def model_path(model_id: str) -> Path:
    return MODELS_DIR / MODELS[model_id]["file"]


def is_installed(model_id: str) -> bool:
    p = model_path(model_id)
    return p.exists() and p.stat().st_size == MODELS[model_id]["size"]


def status() -> dict:
    return {
        "available": torch_available(),
        "error": _torch_error,
        "device": device_label(),
        "maxInputPixels": AI_MAX_INPUT_PIXELS,
        "models": {
            mid: {
                "label": m["label"], "name": m["name"], "file": m["file"],
                "url": m["url"], "size": m["size"], "about": m["about"],
                "installed": is_installed(mid),
            }
            for mid, m in MODELS.items()
        },
    }


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

def download(model_id: str, report: Report) -> None:
    if model_id not in MODELS:
        raise ConversionError("Unknown model.")
    info = MODELS[model_id]
    with _download_lock:
        if is_installed(model_id):
            report(1.0, "Already installed")
            return
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        part = model_path(model_id).with_suffix(".part")
        sha = hashlib.sha256()
        got = 0
        try:
            req = urllib.request.Request(info["url"], headers={"User-Agent": "Morph-local-upscaler"})
            # python.org builds of Python on macOS don't use the system certificates.
            ctx = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp, open(part, "wb") as fh:
                while chunk := resp.read(1 << 18):
                    fh.write(chunk)
                    sha.update(chunk)
                    got += len(chunk)
                    report(min(got / info["size"], 0.999),
                           f"Downloading… {got / 1e6:.0f} of {info['size'] / 1e6:.0f} MB")
        except OSError as exc:
            part.unlink(missing_ok=True)
            raise ConversionError(f"Download failed ({exc}). Check your internet connection.")

        if got != info["size"] or (info["sha256"] and sha.hexdigest() != info["sha256"]):
            part.unlink(missing_ok=True)
            raise ConversionError("The downloaded model didn't match the expected file, so it was discarded.")
        part.rename(model_path(model_id))
        report(1.0, "Installed")


# --------------------------------------------------------------------------
# Inference
# --------------------------------------------------------------------------

def _get_model(model_id: str):
    with _load_lock:
        if model_id in _loaded:
            return _loaded[model_id]
        if not is_installed(model_id):
            raise ConversionError("This AI model isn't downloaded yet.")
        from spandrel import ImageModelDescriptor, ModelLoader

        desc = ModelLoader().load_from_file(str(model_path(model_id)))
        if not isinstance(desc, ImageModelDescriptor) or desc.scale != MODEL_SCALE:
            raise ConversionError("The model file isn't a 4× image upscaler.")
        desc.eval()
        device = _device()
        desc.to(device)
        # Half precision is ~1.6× faster on Apple GPUs with no visible difference.
        if device.type == "mps" and desc.supports_half:
            desc.half()
        _loaded[model_id] = desc
        return desc


def _model_dtype(desc):
    return next(desc.model.parameters()).dtype


def enhance(img: Image.Image, model_id: str, report: Report, tile: int | None = None) -> Image.Image:
    """Return the image upscaled 4× by the AI model (RGB, or RGBA if it had alpha)."""
    if not torch_available():
        raise ConversionError("AI upscaling needs PyTorch, which isn't installed.")
    if img.width * img.height > AI_MAX_INPUT_PIXELS:
        raise ConversionError(
            f"AI upscaling works on images up to {AI_MAX_INPUT_PIXELS / 1e6:.0f} MP; this one is "
            f"{img.width * img.height / 1e6:.1f} MP. Use Standard instead."
        )
    import torch

    alpha = img.getchannel("A") if img.mode in ("RGBA", "LA") else None
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    h, w = rgb.shape[:2]
    s = MODEL_SCALE

    if not _infer_lock.acquire(blocking=False):
        report(0.0, "Waiting for another upscale to finish…")
        _infer_lock.acquire()
    try:
        report(0.0, "Loading AI model…")
        model = _get_model(model_id)
        device = _device()
        dtype = _model_dtype(model)

        tile = tile or (512 if device.type == "mps" else 256)
        pad = TILE_PAD
        tiles = [(x, y) for y in range(0, h, tile) for x in range(0, w, tile)]
        out = np.empty((h * s, w * s, 3), dtype=np.uint8)
        src = torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0)  # 1×3×H×W

        started = time.time()
        with torch.inference_mode():
            for i, (x0, y0) in enumerate(tiles):
                x1, y1 = min(x0 + tile, w), min(y0 + tile, h)
                px0, py0 = max(x0 - pad, 0), max(y0 - pad, 0)
                px1, py1 = min(x1 + pad, w), min(y1 + pad, h)
                patch = src[:, :, py0:py1, px0:px1].to(device=device, dtype=dtype)
                res = model(patch)
                ox0, oy0 = (x0 - px0) * s, (y0 - py0) * s
                core = res[0, :, oy0:oy0 + (y1 - y0) * s, ox0:ox0 + (x1 - x0) * s].float()
                core = (core.clamp(0, 1) * 255.0 + 0.5).to(torch.uint8).permute(1, 2, 0).cpu().numpy()
                out[y0 * s:y1 * s, x0 * s:x1 * s] = core

                done = (i + 1) / len(tiles)
                elapsed = time.time() - started
                eta = elapsed / done - elapsed
                report(done, f"Enhancing with AI… {round(done * 100)}%"
                       + (f" · about {eta:.0f}s left" if i and eta >= 1 else ""))
        if device.type == "mps":
            torch.mps.empty_cache()
    finally:
        _infer_lock.release()

    result = Image.fromarray(out, "RGB")
    if alpha is not None:
        result.putalpha(alpha.resize(result.size, Image.LANCZOS))
    return result
