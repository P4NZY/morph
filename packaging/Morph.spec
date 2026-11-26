# PyInstaller recipe for Morph.app. Build with:  packaging/build_app.sh
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, copy_metadata

ROOT = Path(SPECPATH).parent

datas = [(str(ROOT / "static"), "static"), (str(ROOT / "filters"), "filters")]
binaries = []
hiddenimports = ["selftest", "ai_upscaler", "jobs", "upscaler", "converters", "mac_window", "compressor"]

# Packages that ship binaries (pandoc, ffmpeg, libheif) or data files PyInstaller can't see.
for pkg in ["pypandoc", "imageio_ffmpeg", "pymupdf", "pymupdf4llm", "spandrel",
            "pillow_heif", "pdf2docx", "webview"]:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += collect_data_files("docx")  # python-docx's default.docx template

# torchvision >= 0.24 ships its ops as _C_stable.so, loaded by path, so PyInstaller misses it.
binaries += collect_dynamic_libs("torchvision", search_patterns=["*.so", "*.dylib"])

# pandas checks optional libraries' versions via package metadata (e.g. to_markdown -> tabulate).
for pkg in ["tabulate", "openpyxl", "pandas", "torch", "torchvision", "spandrel", "pywebview"]:
    datas += copy_metadata(pkg)

a = Analysis(
    [str(ROOT / "desktop.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "jupyter", "pytest", "torch.utils.tensorboard"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Morph",
    console=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Morph")

app = BUNDLE(
    coll,
    name="Morph.app",
    icon=str(ROOT / "packaging" / "Morph.icns"),
    bundle_identifier="local.morph.converter",
    info_plist={
        "CFBundleName": "Morph",
        "CFBundleDisplayName": "Morph",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "LSApplicationCategoryType": "public.app-category.utilities",
        # the window talks to Morph's own server on 127.0.0.1
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)
