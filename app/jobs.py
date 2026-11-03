"""Scheduled jobs. Run as `python -m app.jobs <job>` from Render cron or any scheduler.

daily             drop expired PayPal holds and cancel invoices unpaid past the timeout
deliver-webhooks  send due outbound webhooks once (when the in-process loop is turned off)
"""

import sys

from sqlalchemy.orm import sessionmaker

from app.config import Mode, get_settings
from app.db import Base, make_engine, utcnow
from app.escrow import expire_stale_holds, expire_stale_invoices
from app.main import make_gateway
from app.outbound import deliver_due

JOBS = ("daily", "deliver-webhooks")


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in JOBS:
        print(f"usage: python -m app.jobs {{{'|'.join(JOBS)}}}", file=sys.stderr)
        return 2
    settings = get_settings()
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    with sessionmaker(engine)() as session:
        now = utcnow()
        if argv[0] == "daily":
            holds = expire_stale_holds(session, now)
            invoices = expire_stale_invoices(session, make_gateway(settings), now)
            print(f"expired {holds} hold(s), closed {invoices} unpaid invoice(s)")
        else:
            sent = deliver_due(session, now, allow_http=settings.mode is Mode.mock)
            print(f"delivered {sent} webhook(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
