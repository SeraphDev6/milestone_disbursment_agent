"""Scheduled jobs. Run as `python -m app.jobs <job>` from Render cron or any scheduler.

migrate           apply pending migrations and exit (run before starting several app instances)
daily             drop expired PayPal holds, close invoices unpaid past the timeout, mark tranches overdue
payouts           catch up on captures, payments and invoices PayPal finished without a webhook reaching us, pay
                  tranches whose dispute window closed and follow payouts in flight (hourly)
deliver-webhooks  send due outbound webhooks and GitHub check runs once (when the in-process loops are off)
encrypt-files     encrypt stored files kept in plain form or under an old key with FILE_ENCRYPTION_KEY, and move
                  files still in the database to the bucket when FILE_STORAGE=s3 (once, after turning encryption
                  on, rotating the key or switching storage; safe to rerun)

`daily` also ends custody of closed tranches' originals when RETENTION_DAYS is set (app/services/custody.py).
"""

import logging
import sys

from app.api.spa import has_build
from app.config import get_settings
from app.db import make_engine, make_sessionmaker, migrate, utcnow
from app.integrations import filecrypt, filestore
from app.integrations.email import make_mailer
from app.integrations.github_app import make_github_app
from app.integrations.paypal import make_gateway
from app.services import custody
from app.services.github_app import publish_checks
from app.services.handover import run as run_handovers
from app.services.notifications import NOTIFIER, Notifier
from app.services.outbound import deliver_due
from app.services.settlement import disburse_due, expire_stale_holds, expire_stale_invoices
from app.services.verification import mark_overdue

JOBS = ("migrate", "daily", "payouts", "deliver-webhooks", "encrypt-files")


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in JOBS:
        print(f"usage: python -m app.jobs {{{'|'.join(JOBS)}}}", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(name)s: %(message)s")
    settings = get_settings()
    engine = make_engine(settings.database_url)
    migrate.upgrade(engine)
    filecrypt.configure(filecrypt.FileCipher.from_settings(settings))
    filestore.configure(filestore.from_settings(settings), settings.s3_prefix)
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
            kept = custody.run(session, settings, now)
            if kept:
                print(", ".join(f"{k}: {v}" for k, v in kept.items()))
        elif argv[0] == "encrypt-files":
            print(f"updated {custody.encrypt_existing(session)} file(s)")
        elif argv[0] == "payouts":
            counts = disburse_due(session, make_gateway(settings), now)
            print(", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in counts.items()))
        else:
            sent = deliver_due(session, now, allow_http=settings.is_mock)
            gh = make_github_app(settings)
            checks = publish_checks(session, gh, notifier.tranche_link, now)
            handed = run_handovers(session, gh, now)
            print(f"delivered {sent} webhook(s), posted {checks} GitHub check run(s), handed over {handed} tranche(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
