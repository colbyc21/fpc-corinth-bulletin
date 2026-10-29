"""Deterministic text extraction from the weekly inputs.

* ``docx_text`` keeps paragraph breaks and tabs. Tabs matter: the Choral
  Introit and anthem titles are tab-aligned on the right in the liturgy
  docx and sometimes land on the previous line.
* ``rtf_text`` handles the office's content form when it still arrives as
  RTF (TextEdit output).

Uploaded files are untrusted. Nothing here executes or renders them; the
docx is opened as a zip and only ``word/document.xml`` is read as text.
"""
from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path

MAX_DOCX_BYTES = 10 * 1024 * 1024
MAX_XML_BYTES = 50 * 1024 * 1024

_TAG = re.compile(r"<[^>]+>")


def docx_text(path: str | Path) -> str:
    path = Path(path)
    if path.stat().st_size > MAX_DOCX_BYTES:
        raise ValueError("docx larger than 10 MB")
    with zipfile.ZipFile(path) as zf:
        info = zf.getinfo("word/document.xml")
        if info.file_size > MAX_XML_BYTES:
            raise ValueError("document.xml unexpectedly large")
        xml = zf.read(info).decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    xml = re.sub(r"<w:br[^>]*/>", "\n", xml)
    xml = re.sub(r"<w:cr/>", "\n", xml)
    text = _TAG.sub("", xml)
    text = html.unescape(text)
    lines = [ln.rstrip() for ln in text.split("\n")]
    return "\n".join(lines).strip() + "\n"


def rtf_text(path: str | Path) -> str:
    from striprtf.striprtf import rtf_to_text

    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    text = rtf_to_text(raw, errors="ignore")
    return text.replace("\xa0", " ").strip() + "\n"


def is_docx(path: str | Path) -> bool:
    """Content check for uploads: a zip that contains word/document.xml."""
    try:
        with zipfile.ZipFile(path) as zf:
            return "word/document.xml" in zf.namelist()
    except zipfile.BadZipFile:
        return False
