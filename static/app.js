"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const app = { formats: {}, kinds: {}, aliases: {}, presets: [], maxPixels: 120e6, ai: null, compress: null };

/* ================================================================== helpers */

function fmtBytes(n) {
  if (n < 1000) return `${n} bytes`;
  const units = ["KB", "MB", "GB"];
  let i = -1;
  do { n /= 1000; i++; } while (n >= 1000 && i < units.length - 1);  // Finder uses 1000
  return `${n >= 100 ? n.toFixed(0) : n.toFixed(1)} ${units[i]}`;
}

function extOf(name) {
  const m = /\.([^./\\]+)$/.exec(name || "");
  if (!m) return "";
  const e = m[1].toLowerCase();
  return app.aliases[e] || e;
}

function filenameFromHeader(header, fallback) {
  if (!header) return fallback;
  const star = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (star) { try { return decodeURIComponent(star[1]); } catch (_) { /* fall through */ } }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1] : fallback;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const show = (el, on = true) => el.classList.toggle("is-hidden", !on);

function loadPref(key, fallback) {
  try { return localStorage.getItem(`morph.${key}`) ?? fallback; } catch (_) { return fallback; }
}
function savePref(key, value) {
  try { localStorage.setItem(`morph.${key}`, value); } catch (_) { /* storage unavailable */ }
}

let hudTimer;
function hud(message) {
  const el = $("#hud");
  el.textContent = message;
  el.classList.add("is-shown");
  clearTimeout(hudTimer);
  hudTimer = setTimeout(() => el.classList.remove("is-shown"), 3600);
}
window.hud = hud;

/** Show or hide a result. Like a Mac dialog, only one button is the blue default:
 *  the action (Convert/Upscale) before there's a result, Save… after. */
function setResult(scope, has) {
  show(scope.result, has);
  show(scope.download, has);
  scope.go.classList.toggle("default", !has);
}

/** Activity line next to a default button: text, optional progress (null = indeterminate). */
function setActivity(scope, text = "", progress, isError = false) {
  const bar = scope.progress, label = scope.status;
  scope.activity.classList.toggle("is-error", isError);
  label.textContent = text;
  if (progress === undefined) {
    show(bar, false);
  } else {
    show(bar, true);
    if (progress === null) bar.removeAttribute("value");
    else { bar.max = 1; bar.value = progress; }
  }
}

/* ============================================================ format names */

const FORMAT_NAMES = {
  png: "PNG Image", jpg: "JPEG Image", webp: "WebP Image", gif: "GIF Image", bmp: "BMP Image",
  tiff: "TIFF Image", ico: "Windows Icon", heic: "HEIC Image", avif: "AVIF Image",
  pdf: "PDF Document", docx: "Word Document", odt: "OpenDocument Text", rtf: "Rich Text Document",
  epub: "EPUB Book", html: "Web Page", md: "Markdown Document", txt: "Plain Text",
  rst: "reStructuredText", tex: "LaTeX Document", ipynb: "Jupyter Notebook", pptx: "PowerPoint Presentation",
  csv: "CSV Document", tsv: "TSV Document", xlsx: "Excel Workbook", json: "JSON Data",
  mp3: "MP3 Audio", wav: "WAV Audio", ogg: "Ogg Vorbis Audio", flac: "FLAC Audio", m4a: "AAC Audio",
  aac: "AAC Audio", opus: "Opus Audio", aiff: "AIFF Audio", wma: "Windows Media Audio",
  mp4: "MPEG-4 Movie", mov: "QuickTime Movie", webm: "WebM Movie", avi: "AVI Movie", mkv: "Matroska Movie",
  m4v: "MPEG-4 Movie", flv: "Flash Video", wmv: "Windows Media Video", mpeg: "MPEG Movie", "3gp": "3GPP Movie",
  zip: "ZIP Archive",
};
const formatName = (ext) => FORMAT_NAMES[ext] || `${ext.toUpperCase()} File`;
const kindOf = (ext) => (ext === "pdf" ? "document" : app.kinds[ext]) || "other";

const KIND_GROUPS = [
  ["document", "Documents"], ["image", "Images"], ["data", "Spreadsheets & Data"],
  ["audio", "Audio"], ["video", "Video"],
];

// What most people want from each kind of file; the last choice per type is remembered.
const DEFAULT_TARGET = {
  pdf: "docx", docx: "pdf", odt: "pdf", rtf: "pdf", epub: "pdf", html: "pdf", md: "pdf", txt: "pdf",
  rst: "html", tex: "pdf", ipynb: "html", pptx: "pdf",
  png: "jpg", jpg: "png", webp: "png", gif: "png", bmp: "png", tiff: "png", ico: "png", heic: "jpg", avif: "jpg",
  csv: "xlsx", tsv: "xlsx", xlsx: "csv", json: "xlsx",
  mp3: "wav", wav: "mp3", ogg: "mp3", flac: "mp3", m4a: "mp3", aac: "mp3", opus: "mp3", aiff: "mp3", wma: "mp3",
  mp4: "mp3", mov: "mp4", webm: "mp4", avi: "mp4", mkv: "mp4", m4v: "mp4", flv: "mp4", wmv: "mp4", mpeg: "mp4", "3gp": "mp4",
};

function targetLabel(src, t) {
  const cat = app.formats[src]?.category;
  if (cat === "pdf" && (t === "png" || t === "jpg")) return `${t === "png" ? "PNG" : "JPEG"} Images, One per Page`;
  if (cat === "video" && t === "gif") return "Animated GIF";
  if (cat === "video" && kindOf(t) === "audio") return `${formatName(t)}, Sound Only`;
  return formatName(t);
}

function docIcon(ext) {
  const label = (ext || "?").toUpperCase().slice(0, 4);
  return `<svg viewBox="0 0 28 36" aria-hidden="true">
    <path class="doc-page" d="M3 .5h15.5l9 9V33a2.5 2.5 0 0 1-2.5 2.5H3A2.5 2.5 0 0 1 .5 33V3A2.5 2.5 0 0 1 3 .5z"/>
    <path class="doc-fold" d="M18.5 .5V7a2.5 2.5 0 0 0 2.5 2.5h6.5z"/>
    <text class="doc-ext kind-${kindOf(ext)}" x="14" y="29">${label}</text>
  </svg>`;
}

/* ================================================================= network */

/** POST a form with upload progress. Resolves to { blob, filename, xhr }. */
function send(url, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.responseType = "blob";
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress?.("upload", e.loaded / e.total); };
    xhr.upload.onload = () => onProgress?.("processing");
    xhr.onerror = () => reject(new Error("Morph's converter stopped responding. Quit and reopen Morph."));
    xhr.onload = async () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve({ blob: xhr.response, filename: filenameFromHeader(xhr.getResponseHeader("Content-Disposition"), "download"), xhr });
        return;
      }
      let msg = `The request failed (${xhr.status}).`;
      try { msg = JSON.parse(await xhr.response.text()).error || msg; } catch (_) { /* keep default */ }
      reject(new Error(msg));
    };
    xhr.send(formData);
  });
}

async function pollJob(id, onProgress) {
  for (;;) {
    const r = await fetch(`/api/jobs/${id}`);
    const j = await r.json().catch(() => ({ error: `The request failed (${r.status}).` }));
    if (!r.ok || j.status === "error") throw new Error(j.error || "The job failed.");
    onProgress?.(j);
    if (j.status === "done") return j;
    await sleep(300);
  }
}

/* ===================================================== Mac app integration */

const nativeApi = () => window.pywebview?.api;
let currentTab = "convert";
let lastSave = null;  // which tool the last Save… came from

// The three tools, in toolbar order. Scopes are looked up lazily (they're defined further down).
const TOOLS = {
  convert: { scope: () => C, open: (files) => onConvertFile(files[0]), label: "Convert" },
  upscale: { scope: () => U, open: (files) => onUpscaleFile(files[0]), label: "Upscale" },
  compress: { scope: () => K, open: (files) => onCompressFiles(files), label: "Compress" },
};
const TOOL_ORDER = Object.keys(TOOLS);

function enterDesktopMode() {
  const root = document.documentElement;
  if (root.classList.contains("desktop")) return;
  root.classList.add("desktop");
  [C, U, K].forEach((scope) => { scope.download.textContent = "Save…"; });
  nativeApi()?.tab_changed?.(currentTab);
}

// Called by Morph.app once a Save… has finished writing the file.
window.morphSaved = (path, folder) => {
  const scope = TOOLS[lastSave]?.scope() || C;
  scope.resultMeta.textContent = `Saved to ${folder}`;
  scope.reveal.dataset.path = path;
  show(scope.reveal, true);
};

function revealSaved(scope) {
  const path = scope.reveal.dataset.path;
  if (path) nativeApi()?.reveal?.(path);
}

/* ==================================================================== tabs */

function switchTab(name, opts = {}) {
  if (!TOOLS[name]) return;
  currentTab = name;
  $$(".panel").forEach((p) => p.classList.toggle("is-active", p.id === `panel-${name}`));
  $$("#web-tabs button").forEach((b) => {
    const on = b.dataset.tab === name;
    b.classList.toggle("is-on", on);
    b.setAttribute("aria-selected", String(on));
  });
  $("#drop-overlay-text").textContent = `Drop to ${TOOLS[name].label}`;
  savePref("tab", name);
  if (!opts.fromNative) nativeApi()?.tab_changed?.(name);
  if (name === "upscale") requestAnimationFrame(layoutStage);
}
window.switchTab = switchTab;
$$("#web-tabs button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));

/* ===================================================== keyboard & dropping */

document.addEventListener("keydown", (e) => {
  const scope = TOOLS[currentTab].scope();
  if (e.metaKey && !e.altKey && !e.ctrlKey) {
    const key = e.key.toLowerCase();
    if (["1", "2", "3"].includes(key)) {
      switchTab(TOOL_ORDER[+key - 1]);
    } else if (key === "o") {
      if (!scope.busy()) scope.input.click();
    } else if (key === "s") {
      if (scope.download.offsetParent) scope.download.click();
    } else {
      return;
    }
    e.preventDefault();
    return;
  }
  // Return presses the default button, as in any Mac window.
  const tag = document.activeElement?.tagName;
  if (e.key === "Enter" && !["SELECT", "INPUT", "BUTTON", "A", "TEXTAREA"].includes(tag)) {
    const target = scope.download.offsetParent ? scope.download : scope.go;
    if (!target.disabled && target.offsetParent) {
      target.click();
      e.preventDefault();
    }
  }
});

(() => {
  let depth = 0;
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  window.addEventListener("dragenter", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    if (++depth === 1) document.body.classList.add("is-dragging");
  });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("dragleave", () => {
    if (depth > 0 && --depth === 0) document.body.classList.remove("is-dragging");
  });
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    depth = 0;
    document.body.classList.remove("is-dragging");
    const files = [...(e.dataTransfer?.files || [])];
    if (files.length) TOOLS[currentTab].open(files);
  });
})();

/* ================================================================= convert */

const conv = { file: null, ext: "", url: null, busy: false };
const C = {
  drop: $("#c-drop"), choose: $("#c-choose"), input: $("#c-input"), form: $("#c-form"),
  srcIcon: $("#c-src-icon"), name: $("#c-name"), meta: $("#c-meta"), change: $("#c-change"),
  format: $("#c-format"), note: $("#c-note"), unsupported: $("#c-unsupported"),
  activity: $("#c-activity"), progress: $("#c-progress"), status: $("#c-status"), go: $("#c-go"),
  result: $("#c-result"), outIcon: $("#c-out-icon"), resultName: $("#c-result-name"),
  resultMeta: $("#c-result-meta"), reveal: $("#c-reveal"), download: $("#c-download"),
  matrix: $("#c-matrix"),
  busy: () => conv.busy,
};

async function onConvertFile(file) {
  if (conv.busy) return;
  await formatsReady;
  conv.file = file;
  conv.ext = extOf(file.name);
  const info = app.formats[conv.ext];

  show(C.drop, false);
  show(C.form, true);
  $("#c-column").classList.remove("is-empty");
  C.srcIcon.innerHTML = docIcon(conv.ext);
  C.name.textContent = file.name;
  C.meta.textContent = info ? `${fmtBytes(file.size)} · ${formatName(conv.ext)}` : fmtBytes(file.size);
  setResult(C, false);
  setActivity(C);
  C.format.replaceChildren();

  if (!info) {
    C.unsupported.textContent = conv.ext
      ? `Morph can't convert .${conv.ext} files. See Supported Formats below.`
      : "This file has no extension, so Morph can't tell what kind of file it is.";
    show(C.unsupported, true);
    C.note.textContent = "";
    C.format.disabled = true;
    C.go.disabled = true;
    return;
  }

  show(C.unsupported, false);
  const byKind = {};
  info.targets.forEach((t) => (byKind[kindOf(t)] ||= []).push(t));
  KIND_GROUPS.filter(([k]) => byKind[k]).forEach(([k, label]) => {
    const group = document.createElement("optgroup");
    group.label = label;
    byKind[k].forEach((t) => group.append(new Option(`${targetLabel(conv.ext, t)} (.${t})`, t)));
    C.format.append(group);
  });
  const remembered = loadPref(`target.${conv.ext}`, null);
  const preferred = [remembered, DEFAULT_TARGET[conv.ext]].find((t) => t && info.targets.includes(t));
  C.format.value = preferred || info.targets[0];
  C.format.disabled = false;
  C.go.disabled = false;
  updateConvertNote();
}

function updateConvertNote() {
  const src = conv.ext, t = C.format.value, cat = app.formats[src]?.category;
  const lossy = ["png", "webp", "gif", "tiff", "ico", "heic", "avif"];
  let note = "";
  if (cat === "pdf" && (t === "png" || t === "jpg")) note = "Each page becomes an image. Several pages are saved together as a .zip archive.";
  else if (cat === "pdf" && t === "docx") note = "Works best with PDFs that contain real text. Scanned pages can't become editable text.";
  else if (cat === "pdf") note = "Only the text is kept. Scanned pages have no text to extract.";
  else if (cat === "document" && t === "pdf") note = "Text, headings, lists, tables and images are kept. The exact page layout may differ.";
  else if (cat === "video" && t === "gif") note = "GIFs are made at up to 640 pixels wide and 12 frames per second.";
  else if (cat === "video" && kindOf(t) === "audio") note = "Only the sound track is saved.";
  else if (t === "ico") note = "Creates an icon with sizes from 16 to 256 pixels.";
  else if (t === "jpg" && lossy.includes(src)) note = "JPEG has no transparency, so any transparent areas become white.";
  else if (src === "xlsx" && (t === "csv" || t === "tsv")) note = "Only the first sheet of the workbook is saved.";
  C.note.textContent = note;
}

C.format.addEventListener("change", () => {
  savePref(`target.${conv.ext}`, C.format.value);
  updateConvertNote();
  setResult(C, false);
  setActivity(C);
});

async function convertNow() {
  if (!conv.file || conv.busy || C.go.disabled) return;
  conv.busy = true;
  [C.go, C.format, C.change].forEach((el) => { el.disabled = true; });
  setResult(C, false);

  const fd = new FormData();
  fd.append("file", conv.file);
  fd.append("target", C.format.value);
  try {
    const { blob, filename } = await send("/api/convert", fd, (phase, p) => {
      if (phase === "upload") setActivity(C, "Reading file…", p);
      else setActivity(C, "Converting…", null);
    });
    if (conv.url) URL.revokeObjectURL(conv.url);
    conv.url = URL.createObjectURL(blob);
    const outExt = extOf(filename);
    C.outIcon.innerHTML = docIcon(outExt);
    C.resultName.textContent = filename;
    C.resultMeta.textContent = `${fmtBytes(blob.size)} · ${formatName(outExt)}`;
    C.download.href = conv.url;
    C.download.download = filename;
    show(C.reveal, false);
    setResult(C, true);
    setActivity(C);
  } catch (err) {
    setActivity(C, err.message, undefined, true);
  } finally {
    conv.busy = false;
    [C.go, C.format, C.change].forEach((el) => { el.disabled = false; });
  }
}

C.go.addEventListener("click", convertNow);
C.choose.addEventListener("click", () => C.input.click());
C.change.addEventListener("click", () => C.input.click());
C.input.addEventListener("change", () => { if (C.input.files[0]) onConvertFile(C.input.files[0]); C.input.value = ""; });
C.download.addEventListener("click", () => { lastSave = "convert"; });
C.reveal.addEventListener("click", () => revealSaved(C));

function renderMatrix() {
  const cats = [
    ["image", "Images"], ["pdf", "PDF"], ["document", "Documents & Text"],
    ["data", "Spreadsheets & Data"], ["audio", "Audio"], ["video", "Video"],
  ];
  const group = document.createElement("div");
  group.className = "group";
  cats.forEach(([cat, title]) => {
    const inputs = Object.keys(app.formats).filter((k) => app.formats[k].category === cat);
    if (!inputs.length) return;
    const outputs = [...new Set(inputs.flatMap((k) => app.formats[k].targets))];
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<span class="row-label">${title}</span>
      <span class="format-list"><b>From</b> ${inputs.map((e) => e.toUpperCase()).join(", ")}<br>
      <b>To</b> ${outputs.map((e) => e.toUpperCase()).join(", ")}</span>`;
    group.append(row);
  });
  C.matrix.replaceChildren(group);
}

/* ================================================================= upscale */

const up = {
  file: null, w: 0, h: 0,
  mode: "scale", scale: 2, preset: "x2", tw: 0, th: 0, fit: "fill",
  method: loadPref("method", "standard"),
  previewUrl: null, resultUrl: null, resultPlan: null, busy: false, downloading: false,
  view: "preview", split: 50, panX: null, panY: 0,
};
const U = {
  drop: $("#u-drop"), choose: $("#u-choose"), input: $("#u-input"), stage: $("#u-stage"),
  area: $("#u-stage-area"), preview: $("#u-preview"), img: $("#u-img"), crop: $("#u-crop"),
  cropLabel: $("#u-crop-label"), info: $("#u-info"), change: $("#u-change"), view: $("#u-view"),
  preset: $("#u-preset"), slider: $("#u-slider"), scaleOut: $("#u-scale-out"),
  fitRow: $("#u-fit-row"), fit: $("#u-fit"), size: $("#u-size"), fitNote: $("#u-fit-note"),
  method: $("#u-method"), sharpenRow: $("#u-sharpen-row"), sharpen: $("#u-sharpen"),
  model: $("#u-model"), modelMeta: $("#u-model-meta"), modelProgress: $("#u-model-progress"),
  modelGet: $("#u-model-get"), methodNote: $("#u-method-note"), format: $("#u-format"),
  warn: $("#u-warn"), activity: $("#u-progress").parentElement, progress: $("#u-progress"),
  status: $("#u-status"), go: $("#u-go"), result: $("#u-result"), thumb: $("#u-thumb"),
  resultName: $("#u-result-name"), resultMeta: $("#u-result-meta"), reveal: $("#u-reveal"),
  download: $("#u-download"), compare: $("#u-compare"), stageBefore: $("#u-stage-before"),
  stageAfter: $("#u-stage-after"), before: $("#u-before"), after: $("#u-after"),
  divider: $("#u-divider"), afterTag: $("#u-after-tag"),
  busy: () => up.busy,
};

const isAI = () => up.method !== "standard";
const aiModel = () => app.ai?.models?.[up.method];

/** Mirror of upscaler.plan() on the server. */
function plan() {
  const { w, h } = up;
  const none = { crop: null, cropPx: [0, 0] };
  if (up.mode === "scale") {
    const rw = Math.max(1, Math.round(w * up.scale)), rh = Math.max(1, Math.round(h * up.scale));
    return { resize: [rw, rh], final: [rw, rh], scale: up.scale, ...none };
  }
  const { tw, th } = up;
  if (up.fit === "stretch") return { resize: [tw, th], final: [tw, th], scale: Math.max(tw / w, th / h), ...none };
  if (up.fit === "fit") {
    const s = Math.min(tw / w, th / h);
    const rw = Math.max(1, Math.round(w * s)), rh = Math.max(1, Math.round(h * s));
    return { resize: [rw, rh], final: [rw, rh], scale: s, ...none };
  }
  const s = Math.max(tw / w, th / h);
  const rw = Math.max(tw, Math.ceil(w * s)), rh = Math.max(th, Math.ceil(h * s));
  const cw = tw / s, ch = th / s;
  return {
    resize: [rw, rh], final: [tw, th], scale: s,
    crop: { left: (w - cw) / 2 / w, top: (h - ch) / 2 / h, width: cw / w, height: ch / h },
    cropPx: [Math.floor((rw - tw) / 2), Math.floor((rh - th) / 2)],
  };
}

function buildPresetMenu() {
  const scale = document.createElement("optgroup");
  scale.label = "Scale";
  const screen = document.createElement("optgroup");
  screen.label = "Screen Size";
  // Short names; the New Size row right below shows the exact pixels.
  app.presets.forEach((p) => {
    if (p.scale) scale.append(new Option(p.label, p.id));
    else screen.append(new Option(p.label.replace(/\s*\(M5\)/, ""), p.id));
  });
  U.preset.replaceChildren(new Option("Custom", "custom"), scale, screen);
  U.preset.value = up.preset;
}

function invalidateResult() {
  setResult(U, false);
  show(U.view, false);
  showView("preview");
}

function renderUpscale() {
  U.method.value = up.method;
  U.preset.value = up.preset || "custom";
  U.fit.value = up.fit;

  const ai = app.ai;
  $$("option", U.method).forEach((o) => { if (o.value !== "standard") o.disabled = !ai?.available; });
  show(U.sharpenRow, !isAI());
  if (isAI()) {
    const m = aiModel();
    U.methodNote.textContent = up.method === "photo"
      ? "Real-ESRGAN rebuilds edges and texture. Best for photos; very small text can come out garbled."
      : "Real-ESRGAN tuned for drawings, anime, logos and flat colour.";
    U.methodNote.textContent += ai?.device === "CPU" ? " Runs on the CPU, which is slow." : " Runs on the Mac's GPU.";
    show(U.model, !!m && !m.installed);
    if (m && !m.installed && !up.downloading) {
      $("#u-model-name").textContent = `${up.method === "photo" ? "Photo" : "Art"} Model`;
      U.modelMeta.textContent = `Not downloaded · ${(m.size / 1e6).toFixed(0)} MB`;
      U.methodNote.textContent += " The model is a one-time download from the official Real-ESRGAN releases.";
    }
    U.modelGet.disabled = up.downloading;
  } else {
    U.methodNote.textContent = "Lanczos resampling. Quick and clean, but it can't add detail that isn't in the image.";
    show(U.model, false);
  }

  if (!up.w) {
    U.size.textContent = "—";
    U.scaleOut.textContent = up.mode === "scale" ? `${Math.round(up.scale * 100)}%` : "—";
    show(U.fitRow, up.mode === "target");
    U.fitNote.textContent = "";
    show(U.warn, false);
    U.go.disabled = true;
    return;
  }

  const p = plan();
  const pct = Math.round(p.scale * 100);
  // the slider is logarithmic: 0 → 100%, 100 → 200%, 200 → 400%, 300 → 800%
  U.slider.value = String(Math.round(Math.min(300, Math.max(0, Math.log2(p.scale) * 100))));
  U.scaleOut.textContent = `${pct}%`;
  U.size.textContent = `${p.final[0]} × ${p.final[1]}`;

  const isTarget = up.mode === "target";
  show(U.fitRow, isTarget);
  const sameShape = isTarget && Math.abs(up.w / up.h - up.tw / up.th) < 0.005;
  U.fitNote.textContent = !isTarget || sameShape ? "" : {
    fill: "Fills the whole screen size. The dimmed edges of the image are trimmed.",
    fit: "Keeps the whole image, so one side ends up shorter than the screen.",
    stretch: "Forces the exact size. Shapes may look squashed.",
  }[up.fit];

  if (p.crop && (p.crop.width < 0.999 || p.crop.height < 0.999)) {
    Object.assign(U.crop.style, {
      left: `${p.crop.left * 100}%`, top: `${p.crop.top * 100}%`,
      width: `${p.crop.width * 100}%`, height: `${p.crop.height * 100}%`,
    });
    U.cropLabel.textContent = `${up.tw} × ${up.th}`;
    U.crop.hidden = false;
  } else {
    U.crop.hidden = true;
  }

  const pixels = p.resize[0] * p.resize[1];
  const aiActive = isAI() && p.scale > 1;
  const aiMax = app.ai?.maxInputPixels || 8e6;
  let warn = "", error = false;
  if (pixels > app.maxPixels) {
    warn = `The result would be ${(pixels / 1e6).toFixed(0)} megapixels; the limit is ${(app.maxPixels / 1e6).toFixed(0)}. Choose a smaller scale.`;
    error = true;
  } else if (aiActive && up.w * up.h > aiMax) {
    warn = `AI works with images up to ${(aiMax / 1e6).toFixed(0)} megapixels; this one is ${(up.w * up.h / 1e6).toFixed(1)}. Use Standard.`;
    error = true;
  } else if (p.scale < 1) {
    warn = "The image is already larger than this, so it will be made smaller" + (isAI() ? " (AI is only used when enlarging)." : ".");
  } else if (isAI() && p.scale === 1) {
    warn = "This is the original size, so there's nothing to enlarge.";
  }
  U.warn.textContent = warn;
  show(U.warn, !!warn);
  U.warn.classList.toggle("error", error);

  const needsModel = aiActive && !aiModel()?.installed;
  U.go.disabled = error || up.busy || needsModel;
  U.go.textContent = p.scale < 1 ? "Resize" : "Upscale";
}

/** Size the preview to fit the canvas while keeping the image's shape. */
function layoutStage() {
  if (!up.w || U.stage.classList.contains("is-hidden")) return;
  const box = U.area.getBoundingClientRect();
  if (!box.width || !box.height) return;
  const pad = 24;
  const s = Math.min((box.width - pad * 2) / up.w, (box.height - pad * 2) / up.h);
  U.preview.style.width = `${Math.max(1, Math.floor(up.w * s))}px`;
  U.preview.style.height = `${Math.max(1, Math.floor(up.h * s))}px`;
  if (up.view === "compare") layoutCompare();
}
new ResizeObserver(() => layoutStage()).observe(U.area);

async function onUpscaleFile(file) {
  if (up.busy) return;
  await formatsReady;
  const ext = extOf(file.name);
  if (app.formats[ext]?.category !== "image") {
    hud(ext ? `Morph can't upscale .${ext} files. Choose an image.` : "That file has no extension.");
    return;
  }
  let dims;
  try {
    dims = await loadPreview(file);
  } catch (err) {
    hud(err.message);
    return;
  }
  up.file = file;
  up.w = dims.w;
  up.h = dims.h;
  U.info.textContent = `${file.name} — ${up.w} × ${up.h} · ${fmtBytes(file.size)}`;
  show(U.drop, false);
  show(U.stage, true);
  invalidateResult();
  setActivity(U);
  renderUpscale();
  requestAnimationFrame(layoutStage);
}

/** Show the image; ask the server for a preview when WebKit can't decode it (e.g. some TIFFs). */
function loadPreview(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const probe = new Image();
    probe.onload = () => {
      if (up.previewUrl) URL.revokeObjectURL(up.previewUrl);
      up.previewUrl = url;
      U.img.src = url;
      resolve({ w: probe.naturalWidth, h: probe.naturalHeight });
    };
    probe.onerror = async () => {
      URL.revokeObjectURL(url);
      try {
        const fd = new FormData();
        fd.append("file", file);
        const { blob, xhr } = await send("/api/preview", fd);
        if (up.previewUrl) URL.revokeObjectURL(up.previewUrl);
        up.previewUrl = URL.createObjectURL(blob);
        U.img.src = up.previewUrl;
        resolve({ w: +xhr.getResponseHeader("X-Width"), h: +xhr.getResponseHeader("X-Height") });
      } catch (err) {
        reject(err);
      }
    };
    probe.src = url;
  });
}

U.preset.addEventListener("change", () => {
  const p = app.presets.find((x) => x.id === U.preset.value);
  up.preset = U.preset.value;
  if (p?.scale) { up.mode = "scale"; up.scale = p.scale; }
  else if (p) { up.mode = "target"; up.tw = p.width; up.th = p.height; }
  else { up.mode = "scale"; up.preset = "custom"; }
  invalidateResult();
  renderUpscale();
});

U.slider.addEventListener("input", () => {
  const pct = Math.round((Math.pow(2, +U.slider.value / 100) * 100) / 5) * 5;  // tidy 5% steps
  up.mode = "scale";
  up.scale = pct / 100;
  up.preset = pct === 200 ? "x2" : pct === 400 ? "x4" : "custom";
  invalidateResult();
  renderUpscale();
});

U.fit.addEventListener("change", () => { up.fit = U.fit.value; invalidateResult(); renderUpscale(); });
U.method.addEventListener("change", () => {
  up.method = U.method.value;
  savePref("method", up.method);
  invalidateResult();
  renderUpscale();
});
U.sharpen.addEventListener("change", invalidateResult);
U.format.addEventListener("change", invalidateResult);
U.choose.addEventListener("click", () => U.input.click());
U.change.addEventListener("click", () => { if (!up.busy) U.input.click(); });
U.input.addEventListener("change", () => { if (U.input.files[0]) onUpscaleFile(U.input.files[0]); U.input.value = ""; });
U.download.addEventListener("click", () => { lastSave = "upscale"; });
U.reveal.addEventListener("click", () => revealSaved(U));

U.go.addEventListener("click", async () => {
  if (!up.file || up.busy || U.go.disabled) return;
  up.busy = true;
  U.go.disabled = true;
  [U.preset, U.slider, U.fit, U.method, U.format, U.sharpen, U.change].forEach((el) => { el.disabled = true; });
  invalidateResult();

  const p = plan();
  const fd = new FormData();
  fd.append("file", up.file);
  fd.append("mode", up.mode);
  if (up.mode === "scale") fd.append("scale", String(up.scale));
  else { fd.append("width", up.tw); fd.append("height", up.th); fd.append("fit", up.fit); }
  fd.append("sharpen", U.sharpen.checked ? "1" : "0");
  fd.append("format", U.format.value);
  fd.append("method", up.method);

  try {
    const started = await send("/api/upscale", fd, (phase, pr) => {
      if (phase === "upload") setActivity(U, "Reading image…", pr);
      else setActivity(U, "Starting…", null);
    });
    const { job } = JSON.parse(await started.blob.text());
    const done = await pollJob(job, (j) => setActivity(U, j.message, j.progress > 0 ? j.progress : null));

    const res = await fetch(`/api/jobs/${job}/result`);
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || "Couldn't get the result.");
    const blob = await res.blob();
    const filename = filenameFromHeader(res.headers.get("Content-Disposition"), done.filename || "upscaled.png");

    if (up.resultUrl) URL.revokeObjectURL(up.resultUrl);
    up.resultUrl = URL.createObjectURL(blob);
    up.resultPlan = p;
    U.thumb.src = up.resultUrl;
    U.resultName.textContent = filename;
    U.resultMeta.textContent = `${done.width} × ${done.height} · ${fmtBytes(blob.size)}`;
    U.download.href = up.resultUrl;
    U.download.download = filename;
    show(U.reveal, false);
    setResult(U, true);
    setActivity(U);
    const usedAI = done.method && done.method !== "standard";
    setupCompare(p, usedAI ? app.ai.models[done.method].label.replace(" · ", " — ") : "Standard");
  } catch (err) {
    setActivity(U, err.message, undefined, true);
  } finally {
    up.busy = false;
    [U.preset, U.slider, U.fit, U.method, U.format, U.sharpen, U.change].forEach((el) => { el.disabled = false; });
    renderUpscale();
  }
});

/* ---------- AI model download ---------- */

async function refreshAI() {
  try { app.ai = await (await fetch("/api/ai")).json(); } catch (_) { app.ai = null; }
}

U.modelGet.addEventListener("click", async () => {
  if (up.downloading || !isAI()) return;
  const id = up.method;
  up.downloading = true;
  U.modelGet.disabled = true;
  show(U.modelProgress, true);
  try {
    const r = await fetch(`/api/ai/${id}/download`, { method: "POST" });
    const body = await r.json();
    if (!r.ok) throw new Error(body.error);
    await pollJob(body.job, (j) => { U.modelProgress.value = j.progress; U.modelMeta.textContent = j.message; });
    await refreshAI();
  } catch (err) {
    U.modelMeta.textContent = err.message;
  } finally {
    up.downloading = false;
    show(U.modelProgress, false);
    renderUpscale();
  }
});

/* ---------- before / after at 100% ---------- */

function showView(view) {
  up.view = view;
  $$("button", U.view).forEach((b) => b.classList.toggle("is-on", b.dataset.view === view));
  show(U.preview, view === "preview");
  show(U.compare, view === "compare");
  if (view === "compare") layoutCompare(true);
}
$$("button", U.view).forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));

function setupCompare(p, label) {
  const [rw, rh] = p.resize, [fw, fh] = p.final, [cx, cy] = p.cropPx;
  U.before.src = up.previewUrl;
  Object.assign(U.before.style, { width: `${rw}px`, height: `${rh}px`, left: `${-cx}px`, top: `${-cy}px` });
  U.after.src = up.resultUrl;
  Object.assign(U.after.style, { width: `${fw}px`, height: `${fh}px`, left: "0px", top: "0px" });
  U.afterTag.textContent = label;
  up.split = 50;
  up.panX = null;
  show(U.view, true);
}

function layoutCompare(recentre = false) {
  if (!up.resultPlan) return;
  const [fw, fh] = up.resultPlan.final;
  const vw = U.compare.clientWidth, vh = U.compare.clientHeight;
  if (recentre || up.panX === null) {
    up.panX = (vw - fw) / 2;
    up.panY = (vh - fh) / 2;
  }
  up.panX = fw <= vw ? (vw - fw) / 2 : Math.min(0, Math.max(vw - fw, up.panX));
  up.panY = fh <= vh ? (vh - fh) / 2 : Math.min(0, Math.max(vh - fh, up.panY));
  const t = `translate(${up.panX}px, ${up.panY}px)`;
  U.stageBefore.style.transform = t;
  U.stageAfter.style.transform = t;
  U.compare.style.setProperty("--split", `${up.split}%`);
  U.divider.setAttribute("aria-valuenow", String(Math.round(up.split)));
}

(() => {
  let drag = null;
  U.compare.addEventListener("pointerdown", (e) => {
    const rect = U.compare.getBoundingClientRect();
    const dividerX = rect.left + (rect.width * up.split) / 100;
    drag = Math.abs(e.clientX - dividerX) < 20
      ? { kind: "split", rect }
      : { kind: "pan", x: e.clientX, y: e.clientY, px: up.panX, py: up.panY };
    U.compare.setPointerCapture(e.pointerId);
    U.compare.classList.toggle("is-dragging", drag.kind === "pan");
  });
  U.compare.addEventListener("pointermove", (e) => {
    if (!drag) return;
    if (drag.kind === "split") {
      up.split = Math.min(100, Math.max(0, ((e.clientX - drag.rect.left) / drag.rect.width) * 100));
    } else {
      up.panX = drag.px + (e.clientX - drag.x);
      up.panY = drag.py + (e.clientY - drag.y);
    }
    layoutCompare();
  });
  const end = () => { drag = null; U.compare.classList.remove("is-dragging"); };
  U.compare.addEventListener("pointerup", end);
  U.compare.addEventListener("pointercancel", end);
  U.divider.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 10 : 2;
    if (e.key === "ArrowLeft") up.split = Math.max(0, up.split - step);
    else if (e.key === "ArrowRight") up.split = Math.min(100, up.split + step);
    else return;
    e.preventDefault();
    layoutCompare();
  });
})();

/* ================================================================ compress */

const comp = { files: [], kind: "", ext: "", url: null, busy: false };
const K = {
  drop: $("#k-drop"), choose: $("#k-choose"), input: $("#k-input"), form: $("#k-form"), column: $("#k-column"),
  srcIcon: $("#k-src-icon"), name: $("#k-name"), meta: $("#k-meta"), change: $("#k-change"),
  level: $("#k-level"), levelRow: $("#k-level-row"), format: $("#k-format"), formatRow: $("#k-format-row"),
  maxSide: $("#k-max-side"), sizeRow: $("#k-size-row"), resolution: $("#k-resolution"), resRow: $("#k-res-row"),
  strip: $("#k-strip"), stripRow: $("#k-strip-row"), stripLabel: $("#k-strip-label"), zipRow: $("#k-zip-row"),
  note: $("#k-note"),
  activity: $("#k-activity"), progress: $("#k-progress"), status: $("#k-status"), go: $("#k-go"),
  result: $("#k-result"), outIcon: $("#k-out-icon"), resultName: $("#k-result-name"),
  resultMeta: $("#k-result-meta"), reveal: $("#k-reveal"), saving: $("#k-saving"), download: $("#k-download"),
  busy: () => comp.busy,
};
const K_CONTROLS = [K.go, K.level, K.format, K.maxSide, K.resolution, K.strip, K.change];

const COMPRESS_FORMAT_NAMES = {
  jpg: "JPEG", png: "PNG", webp: "WebP", heic: "HEIC", avif: "AVIF", gif: "GIF", tiff: "TIFF",
  m4a: "M4A (AAC)", mp3: "MP3", flac: "FLAC (Lossless)",
};

// What "Remove metadata" means for each kind, and whether it's on by default.
// Photos and videos can carry your location; music tags are usually worth keeping.
const STRIP = {
  image: ["Remove location and camera details", true],
  video: ["Remove location and other metadata", true],
  audio: ["Remove tags (title, artist, album)", false],
  pdf: ["Remove document info (title, author)", false],
};

function compressKind(ext) {
  const c = app.compress;
  if (c.imageFormats[ext]) return "image";
  if (ext === "pdf") return "pdf";
  if (c.videoIn.includes(ext)) return "video";
  if (c.audioLossy[ext] || c.audioLossless[ext]) return "audio";
  if (c.officeIn.includes(ext)) return "office";
  return "archive";
}

async function onCompressFiles(fileList) {
  if (comp.busy || !fileList.length) return;
  await formatsReady;
  comp.files = [...fileList];
  const single = comp.files.length === 1;
  comp.ext = single ? extOf(comp.files[0].name) : "zip";
  comp.kind = single ? compressKind(comp.ext) : "archive";
  const total = comp.files.reduce((n, f) => n + f.size, 0);

  show(K.drop, false);
  show(K.form, true);
  K.column.classList.remove("is-empty");
  K.srcIcon.innerHTML = docIcon(single ? comp.ext : "zip");
  if (single) {
    K.name.textContent = comp.files[0].name;
    K.meta.textContent = `${fmtBytes(total)} · ${comp.ext ? formatName(comp.ext) : "Document"}`;
  } else {
    K.name.textContent = `${comp.files.length} Files`;
    const names = comp.files.slice(0, 3).map((f) => f.name).join(", ");
    K.meta.textContent = `${fmtBytes(total)} · ${names}${comp.files.length > 3 ? ", …" : ""}`;
  }

  K.format.replaceChildren();
  const options = comp.kind === "image" ? app.compress.imageFormats[comp.ext]
    : comp.kind === "audio" ? app.compress.audioLossless[comp.ext] : null;
  if (options) {
    options.forEach((f, i) => {
      const same = comp.kind === "image" && i === 0 && f === comp.ext;
      K.format.append(new Option(same ? `${COMPRESS_FORMAT_NAMES[f]} (Same as Original)` : COMPRESS_FORMAT_NAMES[f], f));
    });
    const remembered = loadPref(`compress.format.${comp.ext}`, null);
    K.format.value = options.includes(remembered) ? remembered : options[0];
  }
  if (STRIP[comp.kind]) {
    K.stripLabel.textContent = STRIP[comp.kind][0];
    K.strip.checked = STRIP[comp.kind][1];
  }
  setResult(K, false);
  setActivity(K);
  renderCompress();
}

function compressNote() {
  const level = K.level.value, fmt = K.format.value, ext = comp.ext;
  switch (comp.kind) {
    case "image":
      if (fmt === "png") return level === "high"
        ? "Keeps the PNG lossless, so the saving is modest. WebP or JPEG is much smaller for photos."
        : "Reduces the PNG to at most 256 colours, like pngquant. For photos, WebP or JPEG is usually far smaller.";
      if (fmt === "webp") return "WebP is usually much smaller than JPEG or PNG and opens in Preview, Safari and most apps.";
      if (fmt === "heic") return "HEIC is already efficient (it's what iPhones use), so the saving is smaller. JPEG works everywhere.";
      if (fmt === "avif") return "AVIF is already efficient, so the saving may be small.";
      if (fmt === "gif") return "GIF is limited to 256 colours. WebP keeps the animation and is much smaller.";
      if (fmt === "tiff") return "TIFF stays lossless. Choose JPEG or WebP for a much smaller file.";
      if (ext !== "jpg") return "JPEG has no transparency, so any transparent areas become white.";
      return "Saves the JPEG at a lower quality. WebP gives an even smaller file.";
    case "pdf":
      return `Pictures are resampled to ${{ small: 96, balanced: 150, high: 220 }[level]} dpi and recompressed. Text stays sharp and selectable.`;
    case "video":
      return "Re-encodes as H.264, which plays everywhere. Long videos can take a few minutes.";
    case "audio": {
      if (fmt === "flac") return "FLAC is lossless: the sound is identical and the file is about half the size.";
      const kbps = (ext === "opus" ? { small: 64, balanced: 96, high: 128 } : { small: 96, balanced: 128, high: 192 })[level];
      return app.compress.audioLossless[ext]
        ? `Encodes at ${kbps} kbps, which sounds close to the original at a fraction of the size.`
        : `Re-encodes at ${kbps} kbps.`;
    }
    case "office":
      return "Shrinks the pictures inside the document. Text and layout don't change.";
    default:
      return comp.files.length > 1
        ? "The files are packed into one ZIP archive. Nothing is lost."
        : "This kind of file is packed into a ZIP archive. Nothing is lost; text and data shrink a lot, files that are already compressed won't.";
  }
}

function renderCompress() {
  const kind = comp.kind;
  show(K.levelRow, kind !== "archive");
  show(K.formatRow, K.format.options.length > 0);
  show(K.sizeRow, kind === "image");
  show(K.resRow, kind === "video");
  show(K.stripRow, !!STRIP[kind]);
  show(K.zipRow, kind === "archive");
  K.note.textContent = compressNote();
  K.go.disabled = comp.busy || !comp.files.length;
}

function compressOptionChanged() {
  if (K.format.value) savePref(`compress.format.${comp.ext}`, K.format.value);
  setResult(K, false);
  setActivity(K);
  renderCompress();
}

async function compressNow() {
  if (!comp.files.length || comp.busy) return;
  comp.busy = true;
  K_CONTROLS.forEach((el) => { el.disabled = true; });
  setResult(K, false);

  const fd = new FormData();
  comp.files.forEach((f) => fd.append("file", f));
  fd.append("level", K.level.value);
  fd.append("format", K.format.value || "same");
  fd.append("max_side", K.maxSide.value);
  fd.append("resolution", K.resolution.value);
  fd.append("strip_metadata", K.strip.checked ? "1" : "0");
  try {
    const started = await send("/api/compress", fd, (phase, p) => {
      if (phase === "upload") setActivity(K, "Reading file…", p);
      else setActivity(K, "Starting…", null);
    });
    const { job } = JSON.parse(await started.blob.text());
    const done = await pollJob(job, (j) => setActivity(K, j.message, j.progress > 0 ? j.progress : null));

    if (!done.smaller) {
      fetch(`/api/jobs/${job}`, { method: "DELETE" });
      setActivity(K, "This is already as small as it gets. Morph couldn't make it any smaller.");
      return;
    }
    const res = await fetch(`/api/jobs/${job}/result`);
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || "Couldn't get the result.");
    const blob = await res.blob();
    const filename = filenameFromHeader(res.headers.get("Content-Disposition"), done.filename || "compressed");

    if (comp.url) URL.revokeObjectURL(comp.url);
    comp.url = URL.createObjectURL(blob);
    K.outIcon.innerHTML = docIcon(extOf(filename));
    K.resultName.textContent = filename;
    K.resultMeta.textContent = `${fmtBytes(done.size)} · was ${fmtBytes(done.original)}`;
    const pct = Math.round((1 - done.size / done.original) * 100);
    K.saving.textContent = pct >= 1 ? `${pct}% smaller` : "Slightly smaller";
    K.download.href = comp.url;
    K.download.download = filename;
    show(K.reveal, false);
    setResult(K, true);
    setActivity(K);
  } catch (err) {
    setActivity(K, err.message, undefined, true);
  } finally {
    comp.busy = false;
    K_CONTROLS.forEach((el) => { el.disabled = false; });
    renderCompress();
  }
}

[K.level, K.format, K.maxSide, K.resolution, K.strip].forEach((el) => el.addEventListener("change", compressOptionChanged));
K.go.addEventListener("click", compressNow);
K.choose.addEventListener("click", () => K.input.click());
K.change.addEventListener("click", () => { if (!comp.busy) K.input.click(); });
K.input.addEventListener("change", () => { if (K.input.files.length) onCompressFiles([...K.input.files]); K.input.value = ""; });
K.download.addEventListener("click", () => { lastSave = "compress"; });
K.reveal.addEventListener("click", () => revealSaved(K));

/* ==================================================================== boot */

if (window.pywebview) enterDesktopMode();
window.addEventListener("pywebviewready", enterDesktopMode);

switchTab(location.hash.slice(1) || loadPref("tab", "convert"));

// The format list is quick; AI status loads PyTorch (a few seconds on first launch), so
// it must not hold up converting. Files dropped before the list arrives simply wait for it.
const formatsReady = fetch("/api/formats")
  .then((r) => r.json())
  .then((data) => {
    app.formats = data.formats;
    app.kinds = data.kinds;
    app.aliases = data.aliases;
    app.presets = data.presets;
    app.maxPixels = data.maxOutputPixels;
    app.compress = data.compress;
    buildPresetMenu();
    renderMatrix();
    renderUpscale();
  })
  .catch(() => hud("Couldn't start Morph's converter. Quit and reopen Morph."));

refreshAI().then(async () => {
  await formatsReady;
  if (isAI() && !app.ai?.models?.[up.method]) up.method = "standard";
  renderUpscale();
});
