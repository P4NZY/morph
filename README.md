# Morph — local file converter, compressor & image upscaler

A small Mac app for everyday file chores: convert between formats, make files smaller, and
upscale images. Everything runs on your Mac; nothing is sent to the internet.

## Start it

**Easiest:** double-click **Morph.app**. It opens in its own window; no browser or Terminal
needed. Close the window to quit. The app is self-contained, so you can keep it in
Applications, on the Desktop, or in the Dock. When you click **Save…**, the normal macOS Save
dialog opens.

Keyboard: **⌘1** Convert, **⌘2** Upscale, **⌘3** Compress, **⌘O** choose a file, **⌘S** save
the result, **Return** presses the blue button. You can drop a file anywhere in the window.

To rebuild the app after changing the code: `packaging/build_app.sh` (output:
`packaging/dist/Morph.app`). `packaging/snapshot.py <folder> [image]` takes light and dark
screenshots of every screen, for checking the design.

**From the source folder:** double-click **`start.command`** (the first run installs dependencies, about 1.5 GB including
PyTorch for AI upscaling, so allow a few minutes),
or from a terminal:

```bash
./start.command
```

Then open <http://127.0.0.1:8765>. Press `Ctrl+C` in the terminal to stop it.

## What it converts

| From | To |
|---|---|
| **Images**: PNG, JPG, WebP, GIF, BMP, TIFF, ICO, HEIC, AVIF | PNG, JPG, WebP, GIF, BMP, TIFF, ICO, AVIF, PDF |
| **PDF** | Word (DOCX), TXT, Markdown, HTML, PNG/JPG (one image per page, zipped if several) |
| **Documents**: DOCX, ODT, RTF, EPUB, HTML, Markdown, TXT, RST, LaTeX, Jupyter, PPTX (text) | PDF, DOCX, ODT, RTF, HTML, Markdown, TXT, EPUB |
| **Data**: CSV, TSV, XLSX, JSON | XLSX, CSV, TSV, JSON, HTML, Markdown, PDF |
| **Audio**: MP3, WAV, OGG, FLAC, M4A, AAC, Opus, AIFF, WMA | MP3, WAV, OGG, FLAC, M4A, Opus, AIFF |
| **Video**: MP4, MOV, WebM, AVI, MKV, M4V, FLV, WMV, MPEG, 3GP | MP4, MOV, WebM, AVI, MKV, GIF, MP3, WAV, M4A |

## Compressor

Pick **Smaller File**, **Balanced** or **Higher Quality**; Morph shows the new size and how much
was saved. If a file can't get any smaller, it says so instead of giving you a bigger copy.

| File | What Morph does | Options |
|---|---|---|
| **Images**: JPEG, PNG, HEIC, WebP, AVIF, GIF, TIFF, BMP | Re-encodes at a lower quality; PNGs get a 256-colour palette (like pngquant) | Format (e.g. PNG → WebP), maximum size, remove location and camera details |
| **PDF** | Resamples and recompresses the pictures inside (96 / 150 / 220 dpi); text stays sharp | Remove document info |
| **Video**: MP4, MOV, MKV, WebM, AVI, … | H.264 at a lower quality, with a progress bar | Resolution (1080p / 720p / 480p), remove location |
| **Audio**: MP3, M4A, OGG, Opus, WAV, AIFF, FLAC | Lower bitrate; WAV/AIFF/FLAC become M4A, MP3 or lossless FLAC | Format, remove tags |
| **Word, PowerPoint, Excel, OpenDocument, EPUB** | Shrinks the pictures inside; text and layout are untouched | |
| **Anything else, or several files at once** | Packs them into a ZIP archive (lossless) | |

## Upscaler

Presets: **200%**, **400%**, **4K UHD** (3840 × 2160), **MacBook Air 13″ M5** (2560 × 1664),
**MacBook Air 15″ M5** (2880 × 1864). Or use the slider for anything from 100% to 800%.

When the image's shape doesn't match a screen preset you choose:
- **Fill & crop**: fills the screen exactly; the dimmed edges in the preview get trimmed
- **Fit inside**: keeps the whole picture, so the output is smaller than the screen on one side
- **Stretch**: forces the exact size, which may distort the image

### Methods

| Method | What it does | Speed (Apple GPU) |
|---|---|---|
| **Standard** | Lanczos resampling + optional sharpening. Smooth, can't add detail. | instant |
| **AI · Photo** | [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) x4plus: rebuilds edges and texture in photos | 1080p → 4K ≈ 17 s |
| **AI · Art** | Real-ESRGAN x4plus anime: for drawings, anime, logos, flat colour | several times faster |

- The AI models are a one-time download (67 MB and 18 MB) from the official Real-ESRGAN GitHub
  releases. Click **Download model** in the app. They're stored in
  `~/Library/Application Support/Morph/models` and checked against pinned SHA-256 hashes.
- The model always enlarges 4×; Morph then resizes to your exact target (so 200%, 4K and the
  MacBook sizes all work).
- AI accepts images up to 8 MP. It only runs when enlarging; shrinking always uses Standard.
- AI makes up plausible detail. Very small text in the source can come out garbled.
- **Compare at 100%** (after upscaling) shows original vs. result side by side; drag to pan,
  drag the handle to compare.

Output is capped at 120 megapixels.

## Notes

- Word/ODT/RTF → PDF keeps text, headings, lists, tables and images, but not exact page layout.
  If [LibreOffice](https://www.libreoffice.org) is installed, Morph uses it automatically for
  exact Office → PDF conversion.
- PDF → Word works best on PDFs with real text. Scanned PDFs have no text to extract.
- Everything (pandoc, ffmpeg) comes bundled through pip, so you don't need Homebrew.

## Files

- `app.py`: web server (Flask)
- `converters.py`: all conversion logic and the format table
- `compressor.py`: compression for images, PDFs, video, audio, Office files and ZIP
- `upscaler.py`: image upscaling (sizes, presets, Standard method)
- `ai_upscaler.py`: Real-ESRGAN AI upscaling, model download, tiled GPU inference
- `jobs.py`: background jobs so long tasks (upscaling, video) can report progress
- `desktop.py`: the Morph.app entry point (native window via pywebview)
- `mac_window.py`: the native macOS toolbar (Convert | Upscale | Compress) and Show in Finder
- `selftest.py`: automated end-to-end test of the app window (`MORPH_SELFTEST=<dir>`)
- `static/`: the page shown in the window (HTML/CSS/JS)
- `filters/title_to_heading.lua`: pandoc filter that keeps document titles visible
- `packaging/`: app icon (built from `icon-art.webp` by `make_icon.py`), PyInstaller recipe
  (`Morph.spec`) and `build_app.sh`

AI models live in `~/Library/Application Support/Morph/models`, shared by the app and
`start.command`.
