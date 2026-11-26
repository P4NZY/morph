"""Compression engine: make files smaller while keeping them usable.

* images            -> re-encode (optionally in a more efficient format), shrink, strip metadata
* PDF               -> resample + recompress embedded images, drop unused objects
* video             -> H.264 at a lower quality and/or resolution (ffmpeg, with progress)
* audio             -> lower bitrate; uncompressed audio becomes AAC, MP3 or FLAC
* Office / EPUB     -> shrink the pictures inside the document
* anything else     -> ZIP archive (lossless); several files always become one ZIP
"""

from __future__ import annotations

import re
import subprocess
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable

import pymupdf
from PIL import Image, ImageOps, ImageSequence

from converters import FFMPEG, ConversionError, flatten_alpha, has_alpha, normalize_ext, open_image

Report = Callable[[float, str], None]
LEVELS = ("small", "balanced", "high")

# input format -> output formats offered (the first one is "same as original")
IMAGE_FORMATS = {
    "jpg": ["jpg", "webp"],
    "png": ["png", "webp", "jpg"],
    "webp": ["webp", "jpg"],
    "heic": ["heic", "jpg", "webp"],
    "avif": ["avif", "jpg", "webp"],
    "gif": ["gif", "webp"],
    "tiff": ["tiff", "jpg", "webp", "png"],
    "bmp": ["png", "jpg", "webp"],
}
VIDEO_IN = ["mp4", "mov", "m4v", "mkv", "webm", "avi", "wmv", "flv", "mpeg", "3gp"]
AUDIO_LOSSY = {"mp3": "mp3", "m4a": "m4a", "aac": "m4a", "ogg": "ogg", "opus": "opus", "wma": "mp3"}
AUDIO_LOSSLESS = {"wav": ["m4a", "mp3", "flac"], "aiff": ["m4a", "mp3", "flac"], "flac": ["m4a", "mp3", "flac"]}
OFFICE_IN = ["docx", "pptx", "xlsx", "odt", "odp", "ods", "epub"]

JPEG_Q = {"small": 55, "balanced": 72, "high": 85}
WEBP_Q = {"small": 50, "balanced": 70, "high": 82}
AVIF_Q = {"small": 45, "balanced": 60, "high": 75}
HEIC_Q = {"small": 50, "balanced": 65, "high": 80}
GIF_COLORS = {"small": 64, "balanced": 128, "high": 256}
PNG_COLORS = {"small": 128, "balanced": 256, "high": 0}  # 0 = lossless only
PDF_IMAGES = {"small": (110, 96, 50), "balanced": (165, 150, 70), "high": (240, 220, 85)}  # dpi above, target dpi, JPEG q
VIDEO_CRF = {"small": 30, "balanced": 26, "high": 22}
VIDEO_AUDIO = {"small": "96k", "balanced": "128k", "high": "160k"}
AUDIO_BITRATE = {"small": "96k", "balanced": "128k", "high": "192k"}
OPUS_BITRATE = {"small": "64k", "balanced": "96k", "high": "128k"}
OFFICE_MAX_SIDE = {"small": 1280, "balanced": 1920, "high": 2560}


@dataclass
class Settings:
    level: str = "balanced"
    format: str = "same"        # images / lossless audio
    max_side: int = 0           # images: longest side in pixels, 0 = keep
    resolution: int = 0         # video: short side (1080, 720, 480), 0 = keep
    strip_metadata: bool = True

    @classmethod
    def from_form(cls, form) -> "Settings":
        try:
            s = cls(
                level=form.get("level", "balanced"),
                format=form.get("format", "same"),
                max_side=int(form.get("max_side", 0) or 0),
                resolution=int(form.get("resolution", 0) or 0),
                strip_metadata=form.get("strip_metadata", "1") == "1",
            )
        except ValueError:
            raise ConversionError("Invalid compression settings.")
        if s.level not in LEVELS:
            raise ConversionError("Unknown quality level.")
        return s


def kind_of(ext: str) -> str:
    ext = normalize_ext(ext)
    if ext in IMAGE_FORMATS:
        return "image"
    if ext == "pdf":
        return "pdf"
    if ext in VIDEO_IN:
        return "video"
    if ext in AUDIO_LOSSY or ext in AUDIO_LOSSLESS:
        return "audio"
    if ext in OFFICE_IN:
        return "office"
    return "archive"


def client_info() -> dict:
    return {
        "imageFormats": IMAGE_FORMATS,
        "videoIn": VIDEO_IN,
        "audioLossy": AUDIO_LOSSY,
        "audioLossless": AUDIO_LOSSLESS,
        "officeIn": OFFICE_IN,
    }


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def compress(files: list[tuple[Path, str]], s: Settings, outdir: Path, report: Report) -> tuple[Path, dict]:
    """Compress ``files`` (path on disk, original filename). Returns (output path, info)."""
    if not files:
        raise ConversionError("No file was uploaded.")
    outdir.mkdir(parents=True, exist_ok=True)
    original = sum(p.stat().st_size for p, _ in files)

    if len(files) > 1:
        out = _zip(files, outdir / "Archive.zip", report)
        kind = "archive"
    else:
        src, name = files[0]
        ext = normalize_ext(Path(name).suffix)
        stem = Path(name).stem or "file"
        kind = kind_of(ext)
        if kind == "image":
            out = _image(src, ext, outdir, stem, s, report)
        elif kind == "pdf":
            out = _pdf(src, outdir / f"{stem}-compressed.pdf", s, report)
        elif kind == "video":
            out = _video(src, ext, outdir, stem, s, report)
        elif kind == "audio":
            out = _audio(src, ext, outdir, stem, s, report)
        elif kind == "office":
            out = _office(src, ext, outdir / f"{stem}-compressed.{ext}", s, report)
        else:
            out = _zip(files, outdir / f"{Path(name).name}.zip", report)

    size = out.stat().st_size
    return out, {"kind": kind, "original": original, "size": size, "smaller": size < original}


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------

def _image(src: Path, ext: str, outdir: Path, stem: str, s: Settings, report: Report) -> Path:
    report(0.1, "Compressing image…")
    img = open_image(src)
    options = IMAGE_FORMATS[ext]
    fmt = s.format if s.format in options else options[0]
    out = outdir / f"{stem}-compressed.{fmt}"
    icc = img.info.get("icc_profile")

    if getattr(img, "n_frames", 1) > 1 and fmt in ("gif", "webp"):
        _animated(img, fmt, out, s)
        return out

    exif = None
    img = ImageOps.exif_transpose(img)  # bake the rotation in, so no orientation tag is needed
    if not s.strip_metadata:
        e = img.getexif()
        if e:
            e[0x0112] = 1
            exif = e.tobytes()
    if s.max_side and max(img.size) > s.max_side:
        img.thumbnail((s.max_side, s.max_side), Image.LANCZOS)
    report(0.5, "Compressing image…")

    extra = {}
    if icc:
        extra["icc_profile"] = icc
    if exif and fmt in ("jpg", "webp", "png", "heic", "avif"):
        extra["exif"] = exif

    if fmt == "jpg":
        flatten_alpha(img).save(out, "JPEG", quality=JPEG_Q[s.level], optimize=True, progressive=True,
                                subsampling=0 if s.level == "high" else 2, **extra)
    elif fmt == "webp":
        img = img.convert("RGBA" if has_alpha(img) else "RGB")
        img.save(out, "WEBP", quality=WEBP_Q[s.level], method=6, **extra)
    elif fmt == "avif":
        img = img.convert("RGBA" if has_alpha(img) else "RGB")
        img.save(out, "AVIF", quality=AVIF_Q[s.level], **extra)
    elif fmt == "heic":
        img = img.convert("RGBA" if has_alpha(img) else "RGB")
        img.save(out, "HEIF", quality=HEIC_Q[s.level], **extra)
    elif fmt == "png":
        colors = PNG_COLORS[s.level]
        if colors and img.mode != "P":
            # Like pngquant: a 256-colour palette with dithering keeps PNGs crisp and much smaller.
            alpha = has_alpha(img)
            img = img.convert("RGBA" if alpha else "RGB").quantize(
                colors=colors, method=Image.Quantize.FASTOCTREE if alpha else Image.Quantize.MEDIANCUT,
                dither=Image.Dither.FLOYDSTEINBERG)
        img.save(out, "PNG", optimize=True, **extra)
    elif fmt == "gif":
        img = img.convert("RGB").quantize(colors=GIF_COLORS[s.level], dither=Image.Dither.FLOYDSTEINBERG)
        img.save(out, "GIF", optimize=True)
    elif fmt == "tiff":
        img.save(out, "TIFF", compression="tiff_adobe_deflate", **({"icc_profile": icc} if icc else {}))
    else:  # pragma: no cover
        raise ConversionError(f"Can't save .{fmt}.")
    report(0.95, "Compressing image…")
    return out


def _animated(img: Image.Image, fmt: str, out: Path, s: Settings) -> None:
    frames, durations = [], []
    for frame in ImageSequence.Iterator(img):
        f = frame.convert("RGBA")
        if s.max_side and max(f.size) > s.max_side:
            f.thumbnail((s.max_side, s.max_side), Image.LANCZOS)
        frames.append(f)
        durations.append(frame.info.get("duration", img.info.get("duration", 100)))
    kwargs = dict(save_all=True, append_images=frames[1:], loop=img.info.get("loop", 0), duration=durations)
    if fmt == "webp":
        frames[0].save(out, "WEBP", quality=WEBP_Q[s.level], method=6, **kwargs)
    else:
        colors = GIF_COLORS[s.level]
        pal = [f.convert("RGB").quantize(colors=colors, dither=Image.Dither.FLOYDSTEINBERG) for f in frames]
        pal[0].save(out, "GIF", optimize=True, disposal=2, **{**kwargs, "append_images": pal[1:]})


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------

def _pdf(src: Path, out: Path, s: Settings, report: Report) -> Path:
    try:
        doc = pymupdf.open(src)
    except Exception:
        raise ConversionError("Couldn't open that PDF — it may be damaged.")
    if doc.needs_pass:
        raise ConversionError("That PDF is password-protected. Unlock it first.")
    try:
        threshold, target, quality = PDF_IMAGES[s.level]
        report(0.1, "Compressing images in the PDF…")
        doc.rewrite_images(dpi_threshold=threshold, dpi_target=target, quality=quality)
        report(0.7, "Removing unused data…")
        try:
            doc.subset_fonts()
        except Exception:
            pass
        if s.strip_metadata:
            doc.set_metadata({})
        doc.save(out, garbage=4, clean=True, deflate=True, deflate_images=True, deflate_fonts=True,
                 use_objstms=True)
    finally:
        doc.close()
    return out


# --------------------------------------------------------------------------
# Video and audio (ffmpeg)
# --------------------------------------------------------------------------

def _duration(src: Path) -> float:
    proc = subprocess.run([FFMPEG, "-hide_banner", "-i", str(src)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0


def _run_ffmpeg(args: list[str], src: Path, out: Path, label: str, report: Report) -> None:
    total = _duration(src)
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-i", str(src), *args,
           "-progress", "pipe:1", "-nostats", "-loglevel", "error", str(out)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in proc.stdout:
        if line.startswith("out_time_us=") and total > 0:
            try:
                done = int(line.split("=", 1)[1]) / 1e6 / total
            except ValueError:
                continue
            report(min(max(done, 0), 0.99), f"{label} {round(min(done, 1) * 100)}%")
    err = proc.stderr.read()
    if proc.wait() != 0:
        if "Invalid data found" in err:
            raise ConversionError("Couldn't read that file — it may be damaged or not a media file.")
        last = err.strip().splitlines()[-1] if err.strip() else "unknown error"
        raise ConversionError(f"ffmpeg failed: {last}")


def _video(src: Path, ext: str, outdir: Path, stem: str, s: Settings, report: Report) -> Path:
    out = outdir / f"{stem}-compressed.{'mov' if ext == 'mov' else 'mp4'}"
    r = s.resolution
    if r:
        # limit the short side (so 720p also works for portrait phone videos), keep sizes even
        scale = (f"scale='if(gt(iw,ih),-2,trunc(min(iw,{r})/2)*2)':"
                 f"'if(gt(iw,ih),trunc(min(ih,{r})/2)*2,-2)'")
    else:
        scale = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    args = ["-map", "0:v:0", "-map", "0:a?", "-vf", scale,
            "-c:v", "libx264", "-preset", "medium", "-crf", str(VIDEO_CRF[s.level]), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", VIDEO_AUDIO[s.level], "-movflags", "+faststart"]
    if s.strip_metadata:
        args += ["-map_metadata", "-1"]
    report(0.0, "Compressing video…")
    _run_ffmpeg(args, src, out, "Compressing video…", report)
    return out


def _audio(src: Path, ext: str, outdir: Path, stem: str, s: Settings, report: Report) -> Path:
    if ext in AUDIO_LOSSLESS:
        options = AUDIO_LOSSLESS[ext]
        fmt = s.format if s.format in options else options[0]
    else:
        fmt = AUDIO_LOSSY[ext]
    out = outdir / f"{stem}-compressed.{fmt}"
    rate = AUDIO_BITRATE[s.level]
    codec = {
        "mp3": ["-c:a", "libmp3lame", "-b:a", rate],
        "m4a": ["-c:a", "aac", "-b:a", rate],
        "ogg": ["-c:a", "libvorbis", "-b:a", rate],
        "opus": ["-c:a", "libopus", "-b:a", OPUS_BITRATE[s.level]],
        "flac": ["-c:a", "flac", "-compression_level", "12"],
    }[fmt]
    args = ["-vn", *codec]
    if s.strip_metadata:
        args += ["-map_metadata", "-1"]
    report(0.0, "Compressing audio…")
    _run_ffmpeg(args, src, out, "Compressing audio…", report)
    return out


# --------------------------------------------------------------------------
# Office documents and e-books (ZIP containers with pictures inside)
# --------------------------------------------------------------------------

def _shrink_picture(data: bytes, name: str, s: Settings) -> bytes | None:
    try:
        img = Image.open(BytesIO(data))
        img.load()
    except Exception:
        return None
    limit = OFFICE_MAX_SIDE[s.level]
    if max(img.size) > limit:
        img.thumbnail((limit, limit), Image.LANCZOS)
    buf = BytesIO()
    if name.endswith((".jpg", ".jpeg")):
        flatten_alpha(img).save(buf, "JPEG", quality=JPEG_Q[s.level], optimize=True, progressive=True)
    else:  # png: keep it a PNG (the document refers to it by name); only the smallest level uses a palette
        if s.level == "small" and img.mode != "P":
            alpha = has_alpha(img)
            img = img.convert("RGBA" if alpha else "RGB").quantize(
                colors=256, method=Image.Quantize.FASTOCTREE if alpha else Image.Quantize.MEDIANCUT,
                dither=Image.Dither.FLOYDSTEINBERG)
        img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _office(src: Path, ext: str, out: Path, s: Settings, report: Report) -> Path:
    try:
        zin = zipfile.ZipFile(src)
    except zipfile.BadZipFile:
        raise ConversionError(f"That .{ext} file is damaged or isn't really a .{ext} file.")
    with zin, zipfile.ZipFile(out, "w") as zout:
        items = zin.infolist()
        for i, info in enumerate(items):
            data = zin.read(info)
            lower = info.filename.lower()
            if lower.endswith((".jpg", ".jpeg", ".png")) and len(data) > 16_000:
                smaller = _shrink_picture(data, lower, s)
                if smaller and len(smaller) < len(data):
                    data = smaller
            entry = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            entry.external_attr = info.external_attr
            if info.filename == "mimetype":  # ODF/EPUB: must stay first and uncompressed
                zout.writestr(entry, data, compress_type=zipfile.ZIP_STORED)
            else:
                zout.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            report((i + 1) / len(items) * 0.98, "Compressing pictures in the document…")
    return out


# --------------------------------------------------------------------------
# Everything else
# --------------------------------------------------------------------------

def _zip(files: list[tuple[Path, str]], out: Path, report: Report) -> Path:
    used: set[str] = set()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for i, (path, name) in enumerate(files):
            arcname = Path(name).name or f"file-{i + 1}"
            base, dot, suffix = arcname.rpartition(".")
            n = 2
            while arcname in used:  # two files with the same name
                arcname = f"{base} {n}.{suffix}" if dot else f"{suffix} {n}"
                n += 1
            used.add(arcname)
            z.write(path, arcname=arcname)
            report((i + 1) / len(files) * 0.98, "Adding to ZIP archive…")
    return out
