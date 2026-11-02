"""Extension allow list plus positive signatures; MIME headers are untrusted."""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from agentic_graphrag.core.ports.parsing import UnsupportedDocumentType

TEXT_MIMES = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
}
OFFICE_MIMES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
IMAGE_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}
MINERU_MIMES = {".pdf": "application/pdf", **OFFICE_MIMES, **IMAGE_MIMES}
SUPPORTED_EXTENSIONS = frozenset(TEXT_MIMES | MINERU_MIMES)
_SIGNATURES = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"BM", "image/bmp"),
    (b"MZ", "application/x-executable"),
    (b"\x7fELF", "application/x-executable"),
    (b"\xcf\xfa\xed\xfe", "application/x-executable"),
    (b"\xd0\xcf\x11\xe0", "application/x-ole-storage"),
    (b"\x1f\x8b", "application/gzip"),
    (b"Rar!", "application/vnd.rar"),
    (b"7z\xbc\xaf\x27\x1c", "application/x-7z-compressed"),
)
_OFFICE_PARTS = {
    ".docx": "word/document.xml",
    ".xlsx": "xl/workbook.xml",
    ".pptx": "ppt/presentation.xml",
}
_HTML = re.compile(
    rb"<(?:!doctype\s+html|html|head|body|main|article|section|h[1-6]|p|table|div|"
    rb"ul|ol|li|pre|blockquote)(?:\s|>)",
    re.I,
)
SNIFF_BYTES = 8192
_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def validate_filename(filename: str) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        shown = extension or "(no extension)"
        raise UnsupportedDocumentType(f"Unsupported file type: {shown}")
    return extension


def sniff_mime(content: bytes, *, filename: str) -> str:
    extension = validate_filename(filename)
    detected = _binary_mime(content)
    if extension in MINERU_MIMES:
        if detected != MINERU_MIMES[extension]:
            raise UnsupportedDocumentType(f"File signature does not match type {extension}")
        return detected
    if detected:
        raise UnsupportedDocumentType(f"Binary file signature does not match type {extension}")
    # Ignore UTF-16/32 padding for the HTML sniff; the decoder validates it later.
    head = content[:SNIFF_BYTES].replace(b"\x00", b"")
    is_html = (
        _HTML.search(head)
        if extension in {".html", ".htm"}
        else _HTML.match(head.lstrip(b"\xef\xbb\xbf\xff\xfe \t\r\n"))
    )
    if is_html:
        return "text/html"
    if TEXT_MIMES[extension] == "text/html":
        raise UnsupportedDocumentType("File signature does not contain HTML markup")
    return TEXT_MIMES[extension]


def _binary_mime(content: bytes) -> str | None:
    for signature, mime in _SIGNATURES:
        if content.startswith(signature):
            return mime
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith(_ZIP_SIGNATURES):
        return _zip_mime(content)
    return None


def _zip_mime(content: bytes) -> str:
    """Inspect ZIP member names only; never extract or inflate untrusted archives."""
    try:
        with ZipFile(BytesIO(content)) as archive:
            members = set(archive.namelist())
    except BadZipFile as exc:
        raise UnsupportedDocumentType("Invalid ZIP/Office file signature") from exc
    formats = [ext for ext, part in _OFFICE_PARTS.items() if part in members]
    if "[Content_Types].xml" in members and len(formats) == 1:
        return OFFICE_MIMES[formats[0]]
    return "application/zip"
