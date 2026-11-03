"""Upload adapter: files a payee attaches (PDF, CSV, XLSX, text, images) -> evidence.

Spreadsheets become CSV text so deterministic checks can read them. PDFs get their text
extracted for the checks, and the original is kept so the verifier can read tables and figures.
"""

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePath

from openpyxl import load_workbook
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.domain.models import EvidenceIn, EvidenceType

MAX_CONTENT_CHARS = 200_000  # EvidenceIn limit; longer text is cut with an explicit marker, original kept
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
SUPPORTED = {".pdf", ".csv", ".xlsx", ".txt", ".md", *IMAGE_TYPES}


class UploadError(ValueError):
    pass


@dataclass
class ParsedUpload:
    evidence: EvidenceIn
    data: bytes  # original bytes, stored and linked to the evidence
    media_type: str
    sha256: str


def _fit(text: str) -> str:
    text = text.strip() or "(no extractable text)"
    if len(text) <= MAX_CONTENT_CHARS:
        return text
    marker = "\n\n[Text truncated for automatic checks; the full original file is attached.]"
    return text[: MAX_CONTENT_CHARS - len(marker)] + marker


def _with_note(text: str, note: str | None) -> str:
    return f"{text}\n\nPayee note (written by the payee, not verified): {note}" if note else text


def _pdf(data: bytes) -> tuple[str, date | None]:
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [f"[page {i}]\n{(p.extract_text() or '').strip()}" for i, p in enumerate(reader.pages, start=1)]
        created = reader.metadata.creation_date if reader.metadata else None
    except (PdfReadError, ValueError, KeyError) as e:
        raise UploadError(f"couldn't read PDF: {e}") from e
    return "\n\n".join(pages), created.date() if isinstance(created, datetime) else None


def _xlsx_sheets(data: bytes) -> list[tuple[str, str]]:
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # openpyxl raises a variety of zip/xml errors
        raise UploadError(f"couldn't read spreadsheet: {e}") from e
    sheets = []
    for ws in wb.worksheets:
        buf = io.StringIO()
        writer = csv.writer(buf)
        rows = 0
        for row in ws.iter_rows(values_only=True):
            if row and any(v not in (None, "") for v in row):
                writer.writerow(["" if v is None else v for v in row])
                rows += 1
        if rows:
            sheets.append((ws.title, buf.getvalue()))
    wb.close()
    return sheets


def parse_upload(filename: str, data: bytes, note: str | None, dated: date | None) -> list[ParsedUpload]:
    """One file -> one or more evidence items (an XLSX gives one per non-empty sheet)."""
    ext = PurePath(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise UploadError(f"'{filename}': upload PDF, CSV, XLSX, TXT, MD or an image (got '{ext or 'no extension'}')")
    if not data:
        raise UploadError(f"'{filename}' is empty")
    digest = hashlib.sha256(data).hexdigest()

    def item(kind: EvidenceType, title: str, text: str, media_type: str, when: date | None) -> ParsedUpload:
        ev = EvidenceIn(
            evidence_type=kind,
            title=title[:200],
            content=_with_note(_fit(text), note),
            external_ref=f"upload:{digest}:{title}"[:500],
            dated=when,
        )
        return ParsedUpload(evidence=ev, data=data, media_type=media_type, sha256=digest)

    if ext == ".pdf":
        text, created = _pdf(data)
        return [item(EvidenceType.document, filename, text, "application/pdf", dated or created)]
    if ext == ".csv":
        return [item(EvidenceType.spreadsheet, filename, data.decode("utf-8-sig", errors="replace"), "text/csv", dated)]
    if ext == ".xlsx":
        sheets = _xlsx_sheets(data)
        if not sheets:
            raise UploadError(f"'{filename}' has no non-empty sheets")
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return [
            item(EvidenceType.spreadsheet, f"{filename} - {name}" if len(sheets) > 1 else filename, text, media, dated)
            for name, text in sheets
        ]
    if ext in IMAGE_TYPES:
        return [item(EvidenceType.photo, filename, f"Photo '{filename}'.", IMAGE_TYPES[ext], dated)]
    return [item(EvidenceType.document, filename, data.decode("utf-8", errors="replace"), "text/plain", dated)]
