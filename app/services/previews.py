"""Payer-facing previews of withheld evidence (payer_visibility=preview), derived from the original and stored apart
from it. The original never goes to the payer side before capture; a preview is the most it gets.

- PDF: the pages the payee picked (the first two by default, at most five), each stamped "Preview, released on
  payment" with the agreement and tranche. The pages are copied as they are, so their text can be selected; the
  watermark marks them, it doesn't lock them.
- Image: downscaled to PREVIEW_PX on the long side, re-encoded (dropping metadata) and watermarked.
- Spreadsheet: headers, row count and the columns the criteria's checks read, with their totals; no rows.
- Text and other files: no preview (the excerpt level already shows the verifier's quotes).

A preview that can't be made is recorded (`preview_failed`) and the evidence is kept without one.
"""

import csv
import io
from datetime import UTC, datetime

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas
from sqlmodel import Session

from app.db import record_audit
from app.domain.checks import _pick_column, _rows
from app.models import Evidence, EvidenceVisibility, Milestone, StoredFile
from app.services import files

MAX_PAGES = 5
DEFAULT_PAGES = (1, 2)
PREVIEW_PX = 800
_SHEETS = {"text/csv", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


class PreviewError(ValueError):
    pass


def parse_pages(spec: str | None) -> list[int] | None:
    """'1-2,5' -> [1, 2, 5]. None or blank: the default pages."""
    if not spec or not spec.strip():
        return None
    pages: list[int] = []
    for part in spec.replace(" ", "").split(","):
        lo, _, hi = part.partition("-")
        if not lo.isdigit() or (hi and not hi.isdigit()):
            raise PreviewError(f"preview pages look like '1-2,5'; got '{spec}'")
        a, b = int(lo), int(hi or lo)
        if a < 1 or b < a:
            raise PreviewError(f"preview pages look like '1-2,5'; got '{spec}'")
        pages += [p for p in range(a, b + 1) if p not in pages]
        if len(pages) > MAX_PAGES:
            raise PreviewError(f"a preview shows at most {MAX_PAGES} pages")
    return pages


def label(m: Milestone) -> str:
    return f"{m.agreement.title} · {m.title}"


def make(session: Session, m: Milestone, ev: Evidence, pages: list[int] | None = None) -> StoredFile | None:
    """Build and keep the preview for one withheld item, if its kind has one. Sets ev.preview_file_id."""
    f = ev.file
    if f is None or EvidenceVisibility(ev.payer_visibility) != EvidenceVisibility.preview:
        return None
    try:
        if f.media_type == "application/pdf":
            data, media, name = pdf_preview(files.read(f), pages, label(m)), "application/pdf", "preview.pdf"
        elif f.media_type.startswith("image/"):
            data, media, name = image_preview(files.read(f), label(m)), "image/jpeg", "preview.jpg"
        elif f.media_type in _SHEETS:
            data, media, name = sheet_preview(ev, m), "text/plain", "preview.txt"
        else:
            return None
    except PreviewError as e:
        detail = {"evidence_id": ev.id, "reason": str(e)}
        record_audit(session, m.agreement_id, ev.submitted_by, "preview_failed", milestone_id=m.id, detail=detail)
        return None
    row = files.save(session, data, filename=f"{ev.title[:180]} ({name})", media_type=media)
    ev.preview_file_id = row.id
    return row


# --- PDF -------------------------------------------------------------------------------


def _stamp(width: float, height: float, text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFillColor(Color(0.75, 0.1, 0.1, alpha=0.18))
    c.setFont("Helvetica-Bold", max(18, min(width, height) / 11))
    c.saveState()
    c.translate(width / 2, height / 2)
    c.rotate(35)
    c.drawCentredString(0, 0, "PREVIEW · released on payment")
    c.restoreState()
    c.setFillColor(Color(0.2, 0.2, 0.2, alpha=0.85))
    c.setFont("Helvetica", 7)
    c.drawString(18, 12, text[:160])
    c.save()
    return buf.getvalue()


def pdf_preview(data: bytes, pages: list[int] | None, text: str) -> bytes:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise PreviewError("the PDF is encrypted")
        total = len(reader.pages)
        chosen = [p for p in (pages or DEFAULT_PAGES) if p <= total][:MAX_PAGES] or [1]
        writer = PdfWriter()
        for n in chosen:
            page = writer.add_page(reader.pages[n - 1])
            w, h = float(page.mediabox.width), float(page.mediabox.height)
            note = f"{text} · page {n} of {total} · preview, not the deliverable"
            page.merge_page(PdfReader(io.BytesIO(_stamp(w, h, note))).pages[0])
        writer.add_metadata({"/Title": f"Preview: {text}", "/Producer": "Milestone Ledger preview"})
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except (PdfReadError, ValueError, KeyError) as e:
        if isinstance(e, PreviewError):
            raise
        raise PreviewError(f"couldn't read the PDF: {e}") from e


# --- images ----------------------------------------------------------------------------


def image_preview(data: bytes, text: str) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.width * im.height > 60_000_000:
                raise PreviewError("the image is too large to preview")
            im.thumbnail((PREVIEW_PX, PREVIEW_PX))
            base = im.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise PreviewError(f"couldn't read the image: {e}") from e
    w, h = base.size
    layer = Image.new("RGBA", (w * 2, h * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = ImageFont.load_default(size=max(14, min(w, h) // 14))
    step = max(80, min(w, h) // 3)
    for y in range(0, h * 2, step):
        for x in range(-w, w * 2, step * 4):
            xy = (x + (y // step % 2) * step * 2, y)
            draw.text(xy, "PREVIEW · released on payment", font=font, fill=(190, 25, 25, 70))
    layer = layer.rotate(30, center=(w, h)).crop((w // 2, h // 2, w // 2 + w, h // 2 + h))
    out = Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")
    ImageDraw.Draw(out).text((6, h - 14), text[:120], font=ImageFont.load_default(size=10), fill=(40, 40, 40))
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=70)  # no EXIF carried over
    return buf.getvalue()


# --- spreadsheets ----------------------------------------------------------------------


def sheet_preview(ev: Evidence, m: Milestone) -> bytes:
    rows = _rows(ev.content)
    headers = next(csv.reader(io.StringIO(ev.content.strip())), [])
    lines = [
        f"Preview of '{ev.title}' ({label(m)}): released on payment.",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC. Headers and totals only; the rows are withheld.",
        "",
        f"Rows: {len(rows)}",
        "Columns: " + ", ".join(h for h in headers if h),
    ]
    used = []
    for c in m.criteria:
        col = _pick_column(rows, c.measure.metric) if c.measure else None
        if col and col not in [u[0] for u in used]:
            values = [(r.get(col) or "").replace(",", "").replace("$", "").strip() for r in rows]
            nums = []
            for v in values:
                try:
                    nums.append(float(v))
                except ValueError:
                    continue
            used.append((col, len(nums), sum(nums), c.id))
    if used:
        lines += ["", "Columns the checks read:"]
        lines += [f"- {col} (criterion {cid}): {n} numeric values, total {total:,.2f}" for col, n, total, cid in used]
    return ("\n".join(lines) + "\n").encode()
