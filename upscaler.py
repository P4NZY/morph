"""Image upscaling: by a scale factor, or to a target resolution."""

from __future__ import annotations

import io
import math
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from converters import ConversionError, flatten_alpha, has_alpha, open_image

MAX_OUTPUT_PIXELS = 120_000_000  # ~120 MP; beyond this things get slow and memory-hungry

PRESETS = [
    {"id": "x2", "label": "200%", "scale": 2.0},
    {"id": "x4", "label": "400%", "scale": 4.0},
    {"id": "4k", "label": "4K UHD", "width": 3840, "height": 2160},
    {"id": "mba13", "label": "MacBook Air 13″ (M5)", "width": 2560, "height": 1664},
    {"id": "mba15", "label": "MacBook Air 15″ (M5)", "width": 2880, "height": 1864},
]


def plan(w: int, h: int, *, scale: float | None = None, width: int | None = None,
         height: int | None = None, fit: str = "fill") -> dict:
    """Work out the resize size, optional center-crop, and final output size."""
    if scale is not None:
        s = float(scale)
        rw, rh = max(1, round(w * s)), max(1, round(h * s))
        return {"resize": (rw, rh), "crop": None, "final": (rw, rh), "scale": s}

    if not width or not height:
        raise ConversionError("Pick a scale or a target resolution.")
    tw, th = int(width), int(height)

    if fit == "stretch":
        return {"resize": (tw, th), "crop": None, "final": (tw, th), "scale": max(tw / w, th / h)}
    if fit == "fit":
        s = min(tw / w, th / h)
        rw, rh = max(1, round(w * s)), max(1, round(h * s))
        return {"resize": (rw, rh), "crop": None, "final": (rw, rh), "scale": s}

    # fill: cover the target, then trim the overflow evenly from both sides
    s = max(tw / w, th / h)
    rw, rh = max(tw, math.ceil(w * s)), max(th, math.ceil(h * s))
    left, top = (rw - tw) // 2, (rh - th) // 2
    return {"resize": (rw, rh), "crop": (left, top, left + tw, top + th), "final": (tw, th), "scale": s}


def _sharpen(img: Image.Image, scale: float) -> Image.Image:
    if scale <= 1.05:
        return img
    radius = min(3.0, 0.8 + 0.35 * (scale - 1))
    usm = ImageFilter.UnsharpMask(radius=radius, percent=70, threshold=2)
    if img.mode == "RGBA":
        rgb, alpha = img.convert("RGB"), img.getchannel("A")
        rgb = rgb.filter(usm)
        rgb.putalpha(alpha)
        return rgb
    return img.filter(usm)


def upscale(src, *, out_format: str = "png", sharpen: bool = True, method: str = "standard",
            report=None, **sizing) -> tuple[bytes, dict]:
    """Upscale ``src`` (a path or file-like). ``method`` is "standard" or an AI model id."""
    report = report or (lambda _p, _m: None)
    img = open_image(src)
    img = ImageOps.exif_transpose(img)
    icc = img.info.get("icc_profile")

    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGBA" if has_alpha(img) else "RGB")

    p = plan(img.width, img.height, **sizing)
    fw, fh = p["final"]
    rw, rh = p["resize"]
    if rw * rh > MAX_OUTPUT_PIXELS:
        raise ConversionError(
            f"That would be {rw:,} × {rh:,} pixels — too big. Try a smaller scale."
        )

    use_ai = method != "standard" and p["scale"] > 1.0
    if use_ai:
        import ai_upscaler

        if method not in ai_upscaler.MODELS:
            raise ConversionError("Unknown upscaling method.")
        enhanced = ai_upscaler.enhance(img, method, lambda f, m: report(f * 0.92, m))
        report(0.94, "Resizing to the exact size…")
        # The model always gives 4×; Lanczos takes it the rest of the way (down or up).
        result = enhanced if enhanced.size == (rw, rh) else enhanced.resize((rw, rh), Image.LANCZOS)
        if result.mode != img.mode and img.mode == "L":
            result = result.convert("L")
    else:
        report(0.2, "Upscaling…")
        result = img.resize((rw, rh), Image.LANCZOS)

    if p["crop"]:
        result = result.crop(p["crop"])
    if sharpen and not use_ai:
        result = _sharpen(result, p["scale"])
    report(0.97, "Saving…")

    buf = io.BytesIO()
    fmt = out_format.lower()
    kwargs: dict = {}
    if icc:
        kwargs["icc_profile"] = icc
    if fmt == "jpg":
        result = flatten_alpha(result)
        result.save(buf, "JPEG", quality=95, subsampling=0, optimize=True, **kwargs)
    elif fmt == "webp":
        result.save(buf, "WEBP", quality=95, method=4, **kwargs)
    else:
        fmt = "png"
        result.save(buf, "PNG", compress_level=6, **kwargs)

    info = {
        "original": (img.width, img.height),
        "output": (fw, fh),
        "scale": p["scale"],
        "format": fmt,
        "method": method if use_ai else "standard",
    }
    return buf.getvalue(), info


def preview(src: Path, max_side: int = 1600) -> tuple[bytes, tuple[int, int]]:
    """A browser-friendly JPEG preview (for formats like HEIC/TIFF) plus the real size."""
    img = ImageOps.exif_transpose(open_image(src))
    size = img.size
    img = flatten_alpha(img)
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue(), size
