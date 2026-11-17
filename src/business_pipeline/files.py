"""File helpers: content hashing and type detection by magic bytes, not by extension."""

import hashlib
from pathlib import Path

from .models import Attachment

# Extensions that are never media, even inside a source folder.
IGNORED_NAMES = {".DS_Store", "_chat.txt"}
IGNORED_SUFFIXES = {".zip", ".tsv"}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_kind(path: Path) -> str:
    """WhatsApp and phones produce files without extensions or with misleading ones."""
    with path.open("rb") as handle:
        head = handle.read(16)
    if head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG"):
        return "image"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        return "image" if brand in {b"heic", b"heix", b"mif1", b"msf1"} else "video"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"BEGIN:VCARD"):
        return "contact"
    return "other"


def is_ignored(path: Path) -> bool:
    return path.name in IGNORED_NAMES or path.suffix.lower() in IGNORED_SUFFIXES


def make_attachment(path: Path, root: Path) -> Attachment:
    relative = path.relative_to(root).as_posix()
    if not path.is_file():
        return Attachment(path=relative, kind="missing", sha256=None)
    return Attachment(path=relative, kind=detect_kind(path), sha256=sha256_of(path))
