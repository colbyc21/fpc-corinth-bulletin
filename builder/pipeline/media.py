"""Photos for the Friday email, hosted on basecamp.

Uploads land in ``BUILDER_VAR/media`` as ``<sunday>-<sha>.jpg`` (or .png when
the image has transparency), resized to at most 1200 px wide, and are served
without login at ``/media/<name>`` through the admin hostname. Mail clients
fetch them from there, so the hostname must stay reachable without a gate.
"""
from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

from builder import config

MAX_WIDTH = 1200
NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[0-9a-f]{10}\.(jpg|png)$")
ALLOWED = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"}


def media_dir() -> Path:
    return config.VAR / "media"  # computed late: tests point VAR elsewhere


def public_url(name: str) -> str:
    return f"{config.PUBLIC_BASE_URL}/media/{name}"


def path_of(name: str) -> Path:
    if not NAME_RE.match(name):
        raise ValueError("bad media name")
    return media_dir() / name


def save_image(sunday: str, data: bytes, original_name: str = "") -> dict:
    """Normalise and store an uploaded image. Returns {src, alt, width, name}."""
    from PIL import Image, ImageOps

    suffix = Path(original_name).suffix.lower()
    if suffix and suffix not in ALLOWED:
        raise ValueError(f"{suffix} is not an image we can use (use JPG, PNG, GIF or WebP)")
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        raise ValueError("that file is not an image we can read") from None
    im = ImageOps.exif_transpose(im)
    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    if im.width > MAX_WIDTH:
        im = im.resize((MAX_WIDTH, round(im.height * MAX_WIDTH / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    if has_alpha:
        im.convert("RGBA").save(buf, "PNG", optimize=True)
        ext = "png"
    else:
        im.convert("RGB").save(buf, "JPEG", quality=85, optimize=True, progressive=True)
        ext = "jpg"
    out = buf.getvalue()
    name = f"{sunday}-{hashlib.sha1(out).hexdigest()[:10]}.{ext}"
    media_dir().mkdir(parents=True, exist_ok=True)
    (media_dir() / name).write_bytes(out)
    alt = re.sub(r"[-_]+", " ", Path(original_name).stem).strip() or "Photo"
    return {"src": public_url(name), "alt": alt[:120], "width": 520 if im.width >= im.height else 400, "name": name}


def week_media(sunday: str) -> list[str]:
    media_dir().mkdir(parents=True, exist_ok=True)
    return sorted(p.name for p in media_dir().glob(f"{sunday}-*") if NAME_RE.match(p.name))


def extract_notes_zip(data: bytes) -> tuple[bytes | None, str, list[tuple[str, bytes]]]:
    """A zipped .rtfd (Mail/TextEdit) holds TXT.rtf plus the pasted images.
    Returns (rtf bytes, rtf member name, [(image name, bytes)])."""
    import zipfile

    rtf, rtf_name, images = None, "", []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            base = Path(info.filename).name
            if info.is_dir() or base.startswith("._") or "__MACOSX" in info.filename or info.file_size > 25_000_000:
                continue
            suffix = Path(base).suffix.lower()
            if suffix == ".rtf" and (rtf is None or base == "TXT.rtf"):
                rtf, rtf_name = z.read(info), base
            elif suffix in ALLOWED:
                images.append((base, z.read(info)))
    return rtf, rtf_name, images
