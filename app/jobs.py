"""Scheduled jobs. Run as `python -m app.jobs <job>` from Render cron or any scheduler.

daily             drop expired PayPal holds and cancel invoices unpaid past the timeout
payouts           pay tranches whose dispute window closed and follow payouts in flight (hourly)
deliver-webhooks  send due outbound webhooks once (when the in-process loop is turned off)
"""

import sys

from app.config import Mode, get_settings
from app.db import make_engine, make_sessionmaker, migrate, utcnow
from app.integrations.paypal import make_gateway
from app.services.outbound import deliver_due
from app.services.settlement import disburse_due, expire_stale_holds, expire_stale_invoices

JOBS = ("migrate", "daily", "payouts", "deliver-webhooks")


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in JOBS:
        print(f"usage: python -m app.jobs {{{'|'.join(JOBS)}}}", file=sys.stderr)
        return 2
    settings = get_settings()
    engine = make_engine(settings.database_url)
    migrate.upgrade(engine)
    if argv[0] == "migrate":
        print("database is at the latest migration")
        return 0
    with make_sessionmaker(engine)() as session:
        now = utcnow()
        if argv[0] == "daily":
            holds = expire_stale_holds(session, now)
            invoices = expire_stale_invoices(session, make_gateway(settings), now)
            print(f"expired {holds} hold(s), closed {invoices} unpaid invoice(s)")
        elif argv[0] == "payouts":
            counts = disburse_due(session, make_gateway(settings), now)
            print(", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in counts.items()))
        else:
            sent = deliver_due(session, now, allow_http=settings.mode is Mode.mock)
            print(f"delivered {sent} webhook(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
