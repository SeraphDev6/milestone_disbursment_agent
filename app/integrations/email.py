"""Outgoing email. Console only for now: messages are logged and kept in memory; in mock mode the API also
returns the link so sign-in and invitations work without an email provider (an SMTP/Resend sender plugs in
behind the same `send`)."""

import logging
from dataclasses import dataclass, field

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


@dataclass
class ConsoleMailer:
    sent: list[SentMail] = field(default_factory=list)

    def __post_init__(self) -> None:
        _ensure_visible()

    def send(self, to: str, subject: str, body: str, link: str) -> None:
        mail = SentMail(to=to, subject=subject, body=body, link=link)
        self.sent.append(mail)
        log.info("email to %s: %s (%s)", to, subject, link)
