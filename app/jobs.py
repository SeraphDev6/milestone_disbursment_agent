"""Scheduled jobs. Run as `python -m app.jobs <job>` from Render cron or any scheduler.

migrate           apply pending migrations and exit (run before starting several app instances)
daily             drop expired PayPal holds, close invoices unpaid past the timeout, mark tranches overdue
payouts           pay tranches whose dispute window closed and follow payouts in flight (hourly)
deliver-webhooks  send due outbound webhooks once (when the in-process loop is turned off)
"""

import logging
import sys

from app.api.spa import has_build
from app.config import get_settings
from app.db import make_engine, make_sessionmaker, migrate, utcnow
from app.integrations.email import make_mailer
from app.integrations.paypal import make_gateway
from app.services.notifications import NOTIFIER, Notifier
from app.services.outbound import deliver_due
from app.services.settlement import disburse_due, expire_stale_holds, expire_stale_invoices
from app.services.verification import mark_overdue

JOBS = ("migrate", "daily", "payouts", "deliver-webhooks")


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in JOBS:
        print(f"usage: python -m app.jobs {{{'|'.join(JOBS)}}}", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(name)s: %(message)s")
    settings = get_settings()
    engine = make_engine(settings.database_url)
    migrate.upgrade(engine)
    if argv[0] == "migrate":
        print("database is at the latest migration")
        return 0
    built = has_build() if settings.web_app_built is None else settings.web_app_built
    app_url = settings.public_app_url or (settings.public_base_url if built else None)
    notifier = Notifier(make_mailer(settings), settings.api_base_url, app_url)
    with make_sessionmaker(engine, info={NOTIFIER: notifier})() as session:
        now = utcnow()
        if argv[0] == "daily":
            holds = expire_stale_holds(session, now)
            invoices = expire_stale_invoices(session, make_gateway(settings), now)
            overdue = mark_overdue(session, now)
            print(f"expired {holds} hold(s), closed {invoices} unpaid invoice(s), marked {overdue} tranche(s) overdue")
        elif argv[0] == "payouts":
            counts = disburse_due(session, make_gateway(settings), now)
            print(", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in counts.items()))
        else:
            sent = deliver_due(session, now, allow_http=settings.is_mock)
            print(f"delivered {sent} webhook(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
