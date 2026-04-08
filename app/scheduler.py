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


def schedule_site_job(site_id: int, url: str, schedule: str):
    """Add or replace a scheduled job for a site.

    schedule: "weekly" or "monthly"
    First run is ~1 week or ~1 month from now at the current time.
    """
    job_id = f"site_crawl_{site_id}"
    now = datetime.now()

    if schedule == "weekly":
        trigger = CronTrigger(day_of_week=now.strftime("%a").lower()[:3], hour=now.hour, minute=now.minute)
    elif schedule == "monthly":
        day = min(now.day, 28)  # avoid issues with short months
        trigger = CronTrigger(day=day, hour=now.hour, minute=now.minute)
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
