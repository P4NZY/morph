"""Morph — a local file converter and image upscaler.

Run:  .venv/bin/python app.py        then open http://127.0.0.1:8765
"""

from __future__ import annotations

import io
import shutil
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

from flask import Flask, after_this_request, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

import ai_upscaler
import compressor
import converters
import jobs
import upscaler
from converters import ConversionError, normalize_ext

HOST, PORT = "127.0.0.1", 8765
BASE = Path(__file__).resolve().parent

app = Flask(__name__, static_folder=str(BASE / "static"), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 GB uploads


def _clean_stem(filename: str) -> str:
    stem = Path(filename.replace("\\", "/")).name
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem
    stem = "".join(c for c in stem if c not in '/\\:*?"<>|\0').strip(" .")
    return stem or "converted"


def _receive_upload() -> tuple[Path, Path, str, str]:
    """Save the uploaded file into a fresh temp dir. Returns (workdir, src, ext, stem)."""
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        raise ConversionError("No file was uploaded.")
    ext = normalize_ext(Path(upload.filename).suffix)
    if not ext:
        raise ConversionError("That file has no extension, so its type can't be detected.")
    workdir = Path(tempfile.mkdtemp(prefix="morph-"))
    src = workdir / "in" / secure_filename(f"input.{ext}")
    src.parent.mkdir()
    upload.save(src)

    @after_this_request
    def _cleanup(response):
        shutil.rmtree(workdir, ignore_errors=True)
        return response

    return workdir, src, ext, _clean_stem(upload.filename)


def _clean_name(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name
    name = "".join(c for c in name if c not in '/\\:*?"<>|\0').strip(" ")
    return name or "file"


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/formats")
def formats():
    data = converters.formats_for_client()
    data["presets"] = upscaler.PRESETS
    data["maxOutputPixels"] = upscaler.MAX_OUTPUT_PIXELS
    data["compress"] = compressor.client_info()
    return jsonify(data)


@app.post("/api/compress")
def compress():
    """Start a compression job for one file (or several, which become one ZIP)."""
    uploads = [u for u in request.files.getlist("file") if u and u.filename]
    if not uploads:
        raise ConversionError("No file was uploaded.")
    settings = compressor.Settings.from_form(request.form)

    workdir = Path(tempfile.mkdtemp(prefix="morph-"))  # owned by the job from here on
    files = []
    try:
        for i, upload in enumerate(uploads):
            name = _clean_name(upload.filename)
            ext = normalize_ext(Path(name).suffix) or "bin"
            src = workdir / "in" / f"{i:04d}.{ext}"
            src.parent.mkdir(exist_ok=True)
            upload.save(src)
            files.append((src, name))
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise

    def work(job: jobs.Job):
        out, info = compressor.compress(files, settings, workdir / "out", job.report)
        job.result_path = out
        job.filename = out.name
        job.meta = info
        job.report(1.0, "Done")

    return jsonify(job=jobs.start(work, workdir=workdir).id)


@app.post("/api/convert")
def convert():
    workdir, src, _ext, stem = _receive_upload()
    target = request.form.get("target", "")
    out = converters.convert(src, target, workdir / "out", stem)
    return send_file(out, as_attachment=True, download_name=out.name)


@app.post("/api/upscale")
def upscale():
    """Start an upscale job. Poll /api/jobs/<id>, then fetch /api/jobs/<id>/result."""
    _workdir, src, _ext, stem = _receive_upload()
    form = request.form
    sizing: dict = {}
    try:
        if form.get("mode") == "target":
            sizing = {
                "width": int(form.get("width", 0)),
                "height": int(form.get("height", 0)),
                "fit": form.get("fit", "fill"),
            }
        else:
            scale = float(form.get("scale", 2))
            if not 0.1 <= scale <= 16:
                raise ConversionError("Scale must be between 10% and 1600%.")
            sizing = {"scale": scale}
    except ValueError:
        raise ConversionError("Invalid size settings.")

    method = form.get("method", "standard")
    if method != "standard":
        if method not in ai_upscaler.MODELS:
            raise ConversionError("Unknown upscaling method.")
        if not ai_upscaler.is_installed(method):
            raise ConversionError("Download the AI model first.")
    out_format = form.get("format", "png")
    sharpen = form.get("sharpen", "1") == "1"
    data = io.BytesIO(src.read_bytes())  # the temp dir is removed when this request ends

    def work(job: jobs.Job):
        result, info = upscaler.upscale(data, out_format=out_format, sharpen=sharpen,
                                        method=method, report=job.report, **sizing)
        w, h = info["output"]
        job.result = result
        job.filename = f"{stem}-{w}x{h}{'-ai' if info['method'] != 'standard' else ''}.{info['format']}"
        job.mimetype = f"image/{'jpeg' if info['format'] == 'jpg' else info['format']}"
        job.meta = {"width": w, "height": h, "method": info["method"]}
        job.report(1.0, "Done")

    return jsonify(job=jobs.start(work).id)


@app.get("/api/jobs/<job_id>")
def job_status(job_id):
    job = jobs.get(job_id)
    if job is None:
        return jsonify(error="That job has expired. Please try again."), 404
    return jsonify(job.public())


@app.delete("/api/jobs/<job_id>")
def job_discard(job_id):
    """The page doesn't want this result (e.g. compression didn't help): free it now."""
    job = jobs.pop(job_id)
    if job is not None:
        job.discard_files()
    return jsonify(ok=True)


@app.get("/api/jobs/<job_id>/result")
def job_result(job_id):
    job = jobs.get(job_id)
    if job is None or job.status != "done" or (job.result is None and job.result_path is None):
        return jsonify(error="That result isn't available any more."), 404
    jobs.pop(job_id)
    if job.result_path is not None:
        resp = send_file(job.result_path, as_attachment=True, download_name=job.filename)

        @after_this_request
        def _discard(response):
            job.discard_files()  # the open file is still sent; the folder entry goes now
            return response
    else:
        resp = send_file(io.BytesIO(job.result), as_attachment=True,
                         download_name=job.filename, mimetype=job.mimetype)
    resp.headers["X-Output-Width"] = str(job.meta.get("width", ""))
    resp.headers["X-Output-Height"] = str(job.meta.get("height", ""))
    return resp


@app.get("/api/ai")
def ai_status():
    return jsonify(ai_upscaler.status())


@app.post("/api/ai/<model_id>/download")
def ai_download(model_id):
    if model_id not in ai_upscaler.MODELS:
        raise ConversionError("Unknown model.")
    return jsonify(job=jobs.start(lambda job: ai_upscaler.download(model_id, job.report)).id)


@app.post("/api/preview")
def preview():
    _workdir, src, _ext, _stem = _receive_upload()
    data, (w, h) = upscaler.preview(src)
    resp = send_file(io.BytesIO(data), mimetype="image/jpeg")
    resp.headers["X-Width"] = str(w)
    resp.headers["X-Height"] = str(h)
    return resp


@app.errorhandler(ConversionError)
def conversion_error(exc):
    return jsonify(error=str(exc)), 400


@app.errorhandler(413)
def too_large(_exc):
    return jsonify(error="That file is bigger than the 2 GB limit."), 413


@app.errorhandler(Exception)
def unexpected(exc):
    if isinstance(exc, HTTPException):
        return exc
    app.logger.exception("Unexpected error")
    return jsonify(error=f"Something went wrong: {type(exc).__name__}: {exc}"), 500


if __name__ == "__main__":
    url = f"http://{HOST}:{PORT}"
    print(f"\n  Morph is running at {url}\n  Press Ctrl+C to stop.\n")
    if "--open" in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
