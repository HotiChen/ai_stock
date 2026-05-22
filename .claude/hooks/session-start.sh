#!/bin/bash
set -euo pipefail

# Only run in remote (Claude Code on the web) environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$(dirname "$(realpath "$0")")")}"

SITE_PACKAGES=$(python -c "import site; print(site.getsitepackages()[0])")
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# ── Install standard packages via pip ────────────────────────────────────────
pip install --quiet \
  "anthropic>=0.97.0" \
  "google-genai>=1.0.0" \
  "yfinance>=0.2.40" \
  "numpy>=1.26.0" \
  "pandas>=2.0.0" \
  "requests>=2.31.0" \
  "python-dotenv>=1.0.0" \
  "streamlit>=1.35.0" \
  "plotly>=5.20.0" \
  "shioaji>=1.3.0" \
  "pytest>=9.0.0"

# ── sgmllib shim (needed by feedparser and ta) ────────────────────────────────
# sgmllib3k's setup.py is incompatible with this env's setuptools, so we
# extract the single sgmllib.py file manually from the source tarball.
if ! python -c "import sgmllib" 2>/dev/null; then
  pip download sgmllib3k --no-deps -d "$TMP/sgml" -q
  tar xzf "$TMP/sgml/sgmllib3k"*.tar.gz -C "$TMP/sgml"
  cp "$TMP/sgml/sgmllib3k-"*/sgmllib.py "$SITE_PACKAGES/"
fi

# ── feedparser (wheel, --no-deps since sgmllib is now in place) ───────────────
if ! python -c "import feedparser" 2>/dev/null; then
  pip download "feedparser>=6.0.0" --no-deps -d "$TMP/fp" -q
  pip install --quiet "$TMP/fp"/feedparser*.whl --no-deps
fi

# ── ta (pure Python, copy source dir; avoids broken setup.py) ─────────────────
if ! python -c "import ta" 2>/dev/null; then
  pip download "ta>=0.11.0" --no-deps -d "$TMP/ta" -q
  tar xzf "$TMP/ta/ta"*.tar.gz -C "$TMP/ta"
  cp -r "$TMP/ta/ta-"*/ta "$SITE_PACKAGES/"
fi

# ── streamlit-autorefresh ─────────────────────────────────────────────────────
if ! python -c "import streamlit_autorefresh" 2>/dev/null; then
  pip download "streamlit-autorefresh>=1.0.1" --no-deps -d "$TMP/sar" -q
  pip install --quiet "$TMP/sar"/streamlit_autorefresh*.whl --no-deps
fi

# Make project root importable
echo "export PYTHONPATH=\"${CLAUDE_PROJECT_DIR}:${PYTHONPATH:-}\"" >> "${CLAUDE_ENV_FILE:-/dev/null}"
