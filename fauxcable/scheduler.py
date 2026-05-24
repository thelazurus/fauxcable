import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from fauxcable.config import get_config
from fauxcable.pipeline import run_pipeline

logger = logging.getLogger(__name__)
_scheduler = AsyncIOScheduler()


def start_scheduler():
    cfg = get_config()
    _scheduler.add_job(
        _scheduled_run,
        trigger=IntervalTrigger(hours=cfg.schedule_interval_hours),
        id="epg_enrich",
        replace_existing=True,
        misfire_grace_time=300,
    )
    _scheduler.start()
    logger.info("Scheduler started — interval %.1fh", cfg.schedule_interval_hours)


def stop_scheduler():
    if _scheduler.running:
        _scheduler.shutdown(wait=False)


def reschedule(interval_hours: float):
    _scheduler.reschedule_job(
        "epg_enrich",
        trigger=IntervalTrigger(hours=interval_hours),
    )
    logger.info("Schedule updated to %.1fh", interval_hours)


def next_run_time() -> str | None:
    job = _scheduler.get_job("epg_enrich")
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None


async def _scheduled_run():
    cfg = get_config()
    if not cfg.epg_url:
        logger.warning("Scheduled run skipped — EPG source URL not set")
        return
    try:
        await run_pipeline(cfg)
    except RuntimeError:
        logger.info("Scheduled run skipped — pipeline already running")
    except Exception as exc:
        logger.exception("Scheduled run failed: %s", exc)
