"""Daily loop used by the Docker setup (on Ubuntu, systemd runs the loader instead)."""
import logging
import time
from datetime import datetime, timedelta

from . import config, db, loader

log = logging.getLogger("scheduler")


def seconds_until(hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    now = datetime.now()
    nxt = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


def main():
    logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
    for _ in range(30):                      # wait for the database container
        try:
            db.connect().close()
            break
        except Exception:
            time.sleep(2)
    while True:
        # On a laptop the computer is often asleep or off at the scheduled time,
        # so also update whenever the last good update is more than 20 hours old.
        row = db.one("""SELECT extract(epoch FROM now() - max(finished_at)) / 3600 AS hours
                         FROM fetch_runs WHERE status IN ('ok', 'partial') AND mode IN ('full', 'incremental')""")
        hours = row["hours"] if row else None
        if hours is None or float(hours) > 20:
            log.info("data is %s hours old: updating now", "unknown" if hours is None else round(float(hours)))
            loader.run_with_catch_up(source="docker")
        else:
            retry = loader.steps_to_retry()       # a step failed because USAspending wasn't answering
            if retry:
                log.info("retrying: %s", ", ".join(retry))
                loader.run([s for s in retry if s != "details"] or ["details"], source="docker")
            if loader.details_pending():          # e.g. the first load was interrupted
                loader.catch_up_details(source="docker")
        wait = min(seconds_until(config.SCHEDULE_AT), 3600)   # wake at least hourly to re-check
        time.sleep(wait)
        if seconds_until(config.SCHEDULE_AT) > 23 * 3600:      # just passed the scheduled time
            loader.run_with_catch_up(source="docker")


if __name__ == "__main__":
    main()
