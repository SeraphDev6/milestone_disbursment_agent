"""Outgoing email: sign-in and invitation links, and notifications about tranches.

Three senders behind one `send`:
- ConsoleMailer: logs and keeps messages in memory (a local run reads links from the log; tests from memory).
- ResendMailer: Resend's HTTP API (EMAIL_PROVIDER=resend, RESEND_API_KEY).
- SmtpMailer: any SMTP server, STARTTLS when offered (EMAIL_PROVIDER=smtp, SMTP_*).

A failed send never fails the request that triggered it: the link or notice is logged and the caller carries on
(the audit log and outbound webhooks still record what happened).
"""

import logging
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from html import escape
from typing import Protocol

import httpx

log = logging.getLogger("mde.email")


def _ensure_visible() -> None:
    """Console mail is the only way to get sign-in and invitation links without a provider, but nothing configures
    the "mde" loggers under plain `uvicorn app.main:app` (Python then drops INFO). Give them a stderr handler
    unless the host's logging config already handles them."""
    root_handles = logging.getLogger().handlers
    mde = logging.getLogger("mde")
    if mde.handlers or root_handles:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))
    mde.addHandler(handler)
    mde.setLevel(logging.INFO)
    mde.propagate = False


@dataclass(frozen=True)
class SentMail:
    to: str
    subject: str
    body: str
    link: str
    tone: str = "neutral"


class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str, link: str, *, tone: str = "neutral") -> None: ...


# The web app's "Ledger & Stamp" look (web/DESIGN.md), in email terms: a paper page, an ink stamp naming what
# happened, ruled lines, one accent, 2px corners, no gradients or shadows. Colours are the Paper theme's hex
# values from web/src/design/themes.css. Everything is inline and table-based for mail clients.
PAPER = {
    "page": "#f6f1e7",
    "sheet": "#fdfbf6",
    "sunken": "#ede6da",
    "ink": "#2a1f17",
    "ink2": "#5d5044",
    "link": "#1e4b8d",
    "hairline": "#d3c9bb",
    "rule": "#463b32",
    "accent": "#18417c",
    "on_accent": "#fcfaf6",
}
# Stamp inks mean the same thing as in the app: settled, held (in progress), review (needs a person),
# alert (failed or disputed), neutral.
STAMPS = {
    "settled": ("#0a562b", "#ddf4e1"),
    "held": ("#194781", "#e1effd"),
    "review": ("#794900", "#fbe9c6"),
    "alert": ("#9b2018", "#ffe6e1"),
    "neutral": ("#514c46", "#ede9e2"),
}
SERIF = "Newsreader, 'Iowan Old Style', Georgia, 'Times New Roman', serif"
SANS = "'Atkinson Hyperlegible Next', 'Atkinson Hyperlegible', -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "'Atkinson Hyperlegible Mono', ui-monospace, Menlo, Consolas, monospace"
BUTTON = "Open in Milestone Ledger"


def split_subject(subject: str) -> tuple[str, str]:
    """Notification subjects are "What happened: 'tranche' (agreement)"; the first part is the stamp."""
    if ": '" in subject:
        stamp, rest = subject.split(": '", 1)
        return stamp, "'" + rest
    return subject, ""


def _paragraph_html(paragraph: str) -> str:
    lines = [ln for ln in paragraph.split("\n") if ln.strip()]
    items = [ln[2:].strip() for ln in lines if ln.startswith("- ")]
    if items:
        head = [ln for ln in lines if not ln.startswith("- ")]
        label = (
            f"<p style='margin:0 0 6px;font:600 15px/1.5 {SANS};color:{PAPER['ink']}'>{escape(' '.join(head))}</p>"
            if head
            else ""
        )
        rows = "".join(
            f"<tr><td valign=top style='padding:4px 10px 4px 0;font:14px/1.5 {MONO};color:{PAPER['ink2']}'>•</td>"
            f"<td style='padding:4px 0;font:15px/1.5 {SANS};color:{PAPER['ink']};border-bottom:1px solid "
            f"{PAPER['hairline']}'>{escape(item)}</td></tr>"
            for item in items
        )
        return f"{label}<table role=presentation cellpadding=0 cellspacing=0 style='margin:0 0 16px'>{rows}</table>"
    return f"<p style='margin:0 0 16px;font:16px/1.55 {SANS};color:{PAPER['ink']}'>{escape(' '.join(lines))}</p>"


def render(subject: str, body: str, link: str, tone: str = "neutral") -> tuple[str, str]:
    """Plain-text and HTML versions of one message. The body is plain text: blank lines separate paragraphs and
    lines starting with "- " make a list. The link, if any, becomes the button and is repeated as text."""
    text = f"{body}\n\n{link}\n" if link else f"{body}\n"
    stamp, headline = split_subject(subject)
    ink, surface = STAMPS.get(tone, STAMPS["neutral"])
    paragraphs = "".join(_paragraph_html(p) for p in body.split("\n\n") if p.strip())
    headline_html = (
        f"<h1 style='margin:14px 0 18px;font:400 26px/1.25 {SERIF};color:{PAPER['ink']}'>{escape(headline)}</h1>"
        if headline
        else "<div style='height:14px'></div>"
    )
    button = (
        f"<table role=presentation cellpadding=0 cellspacing=0 style='margin:8px 0 10px'><tr><td style='background:"
        f"{PAPER['accent']};border-radius:2px'><a href='{escape(link)}' style='display:inline-block;padding:11px 18px;"
        f"font:600 15px/1 {SANS};color:{PAPER['on_accent']};text-decoration:none'>{BUTTON}</a></td></tr></table>"
        f"<p style='margin:0 0 4px;font:12px/1.5 {SANS};color:{PAPER['ink2']}'>Or copy this link:</p>"
        f"<p style='margin:0;font:12px/1.5 {MONO};color:{PAPER['link']};word-break:break-all'>"
        f"<a href='{escape(link)}' style='color:{PAPER['link']}'>{escape(link)}</a></p>"
        if link
        else ""
    )
    html = (
        "<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        "<meta name=color-scheme content=light><meta name=supported-color-schemes content=light>"
        f"<title>{escape(subject)}</title></head>"
        f"<body style='margin:0;padding:0;background:{PAPER['page']}'>"
        f"<div style='display:none;max-height:0;overflow:hidden;color:{PAPER['page']}'>{escape(body[:120])}</div>"
        f"<table role=presentation width=100% cellpadding=0 cellspacing=0 style='background:{PAPER['page']}'>"
        "<tr><td align=center style='padding:28px 12px'>"
        f"<table role=presentation width=100% cellpadding=0 cellspacing=0 style='max-width:600px'>"
        # masthead: wordmark over a double rule (the ledger's closing rule)
        f"<tr><td style='padding:0 4px 10px;font:400 20px/1.2 {SERIF};color:{PAPER['ink']}'>Milestone Ledger"
        f"<span style='font:400 12px/1.2 {SANS};color:{PAPER['ink2']};letter-spacing:.08em;text-transform:uppercase;"
        "float:right;padding-top:5px'>Notice</span></td></tr>"
        f"<tr><td style='border-top:1px solid {PAPER['rule']};border-bottom:3px double {PAPER['rule']};height:2px'></td></tr>"
        # the sheet
        f"<tr><td style='padding:16px 0 0'><table role=presentation width=100% cellpadding=0 cellspacing=0 "
        f"style='background:{PAPER['sheet']};border:1px solid {PAPER['rule']};border-radius:2px'>"
        f"<tr><td style='padding:24px 28px 8px'>"
        # the stamp
        f"<span style='display:inline-block;padding:5px 10px 4px;border:2px solid {ink};border-radius:2px;"
        f"background:{surface};color:{ink};font:600 12px/1 {MONO};letter-spacing:.12em;text-transform:uppercase'>"
        f"{escape(stamp)}</span>"
        f"{headline_html}{paragraphs}{button}"
        "</td></tr>"
        f"<tr><td style='padding:14px 28px 18px;border-top:1px solid {PAPER['hairline']};font:12px/1.5 {SANS};"
        f"color:{PAPER['ink2']}'>Sent by Milestone Ledger about an agreement your organization is party to. "
        "Replies to this address aren't read; the people on the agreement are reachable from the link above."
        "</td></tr></table></td></tr>"
        "</table></td></tr></table></body></html>"
    )
    return text, html


@dataclass
class ConsoleMailer:
    sent: list[SentMail] = field(default_factory=list)

    def __post_init__(self) -> None:
        _ensure_visible()

    def send(self, to: str, subject: str, body: str, link: str, *, tone: str = "neutral") -> None:
        mail = SentMail(to=to, subject=subject, body=body, link=link, tone=tone)
        self.sent.append(mail)
        log.info("email to %s: %s (%s)", to, subject, link)


@dataclass
class ResendMailer:
    api_key: str
    sender: str
    timeout: float = 10.0

    def __post_init__(self) -> None:
        _ensure_visible()

    def send(self, to: str, subject: str, body: str, link: str, *, tone: str = "neutral") -> None:
        text, html = render(subject, body, link, tone)
        try:
            r = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"from": self.sender, "to": [to], "subject": subject, "text": text, "html": html},
                timeout=self.timeout,
            )
            if r.status_code >= 400:
                log.error("email to %s (%s) refused by Resend: HTTP %s %s", to, subject, r.status_code, r.text[:200])
            else:
                log.info("email to %s: %s (resend id %s)", to, subject, (r.json() or {}).get("id"))
        except httpx.HTTPError as e:
            log.error("email to %s (%s) not sent: %s", to, subject, e)


@dataclass
class SmtpMailer:
    host: str
    port: int
    sender: str
    username: str | None = None
    password: str | None = None
    timeout: float = 15.0

    def __post_init__(self) -> None:
        _ensure_visible()

    def send(self, to: str, subject: str, body: str, link: str, *, tone: str = "neutral") -> None:
        text, html = render(subject, body, link, tone)
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
                smtp.ehlo()
                if smtp.has_extn("starttls"):
                    smtp.starttls()
                    smtp.ehlo()
                if self.username and self.password:
                    smtp.login(self.username, self.password)
                smtp.send_message(msg)
        except (OSError, smtplib.SMTPException) as e:
            log.error("email to %s (%s) not sent: %s", to, subject, e)


@dataclass
class SkipUndeliverable:
    """Drops mail to the demo's people (addresses at DEMO_EMAIL_DOMAIN, where nothing can be delivered) before it
    reaches the sender behind it, so the demo doesn't fill the provider's log with bounces. create_app wraps the
    mailer in this when the demo is on. Anything else (a test mailer's `sent`) is read through to the sender."""

    inner: Mailer
    domain: str

    def send(self, to: str, subject: str, body: str, link: str, *, tone: str = "neutral") -> None:
        if to.lower().endswith("@" + self.domain):
            log.debug("email to %s dropped (demo address): %s", to, subject)
            return
        self.inner.send(to, subject, body, link, tone=tone)

    def __getattr__(self, name: str):
        return getattr(self.inner, name)


def make_mailer(settings) -> Mailer:
    if settings.email_provider == "resend":
        return ResendMailer(api_key=settings.resend_api_key.get_secret_value(), sender=settings.email_from)
    if settings.email_provider == "smtp":
        return SmtpMailer(
            host=settings.smtp_host,
            port=settings.smtp_port,
            sender=settings.email_from,
            username=settings.smtp_username,
            password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
        )
    _ensure_visible()
    log.warning("EMAIL_PROVIDER=console: sign-in links and notices are only logged here, not sent")
    return ConsoleMailer()
