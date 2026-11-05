"""Payer-facing previews of withheld evidence (#6)."""

import io
from datetime import timedelta

from PIL import Image
from pypdf import PdfReader
from reportlab.pdfgen import canvas
from sqlmodel import select

from app.db import utcnow
from app.models import AuditEvent, Evidence
from tests.helpers import h
from tests.test_verified_delivery import setup

SHEET = "date,workshop attendees,workshop spend\n2027-03-01,40,2500\n2027-03-08,45,3100.50\n"


def pdf(pages: int) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for n in range(1, pages + 1):
        c.drawString(72, 720, f"Final report page {n} SECRET-PAGE-{n}")
        c.showPage()
    c.save()
    return buf.getvalue()


def png(w: int, h_: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h_), (20, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def send(client, mid, ctx, name, data, media, visibility="preview", **form):
    return client.post(
        f"/milestones/{mid}/uploads",
        files={"files": (name, data, media)},
        data={"dated": "2027-09-01", "payer_visibility": visibility, **form},
        headers=h(ctx["payee"]),
    )


def first(client, mid, ctx, who="payer") -> dict:
    return client.get(f"/milestones/{mid}/evidence", headers=h(ctx[who])).json()[0]


def test_a_pdf_preview_shows_only_the_chosen_pages_watermarked(client, draft):
    _, m2, ctx = setup(client, draft)
    r = send(client, m2, ctx, "report.pdf", pdf(4), "application/pdf", preview_pages="2,4")
    assert r.status_code == 202, r.text
    ev = first(client, m2, ctx)
    assert ev["withheld"] and ev["has_preview"]
    assert client.get(f"/evidence/{ev['id']}/file", headers=h(ctx["payer"])).status_code == 403  # never the original

    got = client.get(f"/evidence/{ev['id']}/preview", headers=h(ctx["payer"]))
    assert got.status_code == 200 and got.headers["content-type"] == "application/pdf"
    pages = PdfReader(io.BytesIO(got.content)).pages
    text = [p.extract_text() for p in pages]
    assert len(pages) == 2 and "SECRET-PAGE-2" in text[0] and "SECRET-PAGE-4" in text[1]
    assert all("PREVIEW" in t and "page" in t and "not the deliverable" in t for t in text)
    assert "SECRET-PAGE-1" not in "".join(text)
    # the payee sees the very same preview
    same = client.get(f"/evidence/{ev['id']}/preview", headers=h(ctx["payee"]))
    assert same.content == got.content
    with client.app.state.sessionmaker() as s:
        q = select(AuditEvent).where(AuditEvent.milestone_id == m2, AuditEvent.action == "preview_downloaded")
        assert {e.detail["side"] for e in s.exec(q)} == {"payer", "payee"}


def test_no_preview_below_the_preview_level(client, draft):
    _, m2, ctx = setup(client, draft)
    assert send(client, m2, ctx, "report.pdf", pdf(2), "application/pdf", visibility="excerpt").status_code == 202
    ev = first(client, m2, ctx)
    assert not ev["has_preview"]
    assert client.get(f"/evidence/{ev['id']}/preview", headers=h(ctx["payer"])).status_code == 404


def test_an_image_preview_is_downscaled_and_re_encoded(client, draft):
    _, m2, ctx = setup(client, draft)
    original = png(2400, 1200)
    assert send(client, m2, ctx, "mockup.png", original, "image/png").status_code == 202
    ev = first(client, m2, ctx)
    got = client.get(f"/evidence/{ev['id']}/preview", headers=h(ctx["payer"]))
    im = Image.open(io.BytesIO(got.content))
    assert im.format == "JPEG" and max(im.size) <= 800 and im.size[0] == 2 * im.size[1]
    assert got.content != original and not im.info.get("exif")


def test_a_sheet_preview_has_headers_and_totals_but_no_rows(client, draft):
    _, _, ctx = setup(client, draft)
    m1 = ctx["m1"]
    assert send(client, m1, ctx, "spend.csv", SHEET.encode(), "text/csv").status_code == 202
    ev = first(client, m1, ctx)
    text = client.get(f"/evidence/{ev['id']}/preview", headers=h(ctx["payer"])).text
    assert "Rows: 2" in text and "workshop attendees" in text
    assert "workshop spend" in text and "total 5,600.50" in text
    assert "2027-03-08" not in text and "3100.50" not in text


def test_bad_preview_pages_are_refused(client, draft):
    _, m2, ctx = setup(client, draft)
    r = send(client, m2, ctx, "report.pdf", pdf(2), "application/pdf", preview_pages="1-9")
    assert r.status_code == 422 and "at most 5" in r.json()["detail"]


def test_a_preview_link_is_shown_to_the_payer_until_it_expires(client, draft):
    _, m2, ctx = setup(client, draft)
    body = {
        "evidence_type": "document",
        "title": "Hosted build",
        "content": "The checkout is deployed for review.",
        "preview_url": "https://preview.devco.dev/checkout",
    }
    r = client.post(f"/milestones/{m2}/evidence", json=body, headers=h(ctx["payee"]))
    assert r.status_code == 202, r.text
    ev = first(client, m2, ctx)
    assert ev["withheld"] and ev["excerpt"] is None  # hash_only: no content...
    assert ev["preview_url"] == body["preview_url"]  # ...but the link the payee chose to share
    assert ev["preview_expires_at"]  # 30 days by default
    with client.app.state.sessionmaker() as s:
        row = s.get(Evidence, ev["id"])
        row.preview_expires_at = utcnow() - timedelta(minutes=1)
        s.add(row)
        s.commit()
    assert first(client, m2, ctx)["preview_url"] is None
    bad = client.post(
        f"/milestones/{m2}/evidence", json=body | {"preview_url": "http://x.dev"}, headers=h(ctx["payee"])
    )
    assert bad.status_code == 422
