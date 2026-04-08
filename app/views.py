from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from app.database import get_db
from app.auth import get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


async def require_login(request: Request):
    """Return user dict or redirect response."""
    user = await get_current_user(request)
    if not user:
        return None
    return user


@router.get("/")
async def landing(request: Request):
    user = await get_current_user(request)
    if user:
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("landing.html", {"request": request})


@router.get("/login")
async def login_page(request: Request):
    user = await get_current_user(request)
    if user:
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/register")
async def register_page(request: Request):
    user = await get_current_user(request)
    if user:
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("register.html", {"request": request})


@router.get("/dashboard")
async def dashboard(request: Request):
    user = await require_login(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM scans WHERE site_id = s.id) as scan_count "
            "FROM sites s WHERE s.user_id = ? ORDER BY s.created_at DESC",
            (user["id"],)
        )
        sites = [dict(r) for r in await cursor.fetchall()]

        for site in sites:
            scan_cursor = await db.execute(
                "SELECT * FROM scans WHERE site_id = ? ORDER BY started_at DESC LIMIT 1",
                (site["id"],)
            )
            scan = await scan_cursor.fetchone()
            site["latest_scan"] = dict(scan) if scan else None

            if scan:
                err_cursor = await db.execute(
                    "SELECT COUNT(*) as cnt FROM pages WHERE scan_id = ? AND status_code != 200",
                    (scan["id"],)
                )
                err_row = await err_cursor.fetchone()
                site["error_count"] = err_row["cnt"]
            else:
                site["error_count"] = 0

        return templates.TemplateResponse("index.html", {"request": request, "sites": sites, "user": user})
    finally:
        await db.close()


@router.get("/site/{site_id}")
async def site_detail(request: Request, site_id: int):
    user = await require_login(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM sites WHERE id = ? AND user_id = ?", (site_id, user["id"]))
        site = await cursor.fetchone()
        if not site:
            return RedirectResponse("/dashboard", status_code=302)
        site = dict(site)

        scan_cursor = await db.execute(
            "SELECT * FROM scans WHERE site_id = ? ORDER BY started_at DESC", (site_id,)
        )
        scans = [dict(r) for r in await scan_cursor.fetchall()]

        for scan in scans:
            err_cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM pages WHERE scan_id = ? AND status_code != 200",
                (scan["id"],)
            )
            scan["error_count"] = (await err_cursor.fetchone())["cnt"]

        return templates.TemplateResponse("site.html", {
            "request": request, "site": site, "scans": scans, "user": user
        })
    finally:
        await db.close()


@router.get("/site/{site_id}/scan/{scan_id}")
async def scan_detail(request: Request, site_id: int, scan_id: int, filter: str | None = None):
    user = await require_login(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM sites WHERE id = ? AND user_id = ?", (site_id, user["id"]))
        site_row = await cursor.fetchone()
        if not site_row:
            return RedirectResponse("/dashboard", status_code=302)
        site = dict(site_row)

        scan_cursor = await db.execute(
            "SELECT * FROM scans WHERE site_id = ? ORDER BY started_at DESC", (site_id,)
        )
        scans = [dict(r) for r in await scan_cursor.fetchall()]

        for scan in scans:
            err_cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM pages WHERE scan_id = ? AND status_code != 200",
                (scan["id"],)
            )
            scan["error_count"] = (await err_cursor.fetchone())["cnt"]

        current_cursor = await db.execute("SELECT * FROM scans WHERE id = ?", (scan_id,))
        current_scan = dict(await current_cursor.fetchone())

        total_cursor = await db.execute("SELECT COUNT(*) as cnt FROM pages WHERE scan_id = ?", (scan_id,))
        total_count = (await total_cursor.fetchone())["cnt"]
        error_cursor = await db.execute("SELECT COUNT(*) as cnt FROM pages WHERE scan_id = ? AND status_code != 200", (scan_id,))
        error_count = (await error_cursor.fetchone())["cnt"]

        if filter == "errors":
            page_cursor = await db.execute(
                "SELECT * FROM pages WHERE scan_id = ? AND status_code != 200 ORDER BY status_code, url",
                (scan_id,)
            )
        else:
            page_cursor = await db.execute(
                "SELECT * FROM pages WHERE scan_id = ? ORDER BY url", (scan_id,)
            )
        pages = [dict(r) for r in await page_cursor.fetchall()]

        return templates.TemplateResponse("site.html", {
            "request": request, "site": site, "scans": scans,
            "current_scan": current_scan, "pages": pages, "filter": filter,
            "total_count": total_count, "error_count": error_count, "user": user
        })
    finally:
        await db.close()


@router.get("/site/{site_id}/diff")
async def diff_view(request: Request, site_id: int, scan_a: int | None = None, scan_b: int | None = None):
    user = await require_login(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM sites WHERE id = ? AND user_id = ?", (site_id, user["id"]))
        site_row = await cursor.fetchone()
        if not site_row:
            return RedirectResponse("/dashboard", status_code=302)
        site = dict(site_row)

        all_scans_cursor = await db.execute(
            "SELECT id, started_at, total_urls FROM scans WHERE site_id = ? AND status = 'done' ORDER BY started_at DESC",
            (site_id,)
        )
        all_scans = [dict(r) for r in await all_scans_cursor.fetchall()]

        if not scan_a or not scan_b:
            if len(all_scans) < 2:
                return templates.TemplateResponse("diff.html", {
                    "request": request, "site": site, "all_scans": all_scans, "user": user,
                    "diff": {"error": "Need at least 2 completed scans", "summary": {"added": 0, "removed": 0, "title_changed": 0, "status_changed": 0},
                             "added": [], "removed": [], "title_changed": [], "status_changed": [], "scan_a": 0, "scan_b": 0}
                })
            scan_b, scan_a = all_scans[0]["id"], all_scans[1]["id"]

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

        diff = {
            "scan_a": scan_a, "scan_b": scan_b,
            "added": added, "removed": removed,
            "title_changed": title_changed, "status_changed": status_changed,
            "summary": {
                "added": len(added), "removed": len(removed),
                "title_changed": len(title_changed), "status_changed": len(status_changed)
            }
        }

        return templates.TemplateResponse("diff.html", {
            "request": request, "site": site, "diff": diff, "all_scans": all_scans, "user": user
        })
    finally:
        await db.close()
