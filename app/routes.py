from fastapi import APIRouter, BackgroundTasks, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import aiohttp
import asyncio
from app.database import get_db
from app.crawler import crawl_site
from app.scheduler import scheduler, scheduled_crawl
from app.auth import hash_password, verify_password, create_token, require_user

router = APIRouter(prefix="/api")


# --- Auth ---

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/auth/register")
async def register(req: RegisterRequest):
    db = await get_db()
    try:
        existing = await db.execute("SELECT id FROM users WHERE email = ?", (req.email,))
        if await existing.fetchone():
            raise HTTPException(status_code=400, detail="Email already registered")
        cursor = await db.execute(
            "INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)",
            (req.email, hash_password(req.password), req.name or req.email.split("@")[0])
        )
        user_id = cursor.lastrowid
        await db.commit()
        token = create_token(user_id, req.email)
        resp = JSONResponse({"user_id": user_id, "email": req.email, "status": "registered"})
        resp.set_cookie("token", token, httponly=True, max_age=30 * 86400, samesite="lax")
        return resp
    finally:
        await db.close()


@router.post("/auth/login")
async def login(req: LoginRequest):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, email, password_hash FROM users WHERE email = ?", (req.email,))
        user = await cursor.fetchone()
        if not user or not verify_password(req.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        token = create_token(user["id"], user["email"])
        resp = JSONResponse({"user_id": user["id"], "email": user["email"], "status": "logged_in"})
        resp.set_cookie("token", token, httponly=True, max_age=30 * 86400, samesite="lax")
        return resp
    finally:
        await db.close()


@router.post("/auth/logout")
async def logout():
    resp = JSONResponse({"status": "logged_out"})
    resp.delete_cookie("token")
    return resp


# --- Sites (auth required) ---

class SiteCreate(BaseModel):
    url: str
    name: str | None = None


async def run_crawl(url: str, scan_id: int):
    db = await get_db()
    try:
        await crawl_site(url, scan_id, db)
    except Exception as e:
        await db.execute(
            "UPDATE scans SET status = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ?",
            (f"error: {e}", scan_id)
        )
        await db.commit()
    finally:
        await db.close()


async def verify_site_owner(db, site_id: int, user_id: int):
    """Check that a site belongs to a user."""
    cursor = await db.execute("SELECT id FROM sites WHERE id = ? AND user_id = ?", (site_id, user_id))
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Site not found")


@router.post("/sites")
async def add_site(site: SiteCreate, request: Request, bg: BackgroundTasks):
    user = await require_user(request)
    db = await get_db()
    try:
        # Check if this user already has this URL
        existing = await db.execute(
            "SELECT id FROM sites WHERE url = ? AND user_id = ?",
            (site.url.rstrip("/"), user["id"])
        )
        row = await existing.fetchone()
        if row:
            site_id = row["id"]
        else:
            cursor = await db.execute(
                "INSERT INTO sites (url, name, user_id) VALUES (?, ?, ?)",
                (site.url.rstrip("/"), site.name or site.url, user["id"])
            )
            site_id = cursor.lastrowid
        await db.commit()

        cursor = await db.execute(
            "INSERT INTO scans (site_id) VALUES (?)", (site_id,)
        )
        scan_id = cursor.lastrowid
        await db.commit()
        bg.add_task(run_crawl, site.url.rstrip("/"), scan_id)
        return {"site_id": site_id, "scan_id": scan_id, "status": "crawling"}
    finally:
        await db.close()


@router.get("/sites")
async def list_sites(request: Request):
    user = await require_user(request)
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM scans WHERE site_id = s.id) as scan_count "
            "FROM sites s WHERE s.user_id = ? ORDER BY s.created_at DESC",
            (user["id"],)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


@router.post("/sites/{site_id}/scan")
async def trigger_scan(site_id: int, request: Request, bg: BackgroundTasks):
    user = await require_user(request)
    db = await get_db()
    try:
        await verify_site_owner(db, site_id, user["id"])
        cursor = await db.execute("SELECT url FROM sites WHERE id = ?", (site_id,))
        site = await cursor.fetchone()
        cursor = await db.execute("INSERT INTO scans (site_id) VALUES (?)", (site_id,))
        scan_id = cursor.lastrowid
        await db.commit()
        bg.add_task(run_crawl, site["url"], scan_id)
        return {"scan_id": scan_id, "status": "crawling"}
    finally:
        await db.close()


@router.get("/sites/{site_id}/scans")
async def list_scans(site_id: int, request: Request):
    user = await require_user(request)
    db = await get_db()
    try:
        await verify_site_owner(db, site_id, user["id"])
        cursor = await db.execute(
            "SELECT * FROM scans WHERE site_id = ? ORDER BY started_at DESC", (site_id,)
        )
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


@router.delete("/scans/{scan_id}")
async def delete_scan(scan_id: int, request: Request):
    user = await require_user(request)
    db = await get_db()
    try:
        # Verify ownership through scan -> site -> user
        cursor = await db.execute(
            "SELECT s.user_id FROM scans sc JOIN sites s ON sc.site_id = s.id WHERE sc.id = ?",
            (scan_id,)
        )
        row = await cursor.fetchone()
        if not row or row["user_id"] != user["id"]:
            raise HTTPException(status_code=404, detail="Scan not found")
        await db.execute("DELETE FROM pages WHERE scan_id = ?", (scan_id,))
        await db.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
        await db.commit()
        return {"status": "deleted"}
    finally:
        await db.close()


@router.get("/scans/{scan_id}/pages")
async def get_scan_pages(scan_id: int, request: Request, status_filter: str | None = None):
    user = await require_user(request)
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT s.user_id FROM scans sc JOIN sites s ON sc.site_id = s.id WHERE sc.id = ?",
            (scan_id,)
        )
        row = await cursor.fetchone()
        if not row or row["user_id"] != user["id"]:
            raise HTTPException(status_code=404, detail="Scan not found")

        if status_filter == "errors":
            cursor = await db.execute(
                "SELECT * FROM pages WHERE scan_id = ? AND status_code != 200 ORDER BY status_code, url",
                (scan_id,)
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM pages WHERE scan_id = ? ORDER BY url", (scan_id,)
            )
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


@router.get("/sites/{site_id}/diff")
async def diff_scans(site_id: int, request: Request, scan_a: int | None = None, scan_b: int | None = None):
    user = await require_user(request)
    db = await get_db()
    try:
        await verify_site_owner(db, site_id, user["id"])

        if not scan_a or not scan_b:
            cursor = await db.execute(
                "SELECT id FROM scans WHERE site_id = ? AND status = 'done' ORDER BY started_at DESC LIMIT 2",
                (site_id,)
            )
            rows = await cursor.fetchall()
            if len(rows) < 2:
                return {"error": "Need at least 2 completed scans to compare"}
            scan_b, scan_a = rows[0]["id"], rows[1]["id"]

        cursor_a = await db.execute("SELECT url, title, description, status_code FROM pages WHERE scan_id = ?", (scan_a,))
        cursor_b = await db.execute("SELECT url, title, description, status_code FROM pages WHERE scan_id = ?", (scan_b,))

        pages_a = {r["url"]: dict(r) for r in await cursor_a.fetchall()}
        pages_b = {r["url"]: dict(r) for r in await cursor_b.fetchall()}

        urls_a = set(pages_a.keys())
        urls_b = set(pages_b.keys())

        added = [{"url": u, **pages_b[u]} for u in sorted(urls_b - urls_a)]
        removed = [{"url": u, **pages_a[u]} for u in sorted(urls_a - urls_b)]
        title_changed = []
        status_changed = []

        for url in sorted(urls_a & urls_b):
            if pages_a[url]["title"] != pages_b[url]["title"]:
                title_changed.append({
                    "url": url,
                    "old_title": pages_a[url]["title"],
                    "new_title": pages_b[url]["title"]
                })
            if pages_a[url]["status_code"] != pages_b[url]["status_code"]:
                status_changed.append({
                    "url": url,
                    "old_status": pages_a[url]["status_code"],
                    "new_status": pages_b[url]["status_code"]
                })

        return {
            "scan_a": scan_a, "scan_b": scan_b,
            "added": added, "removed": removed,
            "title_changed": title_changed,
            "status_changed": status_changed,
            "summary": {
                "added": len(added), "removed": len(removed),
                "title_changed": len(title_changed), "status_changed": len(status_changed)
            }
        }
    finally:
        await db.close()


@router.delete("/sites/{site_id}")
async def delete_site(site_id: int, request: Request):
    user = await require_user(request)
    db = await get_db()
    try:
        await verify_site_owner(db, site_id, user["id"])
        await db.execute(
            "DELETE FROM pages WHERE scan_id IN (SELECT id FROM scans WHERE site_id = ?)",
            (site_id,)
        )
        await db.execute("DELETE FROM scans WHERE site_id = ?", (site_id,))
        await db.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        await db.commit()
        return {"status": "deleted"}
    finally:
        await db.close()


class ScheduleSettings(BaseModel):
    interval_hours: int = 24


@router.post("/settings/schedule")
async def update_schedule(settings: ScheduleSettings, request: Request):
    await require_user(request)
    scheduler.remove_job("auto_crawl", jobstore="default")
    scheduler.add_job(
        scheduled_crawl, "interval", hours=settings.interval_hours,
        id="auto_crawl", replace_existing=True
    )
    return {"interval_hours": settings.interval_hours, "status": "updated"}


@router.get("/settings/schedule")
async def get_schedule(request: Request):
    await require_user(request)
    job = scheduler.get_job("auto_crawl")
    if job:
        return {"interval_hours": job.trigger.interval.total_seconds() / 3600, "next_run": str(job.next_run_time)}
    return {"interval_hours": None, "next_run": None}


class UrlCheckRequest(BaseModel):
    urls: list[str]


@router.post("/check-urls")
async def check_urls(req: UrlCheckRequest, request: Request):
    await require_user(request)
    semaphore = asyncio.Semaphore(10)

    async def check_one(session, url):
        try:
            async with semaphore:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=15),
                    allow_redirects=False, ssl=False
                ) as resp:
                    status = resp.status
                    final_url = None
                    if 300 <= status < 400:
                        location = resp.headers.get("Location", "")
                        try:
                            async with session.get(
                                url, timeout=aiohttp.ClientTimeout(total=15),
                                allow_redirects=True, ssl=False
                            ) as resp2:
                                final_url = str(resp2.url)
                        except Exception:
                            final_url = location
                    return {"url": url, "status_code": status, "redirect_to": final_url}
        except asyncio.TimeoutError:
            return {"url": url, "status_code": 0, "redirect_to": None, "error": "Timeout"}
        except Exception as e:
            return {"url": url, "status_code": 0, "redirect_to": None, "error": str(e)}

    connector = aiohttp.TCPConnector(limit=10, force_close=True)
    async with aiohttp.ClientSession(
        connector=connector,
        headers={"User-Agent": "Compider/1.0 (Site Monitor)"}
    ) as session:
        tasks = [check_one(session, url) for url in req.urls]
        results = await asyncio.gather(*tasks)

    return list(results)
