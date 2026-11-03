"""Outgoing email. Console only for now: messages are logged and kept in memory; in mock mode the API also
returns the link so sign-in and invitations work without an email provider (an SMTP/Resend sender plugs in
behind the same `send`)."""

import logging
from dataclasses import dataclass, field

log = logging.getLogger("mde.email")


@dataclass(frozen=True)
class SentMail:
    to: str
    subject: str
    body: str
    link: str


@dataclass
class ConsoleMailer:
    sent: list[SentMail] = field(default_factory=list)

    def send(self, to: str, subject: str, body: str, link: str) -> None:
        mail = SentMail(to=to, subject=subject, body=body, link=link)
        self.sent.append(mail)
        log.info("email to %s: %s (%s)", to, subject, link)
