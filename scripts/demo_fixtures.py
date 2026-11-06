"""Write the demo's documents to demo/: the grant agreement the engine extracts tranches from, and the evidence
the grantee submits against them. Real files, made the way a grantee would make them (a PDF report with its
tables, CSV exports of a sign-in sheet and a budget), so the demo runs the same extraction, upload, checks and
verifier as any real grant. Deterministic: running it again changes nothing unless the content changed.

    uv run python scripts/demo_fixtures.py

tests/test_demo.py checks the files against the demo grant's criteria; tests/evals/test_demo_eval.py runs them
through the real model.
"""

from __future__ import annotations

import csv
import io
import random
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, ByteStringObject
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo"

AGREEMENT = "Riverside Literacy Grant Agreement.pdf"
SOW = "Checkout redesign SOW.pdf"
SIGN_IN = "Spring workshop sign-in sheets (register).csv"
SIGN_IN_PDF = "Spring workshop sign-in sheets (signed).pdf"
BUDGET = "Spring workshop budget report.csv"
REPORT = "Riverside Reads final outcomes report.pdf"
REPORT_DRAFT = "Riverside Reads final outcomes report (draft).pdf"
THIN = "Workshop photos note.txt"

FUNDER = "Harbor Foundation"
GRANTEE = "Riverside Reads"
REVIEWER = "Sponsor Audit Partners"
GRANT_NO = "HF-2027-014"
ENROLLED = 87
ATTENDEES = 214
ACTUAL_SPEND = "9800.50"

# PDF metadata dates: the agreement's signing date and the report's cover date, so `dated_by` reads them.
AGREEMENT_DATE = "D:20270115100000+00'00'"
SOW_DATE = "D:20270201090000+00'00'"
SIGN_IN_DATE = "D:20270522163000+00'00'"  # the last workshop session
REPORT_DATE = "D:20270912090000+00'00'"


# --- styles ------------------------------------------------------------------------------


def _styles():
    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["BodyText"], fontName="Times-Roman", fontSize=10.5, leading=14)
    return {
        "title": ParagraphStyle("title", parent=ss["Title"], fontName="Times-Bold", fontSize=18, leading=22),
        "sub": ParagraphStyle("sub", parent=body, alignment=TA_CENTER, textColor=colors.HexColor("#444444")),
        "h": ParagraphStyle("h", parent=body, fontName="Times-Bold", fontSize=12, spaceBefore=10, spaceAfter=4),
        "body": body,
        "small": ParagraphStyle("small", parent=body, fontSize=9, leading=11, textColor=colors.HexColor("#444444")),
        "cell": ParagraphStyle("cell", parent=body, fontSize=9, leading=11),
    }


def _table(data, col_widths=None, header=True):
    t = Table(data, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("FONT", (0, 0), (-1, -1), "Times-Roman", 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style += [
            ("FONT", (0, 0), (-1, 0), "Times-Bold", 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
        ]
    t.setStyle(TableStyle(style))
    return t


def _pdf(story, title: str, author: str, date: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=inch,
        rightMargin=inch,
        topMargin=inch,
        bottomMargin=inch,
        title=title,
        author=author,
        invariant=1,  # no clock or randomness in the output
    )
    doc.build(story)
    # reportlab's invariant mode writes a fixed, meaningless date; set the document's own date instead.
    reader = PdfReader(io.BytesIO(buf.getvalue()))
    writer = PdfWriter(clone_from=reader)
    writer.add_metadata({"/Title": title, "/Author": author, "/CreationDate": date, "/ModDate": date})
    fixed = ByteStringObject(b"\x00" * 16)  # fixed file identifiers, so the bytes don't change between runs
    writer._ID = ArrayObject([fixed, fixed])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# --- the grant agreement -----------------------------------------------------------------


def grant_agreement() -> bytes:
    s = _styles()
    p = lambda text, style="body": Paragraph(text, s[style])
    story = [
        p("GRANT AGREEMENT", "title"),
        p(f"Grant No. {GRANT_NO} · Community Literacy Program · Agreement date: January 15, 2027", "sub"),
        Spacer(1, 14),
        p(
            f"This Grant Agreement (the “Agreement”) is entered into as of January 15, 2027 between <b>{FUNDER}</b>, a "
            f"Massachusetts nonprofit corporation with offices at 410 Harbor Street, Boston, MA 02210 (the “Funder”), "
            f"and <b>{GRANTEE}</b>, a Massachusetts nonprofit corporation with offices at 88 Mill Lane, Lowell, MA 01852 "
            f"(the “Grantee”)."
        ),
        p("1. Purpose", "h"),
        p(
            "The Funder awards this grant to support the Grantee's 2027 Riverside Reads community literacy program "
            "(the “Program”): a series of spring reading workshops for children in grades 2 through 5 and their "
            "families, followed by a summer tutoring cohort, as described in the Grantee's proposal dated "
            "November 20, 2026, which is incorporated by reference."
        ),
        p("2. Grant amount", "h"),
        p(
            "The total grant is <b>thirty thousand US dollars (USD 30,000.00)</b>, payable in two tranches as set out "
            "in Section 3. No other amounts are payable under this Agreement."
        ),
        p("3. Payment schedule and conditions of release", "h"),
        p(
            "<b>3.1 Tranche 1 — Spring workshops: USD 10,000.00.</b> Payable upon completion of the spring workshop "
            "series, scheduled for April and May 2027 and in any case no later than May 31, 2027. The Grantee will "
            "hold spring workshops reaching at least 200 participants. As evidence of completion the Grantee shall "
            "submit (a) the signed sign-in sheets for each workshop session, together with the attendance register "
            "exported from them as a spreadsheet, one row per attendee, and (b) a budget report showing actual "
            "spending per line item, demonstrating that workshop spending stayed within the USD 10,000.00 tranche "
            "budget."
        ),
        p(
            "<b>3.2 Tranche 2 — Outcomes report: USD 20,000.00.</b> Payable when the Program's final outcomes report "
            "meets both of the following conditions: the final report will include pre- and post-program reading "
            "assessments for every student enrolled in the Program, presented as an anonymized score table, and the "
            "report shall be submitted on or before September 30, 2027."
        ),
        p(
            "<b>3.3 Community impact.</b> The final report shall also demonstrate meaningful community impact and "
            "describe how the Program engaged families in the Riverside neighborhood."
        ),
        p(
            "<b>3.4 Release.</b> Each tranche is released automatically once the evidence submitted shows that "
            "tranche's conditions met; no further approval by the Funder is required. The Funder, or the independent "
            "reviewer named in Section 6, may examine any evidence and may hold a release only by stating which "
            "condition is not met. Payment is made to the Grantee's designated PayPal business account within five "
            "business days of release."
        ),
        p("4. Use of funds", "h"),
        p(
            "Grant funds shall be used solely for Program costs: workshop venues and materials, books for "
            "participants, facilitator fees, assessment tools, and reasonable program administration not exceeding "
            "ten percent of the grant. Funds may not be used for capital purchases, lobbying, or re-granting."
        ),
        p("5. Records, reporting and audit", "h"),
        p(
            "The Grantee shall keep complete records of Program activities and expenditures for four years after the "
            "final payment and shall make them available to the Funder or its reviewer on ten days' notice. "
            "Interim questions about any submission will be answered within ten business days."
        ),
        p("6. Independent reviewer", "h"),
        p(
            f"The Funder has engaged <b>{REVIEWER}</b> as an independent reviewer. The reviewer may examine any evidence "
            "submitted under Section 3 and may approve or reject a release on the Funder's behalf."
        ),
        p("7. Term and termination", "h"),
        p(
            "This Agreement runs from the agreement date until December 31, 2027. Either party may terminate it on "
            "thirty days' written notice; funds already released for conditions met are not recoverable, and "
            "unreleased tranches lapse on termination."
        ),
        p("8. General", "h"),
        p(
            "This Agreement is the entire agreement between the parties on its subject and is governed by the laws "
            "of the Commonwealth of Massachusetts. Amendments must be in writing and signed by both parties."
        ),
        Spacer(1, 24),
        _table(
            [
                [f"For {FUNDER}", f"For {GRANTEE}"],
                [
                    "Dana Whitfield\nSenior Program Officer\nDate: January 15, 2027",
                    "Marcus Okafor\nExecutive Director\nDate: January 15, 2027",
                ],
            ],
            col_widths=[3.1 * inch, 3.1 * inch],
        ),
    ]
    return _pdf(story, f"Grant Agreement {GRANT_NO}", FUNDER, AGREEMENT_DATE)


# --- the SOW (the "same engine" beat: a contractor paid by invoice when a labelled PR merges with CI green) ---------

CLIENT = "Acme Retail"
CONTRACTOR = "Dev Co"


def sow_agreement() -> bytes:
    s = _styles()
    p = lambda text, style="body": Paragraph(text, s[style])
    story = [
        p("STATEMENT OF WORK", "title"),
        p("SOW No. AR-2027-03 · Checkout redesign · Effective February 1, 2027", "sub"),
        Spacer(1, 14),
        p(
            f"This Statement of Work (the “SOW”) is made under the Master Services Agreement dated January 10, 2027 "
            f"between <b>{CLIENT}</b>, Inc., 1200 Commerce Way, Austin, TX 78701 (the “Client”), and <b>{CONTRACTOR}</b> LLC, "
            f"55 Foundry Street, Portland, OR 97209 (the “Contractor”)."
        ),
        p("1. Scope", "h"),
        p(
            "The Contractor will redesign the checkout of the Client's web store (repository acme-retail/storefront on "
            "GitHub): a new payment form and a new order confirmation flow. Work is delivered as pull requests into the "
            "Client's repository, reviewed and merged by the Client's engineers."
        ),
        p("2. Fees", "h"),
        p(
            "The total fee is <b>six thousand US dollars (USD 6,000.00)</b>, fixed price, invoiced per milestone as set out "
            "in Section 3. Invoices are payable within 7 days of issue by PayPal to the Contractor's business account."
        ),
        p("3. Milestones and acceptance", "h"),
        p(
            "<b>3.1 Milestone 1 — Payment form: USD 3,500.00.</b> Due March 15, 2027. Accepted, and the invoice issued, "
            "when the payment form pull request is merged into the main branch of the Client's repository with every "
            "continuous-integration check passing on the merged commit."
        ),
        p(
            "<b>3.2 Milestone 2 — Order confirmation: USD 2,500.00.</b> Due April 15, 2027. Accepted, and the invoice "
            "issued, when the order confirmation pull request (confirmation page and email) is merged into the main "
            "branch of the Client's repository with every continuous-integration check passing on the merged commit."
        ),
        p(
            "<b>3.3 Acceptance is automatic</b> on the merged commit's CI results; no further sign-off by the Client is "
            "required. A pull request merged with failing or missing checks is not accepted."
        ),
        p("4. Change requests", "h"),
        p(
            "Changes to scope are agreed in writing as a new milestone under this SOW or a new SOW; they never alter "
            "the acceptance condition of a milestone already in progress."
        ),
        p("5. Intellectual property", "h"),
        p(
            "Code merged into the Client's repository is the Client's property on payment of the milestone it belongs "
            "to. Until then the Contractor grants the Client a licence to review and test it."
        ),
        p("6. Term", "h"),
        p(
            "This SOW ends when both milestones are paid, or on 30 days' written notice by either party for unfinished work."
        ),
        Spacer(1, 24),
        _table(
            [
                [f"For {CLIENT}", f"For {CONTRACTOR}"],
                [
                    "Priya Raman\nHead of Engineering\nDate: February 1, 2027",
                    "Jonah Whitaker\nPrincipal\nDate: February 1, 2027",
                ],
            ],
            col_widths=[3.1 * inch, 3.1 * inch],
        ),
    ]
    return _pdf(story, "Statement of Work AR-2027-03: checkout redesign", CLIENT, SOW_DATE)


# --- the grantee's evidence --------------------------------------------------------------

FIRST = [
    "Ava",
    "Liam",
    "Noah",
    "Emma",
    "Olivia",
    "Mateo",
    "Sofia",
    "Elijah",
    "Mia",
    "Lucas",
    "Amelia",
    "Ethan",
    "Zoe",
    "Isaiah",
    "Layla",
    "Omar",
    "Nora",
    "Caleb",
    "Priya",
    "Aiden",
    "Grace",
    "Jonah",
    "Isla",
    "Leo",
    "Maya",
    "Theo",
    "Aria",
    "Diego",
    "Hannah",
    "Owen",
    "Chloe",
    "Ezra",
    "Lily",
    "Micah",
    "Ruby",
    "Samuel",
    "Elena",
    "Jude",
    "Camila",
    "Felix",
    "Rosa",
    "Ian",
    "Naomi",
    "Tariq",
    "Ella",
    "Hugo",
    "Ivy",
    "Adam",
    "Lucia",
    "Kai",
    "Imani",
    "Rafael",
    "Jade",
    "Nico",
    "Sadie",
    "Yusuf",
    "Mila",
    "Beck",
    "Anya",
    "Marcus",
    "Tessa",
    "Arjun",
    "Clara",
    "Dev",
    "Hazel",
    "Ravi",
    "Nina",
    "Asher",
    "Lena",
    "Milo",
    "Esme",
    "Rhys",
    "Thea",
    "Jace",
    "Faye",
    "Quinn",
]
LAST = [
    "Nguyen",
    "Rivera",
    "Patel",
    "Johnson",
    "Okafor",
    "Silva",
    "Kim",
    "Brown",
    "Hernandez",
    "Cohen",
    "Ali",
    "Walsh",
    "Garcia",
    "Lee",
    "Murphy",
    "Santos",
    "Chen",
    "Reyes",
    "Khan",
    "Ortiz",
    "Dubois",
    "Mensah",
    "Park",
    "Ramos",
    "Fischer",
    "Byrne",
    "Castillo",
    "Haddad",
    "Lopez",
    "Novak",
    "Adeyemi",
    "Quinn",
    "Moreau",
    "Torres",
    "Ibrahim",
    "Flores",
    "Shah",
    "Larsen",
    "Vega",
    "Osei",
]
SESSIONS = [
    ("2027-04-10", "Session 1: Story Saturdays kickoff", "Lowell Public Library, Pollard branch"),
    ("2027-04-24", "Session 2: Reading with families", "Lowell Public Library, Pollard branch"),
    ("2027-05-08", "Session 3: Books and bridges", "Riverside Community Center"),
    ("2027-05-22", "Session 4: Spring reading celebration", "Riverside Community Center"),
]
BUDGET_LINES = [
    ("Venue rental", "Facilities", "1500.00", "1450.00", "Riverside Community Center", "Two Saturday sessions"),
    ("Books for participants", "Materials", "3200.00", "3148.75", "Porter Square Books", "4 titles, 240 copies"),
    ("Facilitator fees", "Personnel", "2800.00", "2800.00", "Program staff", "2 facilitators x 4 sessions"),
    ("Reading assessments", "Materials", "900.00", "875.00", "Literacy Assessment Co.", "Pre/post kits"),
    ("Workshop supplies", "Materials", "600.00", "612.30", "Staples", "Name tags, paper, markers"),
    ("Family snacks", "Hospitality", "400.00", "389.45", "Market Basket", "4 sessions"),
    ("Program administration", "Admin", "600.00", "525.00", "Program staff", "Within the 10% cap"),
]


PER_SESSION = [52, 55, 50, 57]  # 214 attendees in all
AGE_GROUPS = ["child (grades 2-3)", "child (grades 4-5)", "parent/guardian"]


def _attendance() -> list[tuple[str, str, str, str, str]]:
    """(session date, session, location, attendee, age group) for every sign-in row, in session order."""
    rng = random.Random(2027)
    assert sum(PER_SESSION) == ATTENDEES
    rows = []
    for (day, title, where), n in zip(SESSIONS, PER_SESSION, strict=True):
        seen: set[str] = set()
        while len(seen) < n:
            seen.add(f"{rng.choice(FIRST)} {rng.choice(LAST)}")
        rows += [(day, title, where, name, rng.choice(AGE_GROUPS)) for name in sorted(seen)]
    return rows


def sign_in_sheet() -> str:
    """The attendance register: the signed sheets typed up, one row per attendee."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["session_date", "session", "location", "attendee", "age_group", "signed"])
    for row in _attendance():
        w.writerow([*row, "yes"])
    return buf.getvalue()


def sign_in_pdf() -> bytes:
    """The signed sign-in sheets themselves: one sheet per session, a signature column on every row."""
    s = _styles()
    story = []
    for (day, title, where), n in zip(SESSIONS, PER_SESSION, strict=True):
        rows = [r for r in _attendance() if r[0] == day]
        assert len(rows) == n
        story += [
            Paragraph(f"{GRANTEE} — Workshop sign-in sheet", s["title"]),
            Paragraph(f"{title} · {day} · {where} · Grant {GRANT_NO}", s["sub"]),
            Spacer(1, 8),
            Paragraph(
                "Please sign in. By signing you confirm you attended this session. One row per attendee.", s["small"]
            ),
            Spacer(1, 6),
            _table(
                [["#", "Name", "Age group", "Signature"]]
                + [[str(i), name, group, "signed"] for i, (_, _, _, name, group) in enumerate(rows, start=1)]
                + [["", f"Attendees signed in: {n}", "", f"Facilitator: M. Okafor, {day}"]],
                col_widths=[0.4 * inch, 2.6 * inch, 1.6 * inch, 1.9 * inch],
            ),
            PageBreak(),
        ]
    story.pop()  # no trailing blank page
    return _pdf(story, f"{GRANTEE} workshop sign-in sheets, spring 2027", GRANTEE, SIGN_IN_DATE)


def budget_sheet() -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["line_item", "category", "budgeted_usd", "actual_spend_usd", "vendor", "notes"])
    for row in BUDGET_LINES:
        w.writerow(row)
    assert f"{sum(float(r[3]) for r in BUDGET_LINES):.2f}" == ACTUAL_SPEND
    return buf.getvalue()


def _scores() -> list[tuple[str, str, str, int, int | None]]:
    rng = random.Random(87)
    rows = []
    for i in range(1, ENROLLED + 1):
        grade = rng.choice(["2", "3", "4", "5"])
        section = "A" if i <= 44 else "B"
        pre = rng.randint(28, 71)
        post = min(100, pre + rng.randint(2, 19))
        rows.append((f"R-{i:03d}", grade, section, pre, post))
    return rows


def outcomes_report(draft: bool = False) -> bytes:
    s = _styles()
    p = lambda text, style="body": Paragraph(text, s[style])
    scores = _scores()
    gains = [post - pre for _, _, _, pre, post in scores]
    mean_pre = sum(r[3] for r in scores) / ENROLLED
    mean_post = sum(r[4] for r in scores) / ENROLLED
    improved = sum(1 for g in gains if g > 0)
    title = f"{GRANTEE} — Final Outcomes Report" + (" (DRAFT)" if draft else "")

    score_rows = [["Student ID", "Grade", "Section", "Pre-program score", "Post-program score", "Change"]]
    for sid, grade, section, pre, post in scores:
        if draft and section == "B":
            score_rows.append([sid, grade, section, str(pre), "TODO", "—"])
        else:
            score_rows.append([sid, grade, section, str(pre), str(post), f"+{post - pre}"])

    story = [
        p(title, "title"),
        p(f"2027 Community Literacy Program · Grant No. {GRANT_NO} · Prepared for {FUNDER}", "sub"),
        p("Report date: September 12, 2027" + (" · Draft: not for submission" if draft else ""), "sub"),
        Spacer(1, 14),
        p("1. Summary", "h"),
        p(
            f"Between April and August 2027, {GRANTEE} delivered the Riverside Reads program funded under Grant "
            f"{GRANT_NO}: four spring reading workshops attended by {ATTENDEES} children and family members, followed "
            f"by an eight-week summer tutoring cohort of {ENROLLED} enrolled students in grades 2 through 5. Every "
            f"enrolled student completed a pre-program and a post-program reading assessment (Table 2). The mean "
            f"score rose from {mean_pre:.1f} to {mean_post:.1f} on the 100-point assessment, and {improved} of "
            f"{ENROLLED} students improved."
        ),
        p("2. Program delivery", "h"),
        p(
            "The spring workshop series ran on four Saturdays at the Pollard branch of the Lowell Public Library "
            "and the Riverside Community Center. Attendance was recorded on signed sign-in sheets, submitted with "
            "the Tranche 1 evidence in June. The summer cohort met twice a week from June 21 to August 13, 2027, "
            "in groups of five to seven students with one trained tutor each."
        ),
        p("Table 1. Spring workshop attendance by session", "small"),
        _table(
            [["Date", "Session", "Location", "Attendees"]]
            + [[d, t, w, str(n)] for (d, t, w), n in zip(SESSIONS, PER_SESSION, strict=True)]
            + [["", "Total", "", str(ATTENDEES)]],
            col_widths=[0.9 * inch, 2.5 * inch, 2.4 * inch, 0.8 * inch],
        ),
        Spacer(1, 10),
        p("3. Reading outcomes", "h"),
        p(
            "Students were assessed with the Riverside Reading Inventory, a 100-point measure of decoding, fluency "
            "and comprehension, in the first and last week of the summer cohort. Table 2 lists both scores for all "
            f"{ENROLLED} enrolled students. Student identifiers are anonymized; the key is held by the Grantee and "
            "available to the reviewer on request."
            + (" <b>Post-program scores for section B are still being entered: TODO.</b>" if draft else "")
        ),
        p(f"Table 2. Pre- and post-program reading assessment scores, all {ENROLLED} enrolled students", "small"),
        _table(score_rows, col_widths=[1.0 * inch, 0.7 * inch, 0.8 * inch, 1.4 * inch, 1.4 * inch, 0.8 * inch]),
        PageBreak(),
        p("4. Community engagement and impact", "h"),
        p(
            "Families attended each workshop with their children; 61 parents and guardians took part in the "
            "family reading sessions, and 38 households borrowed books through the program's lending shelf at the "
            "community center. Two parent volunteers trained as reading buddies and continue with the fall cohort. "
            "The program's impact on the Riverside neighborhood: 240 books now in participants' homes, a weekly "
            "family reading hour the community center has kept on its schedule, and the reading gains in Table 2."
        ),
        p("5. Budget", "h"),
        p(
            f"Workshop spending for Tranche 1 totaled USD {ACTUAL_SPEND} against the USD 10,000.00 tranche budget, as "
            "reported in the spring workshop budget report. Tranche 2 costs (tutor stipends, assessment kits and books for the "
            "summer cohort) are itemized in the attached final budget."
        ),
        p("6. Attestation", "h"),
        p(
            f"I attest that the information in this report is accurate and complete to the best of my knowledge.<br/>"
            f"Marcus Okafor, Executive Director, {GRANTEE} · September 12, 2027"
        ),
    ]
    return _pdf(story, title, GRANTEE, REPORT_DATE)


THIN_NOTE = (
    "Hi! Just a quick note on the spring workshops. They went really well and lots of people came. Everyone was "
    "engaged and the kids loved the books. We took some photos which we can share later. Thanks for your support!\n"
    "- Marcus\n"
)


def write_all(out: Path = OUT) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    files = {
        AGREEMENT: grant_agreement(),
        SOW: sow_agreement(),
        SIGN_IN: sign_in_sheet().encode(),
        SIGN_IN_PDF: sign_in_pdf(),
        BUDGET: budget_sheet().encode(),
        REPORT: outcomes_report(),
        REPORT_DRAFT: outcomes_report(draft=True),
        THIN: THIN_NOTE.encode(),
    }
    written = []
    for name, data in files.items():
        path = out / name
        if not path.exists() or path.read_bytes() != data:
            path.write_bytes(data)
            written.append(path)
    return written


if __name__ == "__main__":
    changed = write_all()
    for path in changed:
        print(f"wrote {path.relative_to(ROOT)}")
    if not changed:
        print("demo/ is up to date")
    sys.exit(0)
