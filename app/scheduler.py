from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.database import get_db
from app.crawler import crawl_site
import logging

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def scheduled_crawl():
    """Crawl all tracked sites."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, url FROM sites")
        sites = await cursor.fetchall()
        for site in sites:
            cursor = await db.execute(
                "INSERT INTO scans (site_id) VALUES (?)", (site["id"],)
            )
            scan_id = cursor.lastrowid
            await db.commit()
            logger.info(f"Scheduled scan {scan_id} for {site['url']}")
            try:
                await crawl_site(site["url"], scan_id, db)
            except Exception as e:
                logger.error(f"Scheduled crawl failed for {site['url']}: {e}")
                await db.execute(
                    "UPDATE scans SET status = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (f"error: {e}", scan_id)
                )
                await db.commit()
    finally:
        await db.close()


def start_scheduler(interval_hours: int = 24):
    scheduler.add_job(
        scheduled_crawl, "interval", hours=interval_hours,
        id="auto_crawl", replace_existing=True
    )
    scheduler.start()
    logger.info(f"Scheduler started: auto crawl every {interval_hours} hours")


def stop_scheduler():
    scheduler.shutdown(wait=False)
