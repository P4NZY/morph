#!/bin/bash
# Build Morph.app (a standalone Mac app) into packaging/dist/.
# Usage: packaging/build_app.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if ! .venv/bin/python -c "import PyInstaller, webview" 2>/dev/null; then
  .venv/bin/python -m pip install --quiet -r requirements.txt
fi
[ -f packaging/Morph.icns ] || .venv/bin/python packaging/make_icon.py

.venv/bin/python -m PyInstaller packaging/Morph.spec --noconfirm --clean \
  --distpath packaging/dist --workpath packaging/build

echo
echo "Built: packaging/dist/Morph.app ($(du -sh packaging/dist/Morph.app | cut -f1))"
