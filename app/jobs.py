"""Scheduled jobs. Run as `python -m app.jobs expire-holds` (Render cron or any scheduler)."""

import sys

from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db import Base, make_engine, utcnow
from app.escrow import expire_stale_holds


def main(argv: list[str]) -> int:
    if argv != ["expire-holds"]:
        print("usage: python -m app.jobs expire-holds", file=sys.stderr)
        return 2
    engine = make_engine(get_settings().database_url)
    Base.metadata.create_all(engine)
    with sessionmaker(engine)() as session:
        print(f"expired {expire_stale_holds(session, utcnow())} hold(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
