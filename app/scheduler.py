from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
from app.database import get_db
from app.crawler import crawl_site
import logging

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def crawl_one_site(site_id: int):
    """Scheduled crawl for a single site."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT url FROM sites WHERE id = ?", (site_id,))
        site = await cursor.fetchone()
        if not site:
            return
        cursor = await db.execute("INSERT INTO scans (site_id) VALUES (?)", (site_id,))
        scan_id = cursor.lastrowid
        await db.commit()
        logger.info(f"Scheduled scan {scan_id} for site {site_id} ({site['url']})")
        try:
            await crawl_site(site["url"], scan_id, db)
        except Exception as e:
            logger.error(f"Scheduled crawl failed for site {site_id}: {e}")
            await db.execute(
                "UPDATE scans SET status = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ?",
                (f"error: {e}", scan_id)
            )
            await db.commit()
    finally:
        await db.close()


# Scheduled scans run in the early morning (CST = UTC+8) and are staggered by site id so
# that two sites on the same server never crawl at the same time. Before 2026-09-13 the
# trigger copied the wall-clock time of the moment the job was (re)created, so every
# container restart moved the run to a random daytime slot and all sites fired at once.
SCAN_BASE_HOUR_UTC = 19  # 03:00 CST


def scan_time_for_site(site_id: int):
    """Return (hour_utc, minute) for a site: 19:00, 20:00, 21:00 ... UTC by site id."""
    hour = (SCAN_BASE_HOUR_UTC + (site_id - 1)) % 24
    return hour, 0


def schedule_site_job(site_id: int, url: str, schedule: str):
    """Add or replace a scheduled job for a site.

    schedule: "weekly" or "monthly"
    weekly  -> every Sunday at the site's fixed early-morning slot
    monthly -> the 1st of each month at that slot
    """
    job_id = f"site_crawl_{site_id}"
    hour, minute = scan_time_for_site(site_id)

    if schedule == "weekly":
        trigger = CronTrigger(day_of_week="sun", hour=hour, minute=minute)
    elif schedule == "monthly":
        trigger = CronTrigger(day=1, hour=hour, minute=minute)
    else:
        return

    scheduler.add_job(
        crawl_one_site, trigger,
        args=[site_id], id=job_id, replace_existing=True
    )
    next_run = scheduler.get_job(job_id).next_run_time
    logger.info(f"Scheduled {schedule} crawl for site {site_id} ({url}), next run: {next_run}")


def remove_site_job(site_id: int):
    """Remove a scheduled job for a site."""
    job_id = f"site_crawl_{site_id}"
    try:
        scheduler.remove_job(job_id)
        logger.info(f"Removed scheduled crawl for site {site_id}")
    except Exception:
        pass


async def restore_schedules():
    """On startup, restore scheduled jobs from DB."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, url, schedule FROM sites WHERE schedule IS NOT NULL")
        rows = await cursor.fetchall()
        for row in rows:
            schedule_site_job(row["id"], row["url"], row["schedule"])
        if rows:
            logger.info(f"Restored {len(rows)} scheduled crawl jobs")
    finally:
        await db.close()


def start_scheduler():
    scheduler.start()
    logger.info("Scheduler started")


def stop_scheduler():
    scheduler.shutdown(wait=False)
