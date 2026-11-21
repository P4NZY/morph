#!/bin/bash
# Double-click this file in Finder to start Morph.
cd "$(dirname "$0")" || exit 1

if ! .venv/bin/python -c "import flask, PIL, pymupdf, pypandoc, torch, spandrel, certifi" 2>/dev/null; then
  echo "First run: installing what Morph needs (a few minutes)…"
  rm -rf .venv
  python3 -m venv .venv || { echo "Python 3 is required: https://www.python.org/downloads/"; exit 1; }
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt || exit 1
fi

exec .venv/bin/python app.py --open
