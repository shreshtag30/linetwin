#!/usr/bin/env python3
"""Render a markdown document to PDF.

    uv run --with markdown python tools/mkpdf.py README.md
    uv run --with markdown python tools/mkpdf.py docs/SOLUTION_DESIGN.md

Replaces `tools/mkpdf.sh`, which hardcoded a macOS Chrome path
(`/Applications/Google Chrome.app/...`) and therefore could not run on the
machine this project is actually developed on. This finds a headless-capable
Chromium browser on macOS, Windows or Linux, and falls back to Edge -- which
is present on every Windows 11 install -- when Chrome is absent.

Headless Chromium `--print-to-pdf` remains the converter of choice for the
reason the shell script recorded: no LaTeX, weasyprint or wkhtmltopdf is
available here, and it is the only path confirmed to produce a valid PDF.

`markdown` is deliberately NOT added to the project's dependencies. It is a
documentation-rendering tool, not part of the server or the ml path, and this
project keeps that boundary strict (tests/test_server_import_hygiene.py).
Pass it transiently with `uv run --with markdown`.
"""

from __future__ import annotations

import argparse
import html
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BROWSER_CANDIDATES = [
    # Windows
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    # macOS
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]
BROWSER_ON_PATH = [
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "msedge",
]

CSS = """
  @page { margin: 18mm 16mm; }
  body {
    font-family: "Segoe UI", -apple-system, "Helvetica Neue", Arial, sans-serif;
    font-size: 10pt; line-height: 1.52; color: #17141C; max-width: 100%;
  }
  h1 { font-size: 19pt; border-bottom: 2px solid #8400D6; padding-bottom: 6pt;
       margin-bottom: 10pt; }
  h2 { font-size: 13.5pt; color: #6A00B8; margin-top: 20pt; border-bottom: 1px solid #E3D6F2;
       padding-bottom: 3pt; }
  h3 { font-size: 11.5pt; margin-top: 14pt; }
  code { background: #F4F1F7; padding: 1px 4px; border-radius: 3px;
         font-family: "Cascadia Mono", Consolas, monospace; font-size: 8.6pt; }
  pre { background: #F7F5FA; border: 1px solid #E3DCEC; border-radius: 4px; padding: 8pt;
        overflow-x: auto; }
  pre code { background: none; padding: 0; white-space: pre-wrap; word-break: break-word; }
  table { border-collapse: collapse; width: 100%; font-size: 8.4pt; margin: 9pt 0;
          page-break-inside: avoid; }
  th, td { border: 1px solid #CFC8DA; padding: 4pt 6pt; text-align: left; vertical-align: top; }
  th { background: #F2E4FD; font-weight: 600; }
  tr:nth-child(even) td { background: #FBF9FD; }
  blockquote { border-left: 3px solid #8400D6; margin-left: 0; padding: 2pt 0 2pt 10pt;
               color: #4A4455; background: #FAF6FE; }
  a { color: #8400D6; text-decoration: none; }
  hr { border: none; border-top: 1px solid #E3DCEC; margin: 16pt 0; }
  h1, h2, h3 { page-break-after: avoid; }
"""


def find_browser() -> str | None:
    for candidate in BROWSER_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return candidate
    for name in BROWSER_ON_PATH:
        found = shutil.which(name)
        if found:
            return found
    return None


def render_html(md_path: Path, out_html: Path) -> None:
    try:
        import markdown as md
    except ImportError:
        sys.exit(
            "error: the 'markdown' package is not available.\n"
            "  Run it transiently instead of installing it into the project:\n"
            f"      uv run --with markdown python tools/mkpdf.py {md_path}"
        )

    text = md_path.read_text(encoding="utf-8")
    body = md.markdown(text, extensions=["extra", "sane_lists", "tables", "toc"])
    title = html.escape(md_path.stem.replace("-", " ").replace("_", " ").title())
    out_html.write_text(
        f"<!doctype html>\n<html><head><meta charset='utf-8'><title>{title}</title>"
        f"<style>{CSS}</style></head><body>{body}</body></html>",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", help="path to the .md file")
    parser.add_argument("-o", "--out", help="output .pdf path (default: alongside the .md)")
    args = parser.parse_args()

    md_path = Path(args.markdown)
    if not md_path.is_file():
        sys.exit(f"error: not found: {md_path}")

    pdf_path = Path(args.out) if args.out else md_path.with_suffix(".pdf")
    browser = find_browser()
    if browser is None:
        sys.exit(
            "error: no headless-capable Chromium browser found.\n"
            "  Looked for Chrome and Edge in the standard locations and on PATH."
        )

    tmp_dir = Path(tempfile.mkdtemp(prefix="linetwin-pdf-"))
    tmp_html = tmp_dir / (md_path.stem + ".html")
    render_html(md_path, tmp_html)

    result = subprocess.run(
        [
            browser,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path.resolve()}",
            tmp_html.resolve().as_uri(),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    shutil.rmtree(tmp_dir, ignore_errors=True)

    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        sys.exit(
            f"error: PDF was not produced at {pdf_path}\n"
            f"  browser: {browser}\n  stderr: {result.stderr[:500]}"
        )

    kb = pdf_path.stat().st_size / 1024
    print(f"wrote {pdf_path}  ({kb:.0f} KB, via {Path(browser).name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
