#!/usr/bin/env bash
# Render a markdown phase record to PDF.
#
# Verified path (Phase 2): this machine has no LaTeX, weasyprint, or wkhtmltopdf,
# and `cupsfilter` fails on HTML input. Chrome headless `--print-to-pdf` produces
# a valid PDF and is the only converter confirmed working here.
#
# Usage: tools/mkpdf.sh docs/phases/phase-03-simulation-core.md
# Writes: docs/phases/phase-03-simulation-core.pdf

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <path-to-markdown>" >&2
  exit 1
fi

MD_PATH="$1"
if [[ ! -f "$MD_PATH" ]]; then
  echo "error: not found: $MD_PATH" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PDF_PATH="${MD_PATH%.md}.pdf"
HTML_TMP="$(mktemp -t linetwin-phase-XXXX).html"

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [[ ! -x "$CHROME" ]]; then
  echo "error: Chrome not found at expected path: $CHROME" >&2
  exit 1
fi

python3 - "$MD_PATH" "$HTML_TMP" <<'PY'
import html
import sys
from pathlib import Path

try:
    import markdown as md
except ImportError:
    sys.exit("error: python 'markdown' package not installed (pip install markdown)")

src, dst = sys.argv[1], sys.argv[2]
text = Path(src).read_text(encoding="utf-8")
body = md.markdown(
    text,
    extensions=["extra", "sane_lists", "tables", "toc"],
)
title = html.escape(Path(src).stem.replace("-", " ").title())

page = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
  @page {{ margin: 20mm 18mm; }}
  body {{
    font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
    font-size: 10.5pt; line-height: 1.5; color: #17141C; max-width: 100%;
  }}
  h1 {{ font-size: 18pt; border-bottom: 2px solid #8400D6; padding-bottom: 6pt; }}
  h2 {{ font-size: 13.5pt; color: #6A00B8; margin-top: 20pt; }}
  h3 {{ font-size: 11.5pt; margin-top: 14pt; }}
  code {{ background: #F2F2F2; padding: 1px 4px; border-radius: 3px; font-size: 9pt; }}
  pre code {{ display: block; padding: 8pt; overflow-x: auto; white-space: pre-wrap; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 9pt; margin: 8pt 0; }}
  th, td {{ border: 1px solid #CFC8DA; padding: 4pt 7pt; text-align: left; vertical-align: top; }}
  th {{ background: #F2E4FD; }}
  blockquote {{ border-left: 3px solid #8400D6; margin-left: 0; padding-left: 10pt; color: #4A4455; }}
  a {{ color: #8400D6; }}
</style></head>
<body>{body}</body></html>"""

Path(dst).write_text(page, encoding="utf-8")
PY

"$CHROME" \
  --headless \
  --disable-gpu \
  --no-pdf-header-footer \
  --print-to-pdf="$PDF_PATH" \
  "$HTML_TMP" >/dev/null 2>&1

rm -f "$HTML_TMP"

if [[ ! -s "$PDF_PATH" ]]; then
  echo "error: PDF was not produced: $PDF_PATH" >&2
  exit 1
fi

echo "wrote $PDF_PATH"
