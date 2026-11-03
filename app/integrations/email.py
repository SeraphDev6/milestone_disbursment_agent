"""Outgoing email: sign-in and invitation links, and notifications about tranches.

Three senders behind one `send`:
- ConsoleMailer: logs and keeps messages in memory (mock mode also returns the link in API responses).
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
    """Console mail is the only way to get sign-in and invitation links outside mock mode, but nothing configures
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


class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str, link: str) -> None: ...


def render(subject: str, body: str, link: str) -> tuple[str, str]:
    """Plain-text and HTML versions of one message. The body is plain text; the link is shown once at the end."""
    text = f"{body}\n\n{link}\n" if link else f"{body}\n"
    paragraphs = "".join(f"<p>{escape(p)}</p>" for p in body.split("\n\n") if p.strip())
    button = (
        f"<p><a href='{escape(link)}' style='display:inline-block;padding:.6rem 1rem;background:#1f3a5f;"
        f"color:#fff;text-decoration:none;border-radius:4px'>Open</a></p><p style='color:#666;font-size:.85rem'>"
        f"Or copy this link: {escape(link)}</p>"
        if link
        else ""
    )
    html = (
        "<!doctype html><meta charset=utf-8><body style='font-family:system-ui;max-width:36rem;margin:2rem auto;"
        f"line-height:1.5'><h1 style='font-size:1.2rem'>{escape(subject)}</h1>{paragraphs}{button}</body>"
    )
    return text, html


@dataclass
class ConsoleMailer:
    sent: list[SentMail] = field(default_factory=list)

    def __post_init__(self) -> None:
        _ensure_visible()

    def send(self, to: str, subject: str, body: str, link: str) -> None:
        mail = SentMail(to=to, subject=subject, body=body, link=link)
        self.sent.append(mail)
        log.info("email to %s: %s (%s)", to, subject, link)


@dataclass
class ResendMailer:
    api_key: str
    sender: str
    timeout: float = 10.0

    def __post_init__(self) -> None:
        _ensure_visible()

    def send(self, to: str, subject: str, body: str, link: str) -> None:
        text, html = render(subject, body, link)
        try:
            r = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"from": self.sender, "to": [to], "subject": subject, "text": text, "html": html},
                timeout=self.timeout,
            )
            if r.status_code >= 400:
                log.error("email to %s (%s) refused by Resend: HTTP %s %s", to, subject, r.status_code, r.text[:200])
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

    def send(self, to: str, subject: str, body: str, link: str) -> None:
        text, html = render(subject, body, link)
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
    if not settings.is_mock:
        _ensure_visible()
        log.warning("EMAIL_PROVIDER=console outside mock mode: sign-in links and notices are only logged, not sent")
    return ConsoleMailer()
