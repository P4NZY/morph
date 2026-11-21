"""Conversion engine.

Defines which input formats can become which output formats, and does the
actual work. Everything runs locally:

* images            -> Pillow (+ pillow-heif for HEIC)
* PDFs              -> PyMuPDF, pdf2docx
* documents         -> pandoc (bundled via pypandoc_binary), PyMuPDF for PDF output
* spreadsheets/data -> pandas + openpyxl
* audio / video     -> ffmpeg (bundled via imageio-ffmpeg)
"""

from __future__ import annotations

import html as html_lib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import imageio_ffmpeg
import pandas as pd
import pillow_heif
import pymupdf
import pypandoc
from PIL import Image, ImageOps, ImageSequence

pillow_heif.register_heif_opener()
Image.MAX_IMAGE_PIXELS = 250_000_000


class ConversionError(Exception):
    """A conversion failed for a reason we can explain to the user."""


# --------------------------------------------------------------------------
# Format table
# --------------------------------------------------------------------------

ALIASES = {
    "jpeg": "jpg", "tif": "tiff", "htm": "html", "markdown": "md",
    "aif": "aiff", "mpg": "mpeg", "heif": "heic",
}

IMAGE_IN = ["png", "jpg", "webp", "gif", "bmp", "tiff", "ico", "heic", "avif"]
IMAGE_OUT = ["png", "jpg", "webp", "gif", "bmp", "tiff", "ico", "avif", "pdf"]

PDF_OUT = ["docx", "txt", "md", "html", "png", "jpg"]

DOC_IN = ["docx", "odt", "rtf", "epub", "html", "md", "txt", "rst", "tex", "ipynb", "pptx"]
DOC_OUT = ["pdf", "docx", "odt", "rtf", "html", "md", "txt", "epub"]

SHEET_IN = ["csv", "tsv", "xlsx", "json"]
SHEET_OUT = ["xlsx", "csv", "tsv", "json", "html", "md", "pdf"]

AUDIO_IN = ["mp3", "wav", "ogg", "flac", "m4a", "aac", "opus", "aiff", "wma"]
AUDIO_OUT = ["mp3", "wav", "ogg", "flac", "m4a", "opus", "aiff"]

VIDEO_IN = ["mp4", "mov", "webm", "avi", "mkv", "m4v", "flv", "wmv", "mpeg", "3gp"]
VIDEO_OUT = ["mp4", "mov", "webm", "avi", "mkv", "gif", "mp3", "wav", "m4a"]

LABELS = {
    "png": "PNG image", "jpg": "JPEG image", "webp": "WebP image", "gif": "GIF",
    "bmp": "Bitmap", "tiff": "TIFF image", "ico": "Icon", "heic": "HEIC photo",
    "avif": "AVIF image", "pdf": "PDF", "docx": "Word", "odt": "OpenDocument",
    "rtf": "Rich text", "epub": "E-book", "html": "Web page", "md": "Markdown",
    "txt": "Plain text", "rst": "reStructuredText", "tex": "LaTeX",
    "ipynb": "Jupyter notebook", "pptx": "PowerPoint", "csv": "CSV",
    "tsv": "TSV", "xlsx": "Excel", "json": "JSON", "mp3": "MP3 audio",
    "wav": "WAV audio", "ogg": "Ogg audio", "flac": "FLAC audio",
    "m4a": "M4A audio", "aac": "AAC audio", "opus": "Opus audio",
    "aiff": "AIFF audio", "wma": "WMA audio", "mp4": "MP4 video",
    "mov": "QuickTime video", "webm": "WebM video", "avi": "AVI video",
    "mkv": "MKV video", "m4v": "M4V video", "flv": "Flash video",
    "wmv": "WMV video", "mpeg": "MPEG video", "3gp": "3GP video",
}

# What kind of thing each *output* format is (used to group buttons in the UI).
KIND = {}
for _e in IMAGE_IN + IMAGE_OUT:
    KIND[_e] = "image"
for _e in DOC_IN + DOC_OUT + ["pdf"]:
    KIND[_e] = "document"
for _e in SHEET_IN:
    KIND[_e] = "data"
for _e in AUDIO_IN + AUDIO_OUT:
    KIND[_e] = "audio"
for _e in VIDEO_IN:
    KIND[_e] = "video"
KIND["gif"] = "image"
KIND["pdf"] = "document"


def _build_formats() -> dict:
    table: dict[str, dict] = {}

    def add(inputs, outputs, category):
        for ext in inputs:
            table[ext] = {
                "category": category,
                "label": LABELS.get(ext, ext.upper()),
                "targets": [t for t in outputs if t != ext],
            }

    add(IMAGE_IN, IMAGE_OUT, "image")
    add(DOC_IN, DOC_OUT, "document")
    add(SHEET_IN, SHEET_OUT, "data")
    add(AUDIO_IN, AUDIO_OUT, "audio")
    add(VIDEO_IN, VIDEO_OUT, "video")
    table["pdf"] = {"category": "pdf", "label": "PDF", "targets": PDF_OUT}
    return table


FORMATS = _build_formats()


def normalize_ext(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    return ALIASES.get(ext, ext)


def formats_for_client() -> dict:
    return {
        "formats": FORMATS,
        "labels": LABELS,
        "kinds": {t: KIND.get(t, "document") for t in LABELS},
        "aliases": ALIASES,
    }


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def convert(src: Path, target: str, outdir: Path, stem: str) -> Path:
    """Convert ``src`` to ``target`` inside ``outdir``. Returns the output path."""
    ext = normalize_ext(src.suffix)
    target = normalize_ext(target)
    info = FORMATS.get(ext)
    if info is None:
        raise ConversionError(f".{ext} files aren't supported yet.")
    if target not in info["targets"]:
        raise ConversionError(f"Can't convert .{ext} to .{target}.")

    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"{stem}.{target}"
    cat = info["category"]

    if cat == "image":
        _convert_image(src, target, out)
    elif cat == "pdf":
        out = _convert_pdf(src, target, out, stem)
    elif cat == "document":
        _convert_document(src, ext, target, out, outdir, stem)
    elif cat == "data":
        _convert_table(src, ext, target, out, stem)
    elif cat in ("audio", "video"):
        _convert_media(src, target, out)
    else:  # pragma: no cover
        raise ConversionError("Unknown file category.")

    if not out.exists() or out.stat().st_size == 0:
        raise ConversionError("The conversion produced an empty file.")
    return out


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------

PIL_FORMAT = {
    "png": "PNG", "jpg": "JPEG", "webp": "WEBP", "gif": "GIF", "bmp": "BMP",
    "tiff": "TIFF", "ico": "ICO", "avif": "AVIF", "pdf": "PDF",
}


def flatten_alpha(img: Image.Image, background=(255, 255, 255)) -> Image.Image:
    """Return an RGB image, compositing any transparency onto ``background``."""
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, background)
        bg.paste(img, mask=img.getchannel("A"))
        return bg
    return img.convert("RGB")


def has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)


def open_image(src: Path) -> Image.Image:
    try:
        img = Image.open(src)
        img.load()
    except Image.DecompressionBombError:
        raise ConversionError("That image is too large to process safely.")
    except Exception:
        raise ConversionError("Couldn't read that image — it may be damaged or not really an image.")
    return img


def _convert_image(src: Path, target: str, out: Path) -> None:
    img = open_image(src)
    fmt = PIL_FORMAT[target]
    frames = getattr(img, "n_frames", 1)

    # Keep animation for animated GIF/WebP -> GIF/WebP.
    if target in ("gif", "webp") and frames > 1:
        seq = [f.convert("RGBA") for f in ImageSequence.Iterator(img)]
        durations = []
        for f in ImageSequence.Iterator(img):
            durations.append(f.info.get("duration", img.info.get("duration", 100)))
        kwargs = dict(save_all=True, append_images=seq[1:], loop=img.info.get("loop", 0),
                      duration=durations)
        if target == "webp":
            kwargs["quality"] = 90
        else:
            kwargs["disposal"] = 2
        seq[0].save(out, fmt, **kwargs)
        return

    img = ImageOps.exif_transpose(img)
    icc = img.info.get("icc_profile")

    if target in ("jpg", "bmp", "pdf"):
        img = flatten_alpha(img)
    elif target in ("webp", "avif", "png", "tiff"):
        if img.mode not in ("RGB", "RGBA", "L", "LA") or target in ("webp", "avif"):
            img = img.convert("RGBA" if has_alpha(img) else "RGB")
    elif target == "gif":
        img = img.convert("RGBA") if has_alpha(img) else img.convert("RGB")
    elif target == "ico":
        img = img.convert("RGBA")
        side = max(img.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
        img = square
        if side > 256:
            img = img.resize((256, 256), Image.LANCZOS)

    save_kwargs: dict = {}
    if target == "jpg":
        save_kwargs = dict(quality=92, optimize=True, subsampling=0)
    elif target == "webp":
        save_kwargs = dict(quality=92, method=4)
    elif target == "avif":
        save_kwargs = dict(quality=80)
    elif target == "png":
        save_kwargs = dict(optimize=False)
    elif target == "tiff":
        save_kwargs = dict(compression="tiff_lzw")
    elif target == "ico":
        save_kwargs = dict(sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256) if s <= img.width])
    elif target == "pdf":
        save_kwargs = dict(resolution=150)

    if icc and target in ("jpg", "png", "webp", "tiff", "avif"):
        save_kwargs["icc_profile"] = icc

    img.save(out, fmt, **save_kwargs)


# --------------------------------------------------------------------------
# PDF input
# --------------------------------------------------------------------------

def _open_pdf(src: Path):
    try:
        doc = pymupdf.open(src)
    except Exception:
        raise ConversionError("Couldn't open that PDF — it may be damaged.")
    if doc.needs_pass:
        raise ConversionError("That PDF is password-protected. Unlock it first.")
    return doc


def _convert_pdf(src: Path, target: str, out: Path, stem: str) -> Path:
    if target == "docx":
        from pdf2docx import Converter

        doc = _open_pdf(src)
        doc.close()
        cv = Converter(str(src))
        try:
            cv.convert(str(out))
        finally:
            cv.close()
        return out

    doc = _open_pdf(src)
    try:
        if target == "txt":
            text = "\n\n".join(page.get_text("text") for page in doc)
            if not text.strip():
                raise ConversionError(
                    "This PDF has no selectable text (it's probably a scan), so there's nothing to extract."
                )
            out.write_text(text, encoding="utf-8")
            return out

        if target == "md":
            try:
                import pymupdf4llm

                md = pymupdf4llm.to_markdown(doc)
            except ConversionError:
                raise
            except Exception:
                md = "\n\n".join(page.get_text("text") for page in doc)
            out.write_text(md, encoding="utf-8")
            return out

        if target == "html":
            body = "\n".join(
                f'<section class="page" id="page-{i + 1}">{page.get_text("xhtml")}</section>'
                for i, page in enumerate(doc)
            )
            out.write_text(_html_page(stem, body), encoding="utf-8")
            return out

        if target in ("png", "jpg"):
            dpi = 200 if doc.page_count <= 20 else 150
            images = []
            for i, page in enumerate(doc):
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                name = f"{stem}-page-{i + 1:02d}.{target}"
                if target == "png":
                    data = pix.tobytes("png")
                else:
                    data = pix.tobytes("jpg", jpg_quality=92)
                images.append((name, data))
            if len(images) == 1:
                out.write_bytes(images[0][1])
                return out
            zip_path = out.with_name(f"{stem}-{target}-pages.zip")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for name, data in images:
                    zf.writestr(name, data)
            return zip_path
    finally:
        doc.close()

    raise ConversionError(f"Can't convert PDF to .{target}.")


# --------------------------------------------------------------------------
# Documents (pandoc)
# --------------------------------------------------------------------------

PANDOC_READER = {
    "docx": "docx", "odt": "odt", "rtf": "rtf", "epub": "epub", "html": "html",
    "md": "markdown", "rst": "rst", "tex": "latex", "ipynb": "ipynb", "pptx": "pptx",
}
PANDOC_WRITER = {
    "docx": "docx", "odt": "odt", "rtf": "rtf", "epub": "epub", "html": "html",
    "md": "gfm", "txt": "plain",
}

TITLE_FILTER = Path(__file__).resolve().parent / "filters" / "title_to_heading.lua"

SOFFICE_CANDIDATES = [
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    shutil.which("soffice") or "",
]


def _soffice() -> str | None:
    for path in SOFFICE_CANDIDATES:
        if path and Path(path).exists():
            return path
    return None


def read_text_file(src: Path) -> str:
    raw = src.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _text_to_html(text: str) -> str:
    paragraphs = [p for p in text.replace("\r\n", "\n").split("\n\n")]
    parts = []
    for p in paragraphs:
        if not p.strip():
            continue
        lines = [html_lib.escape(line) for line in p.split("\n")]
        parts.append("<p>" + "<br>\n".join(lines) + "</p>")
    return "\n".join(parts) or "<p></p>"


def _pandoc(source: Path | str, reader: str, writer: str, out: Path | None,
            extra: list[str] | None = None, is_text: bool = False) -> str:
    args = extra or []
    try:
        if is_text:
            return pypandoc.convert_text(source, writer, format=reader,
                                         outputfile=str(out) if out else None, extra_args=args)
        return pypandoc.convert_file(str(source), writer, format=reader,
                                     outputfile=str(out) if out else None, extra_args=args)
    except RuntimeError as exc:
        msg = str(exc).strip().splitlines()
        detail = msg[-1] if msg else "unknown error"
        raise ConversionError(f"Document conversion failed: {detail}")


def _convert_document(src: Path, ext: str, target: str, out: Path, outdir: Path, stem: str) -> None:
    # Best-fidelity Office -> PDF if LibreOffice happens to be installed.
    if target == "pdf" and ext in ("docx", "odt", "rtf", "pptx") and _soffice():
        _soffice_to_pdf(src, out)
        return

    if ext == "txt":
        source, reader, is_text = _text_to_html(read_text_file(src)), "html", True
    else:
        source, reader, is_text = src, PANDOC_READER[ext], False

    # Keep the document's title visible (except for web pages, whose <title> is
    # usually just the browser-tab text, and e-books, which need it as metadata).
    title_filter = []
    if ext not in ("html", "txt") and target != "epub":
        title_filter = [f"--lua-filter={TITLE_FILTER}"]

    if target == "pdf":
        media = outdir / "_media"
        fragment = _pandoc(source, reader, "html", None,
                           ["--wrap=none", f"--extract-media={media}"] + title_filter, is_text)
        fragment = fragment.replace(f"{outdir}/", "")
        html_to_pdf(fragment, out, archive_dir=outdir)
        shutil.rmtree(media, ignore_errors=True)
        return

    writer = PANDOC_WRITER[target]
    extra = list(title_filter)
    if target in ("txt", "md"):
        extra += ["--wrap=none"]
    if target == "html":
        extra += ["--standalone", "--embed-resources", f"--metadata=pagetitle:{stem}"]
    if target == "rtf":
        extra += ["--standalone"]
    if target == "epub":
        extra += [f"--metadata=title:{stem}"]

    if target in ("txt", "md", "html"):
        text = _pandoc(source, reader, writer, None, extra, is_text)
        out.write_text(text, encoding="utf-8")
    else:
        _pandoc(source, reader, writer, out, extra, is_text)


def _soffice_to_pdf(src: Path, out: Path) -> None:
    tmp = out.parent / "_soffice"
    tmp.mkdir(exist_ok=True)
    proc = subprocess.run(
        [_soffice(), "--headless", "--convert-to", "pdf", "--outdir", str(tmp), str(src)],
        capture_output=True, text=True, timeout=300,
    )
    produced = tmp / (src.stem + ".pdf")
    if proc.returncode != 0 or not produced.exists():
        raise ConversionError("LibreOffice couldn't convert that file to PDF.")
    shutil.move(str(produced), out)
    shutil.rmtree(tmp, ignore_errors=True)


PDF_CSS = """
body { font-family: sans-serif; font-size: 11pt; line-height: 1.45; color: #1d1d1f; }
h1 { font-size: 21pt; margin: 0 0 10pt 0; }
h2 { font-size: 16pt; margin: 14pt 0 6pt 0; }
h3 { font-size: 13pt; margin: 12pt 0 4pt 0; }
p  { margin: 0 0 8pt 0; }
table { border-collapse: collapse; margin: 6pt 0 10pt 0; }
th, td { border: 0.6pt solid #b8b8c0; padding: 3pt 6pt; font-size: 9.5pt; }
th { background-color: #eeeef3; font-weight: bold; }
pre, code { font-family: monospace; font-size: 9.5pt; }
pre { background-color: #f4f4f7; padding: 6pt; }
blockquote { margin-left: 14pt; color: #555; }
"""


def html_to_pdf(fragment: str, out: Path, archive_dir: Path | None = None,
                landscape: bool = False) -> None:
    """Lay out an HTML fragment onto A4 pages with PyMuPDF's Story engine."""
    archive = pymupdf.Archive(str(archive_dir)) if archive_dir else None
    story = pymupdf.Story(html=fragment, user_css=PDF_CSS, archive=archive)
    page_rect = pymupdf.paper_rect("a4-l" if landscape else "a4")
    content = page_rect + (54, 54, -54, -54)
    raw = out.with_suffix(".raw.pdf")
    writer = pymupdf.DocumentWriter(str(raw))
    more, pages = 1, 0
    while more and pages < 5000:
        device = writer.begin_page(page_rect)
        more, _ = story.place(content)
        story.draw(device)
        writer.end_page()
        pages += 1
    writer.close()

    # Embed only the glyphs actually used (fallback fonts for e.g. Chinese are huge).
    doc = pymupdf.open(raw)
    try:
        doc.subset_fonts()
    except Exception:
        pass
    doc.save(out, garbage=3, deflate=True)
    doc.close()
    raw.unlink(missing_ok=True)


def _html_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_lib.escape(title)}</title>
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; color: #1d1d1f; }}
table {{ border-collapse: collapse; margin: 1rem 0; font-size: 14px; }}
th, td {{ border: 1px solid #d4d4da; padding: 6px 10px; text-align: left; }}
th {{ background: #f2f2f6; }}
.page {{ border-bottom: 1px dashed #ccc; padding-bottom: 1.5rem; margin-bottom: 1.5rem; }}
</style></head><body>
{body}
</body></html>
"""


# --------------------------------------------------------------------------
# Tables / data
# --------------------------------------------------------------------------

def _read_table(src: Path, ext: str) -> dict[str, pd.DataFrame]:
    try:
        if ext == "csv":
            return {"Sheet1": pd.read_csv(src, sep=None, engine="python", encoding_errors="replace")}
        if ext == "tsv":
            return {"Sheet1": pd.read_csv(src, sep="\t", encoding_errors="replace")}
        if ext == "xlsx":
            sheets = pd.read_excel(src, sheet_name=None)
            return {name: df for name, df in sheets.items()} or {"Sheet1": pd.DataFrame()}
        if ext == "json":
            data = json.loads(read_text_file(src))
            if isinstance(data, dict) and all(isinstance(v, list) for v in data.values()) and data:
                # {"sheetA": [...], "sheetB": [...]} or column-oriented {"col": [...]}
                if all(isinstance(r, dict) for v in data.values() for r in v):
                    return {str(k)[:31]: pd.json_normalize(v) for k, v in data.items()}
                return {"Sheet1": pd.DataFrame(data)}
            if isinstance(data, list):
                return {"Sheet1": pd.json_normalize(data) if all(isinstance(r, dict) for r in data)
                        else pd.DataFrame({"value": data})}
            if isinstance(data, dict):
                return {"Sheet1": pd.json_normalize(data)}
            raise ConversionError("That JSON isn't shaped like a table.")
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"Couldn't read that file as a table ({type(exc).__name__}).")
    raise ConversionError(f"Can't read .{ext} as a table.")


def _convert_table(src: Path, ext: str, target: str, out: Path, stem: str) -> None:
    sheets = _read_table(src, ext)
    first = next(iter(sheets.values()))
    multi = len(sheets) > 1

    if target == "xlsx":
        with pd.ExcelWriter(out, engine="openpyxl") as xw:
            for name, df in sheets.items():
                df.to_excel(xw, sheet_name=name[:31] or "Sheet1", index=False)
        return
    if target == "csv":
        first.to_csv(out, index=False)
        return
    if target == "tsv":
        first.to_csv(out, sep="\t", index=False)
        return
    if target == "json":
        if multi:
            payload = {name: json.loads(df.to_json(orient="records", date_format="iso"))
                       for name, df in sheets.items()}
        else:
            payload = json.loads(first.to_json(orient="records", date_format="iso"))
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return
    if target == "md":
        parts = []
        for name, df in sheets.items():
            if multi:
                parts.append(f"## {name}\n")
            parts.append(df.to_markdown(index=False) + "\n")
        out.write_text("\n".join(parts), encoding="utf-8")
        return
    if target in ("html", "pdf"):
        parts = []
        for name, df in sheets.items():
            if multi:
                parts.append(f"<h2>{html_lib.escape(str(name))}</h2>")
            parts.append(df.to_html(index=False, border=0, na_rep=""))
        body = "\n".join(parts)
        if target == "html":
            out.write_text(_html_page(stem, body), encoding="utf-8")
        else:
            widest = max(len(df.columns) for df in sheets.values())
            html_to_pdf(body, out, landscape=widest > 6)
        return
    raise ConversionError(f"Can't convert a table to .{target}.")


# --------------------------------------------------------------------------
# Audio / video (ffmpeg)
# --------------------------------------------------------------------------

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

AUDIO_CODECS = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    "wav": ["-c:a", "pcm_s16le"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "5"],
    "flac": ["-c:a", "flac"],
    "m4a": ["-c:a", "aac", "-b:a", "192k"],
    "opus": ["-c:a", "libopus", "-b:a", "128k"],
    "aiff": ["-c:a", "pcm_s16be"],
}

EVEN = ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
VIDEO_CODECS = {
    "mp4": EVEN + ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"],
    "mov": EVEN + ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"],
    "mkv": EVEN + ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "160k"],
    "webm": ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1",
             "-deadline", "good", "-cpu-used", "4", "-c:a", "libopus", "-b:a", "128k"],
    "avi": ["-c:v", "mpeg4", "-q:v", "3", "-c:a", "libmp3lame", "-q:a", "3"],
}

GIF_FILTER = ("fps=12,scale='min(640,iw)':-1:flags=lanczos,split[a][b];"
              "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")


def _convert_media(src: Path, target: str, out: Path) -> None:
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src)]
    if target == "gif":
        cmd += ["-filter_complex", GIF_FILTER, "-loop", "0"]
    elif target in AUDIO_CODECS:
        cmd += ["-vn"] + AUDIO_CODECS[target]
    elif target in VIDEO_CODECS:
        cmd += VIDEO_CODECS[target]
    else:
        raise ConversionError(f"Can't convert to .{target}.")
    cmd.append(str(out))

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        err = proc.stderr.strip()
        if "does not contain any stream" in err or "matches no streams" in err:
            raise ConversionError("This file has no audio track to extract.")
        if "Invalid data found" in err:
            raise ConversionError("Couldn't read that file — it may be damaged or not a media file.")
        last = err.splitlines()[-1] if err else "unknown error"
        raise ConversionError(f"ffmpeg failed: {last}")
