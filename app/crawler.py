from urllib.parse import urljoin, urlparse, urlunparse
from bs4 import BeautifulSoup
import aiohttp
import asyncio
import logging

logger = logging.getLogger(__name__)


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, ""))


def extract_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    base_domain = urlparse(base_url).netloc
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        if parsed.netloc == base_domain and parsed.scheme in ("http", "https"):
            links.append(normalize_url(full_url))
    return list(set(links))


def extract_metadata(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    description = ""
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        description = meta_desc["content"].strip()
    return {"title": title, "description": description}


async def crawl_site(site_url: str, scan_id: int, db, max_concurrent: int = 10):
    """Crawl a site, storing results in the database."""
    site_url = normalize_url(site_url)
    visited = set()
    queue = asyncio.Queue()
    queue.put_nowait((site_url, None))  # (url, referred_by)
    semaphore = asyncio.Semaphore(max_concurrent)
    total = 0

    async def fetch_page(session, url, referred_by):
        nonlocal total
        if url in visited:
            return
        visited.add(url)

        status_code = None
        title = ""
        description = ""
        new_links = []

        try:
            async with semaphore:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30),
                                       allow_redirects=True, ssl=False) as resp:
                    status_code = resp.status
                    if resp.content_type and "text/html" in resp.content_type:
                        html = await resp.text(errors="replace")
                        meta = extract_metadata(html)
                        title = meta["title"]
                        description = meta["description"]
                        if status_code == 200:
                            new_links = extract_links(html, url)
        except asyncio.TimeoutError:
            status_code = 0
            title = "[Timeout]"
        except Exception as e:
            status_code = 0
            title = f"[Error: {type(e).__name__}]"
            logger.warning(f"Error fetching {url}: {e}")

        await db.execute(
            "INSERT INTO pages (scan_id, url, status_code, title, description, referred_by) VALUES (?, ?, ?, ?, ?, ?)",
            (scan_id, url, status_code, title, description, referred_by)
        )
        await db.commit()
        total += 1

        for link in new_links:
            if link not in visited:
                queue.put_nowait((link, url))

    connector = aiohttp.TCPConnector(limit=max_concurrent, force_close=True)
    async with aiohttp.ClientSession(
        connector=connector,
        headers={"User-Agent": "Compider/1.0 (Site Monitor)"}
    ) as session:
        while not queue.empty() or total == 0:
            tasks = []
            batch_size = min(queue.qsize(), max_concurrent) if not queue.empty() else 0
            for _ in range(max(batch_size, 1)):
                if queue.empty():
                    break
                url, referred_by = queue.get_nowait()
                tasks.append(fetch_page(session, url, referred_by))
            if tasks:
                await asyncio.gather(*tasks)
            else:
                break

    await db.execute(
        "UPDATE scans SET finished_at = CURRENT_TIMESTAMP, total_urls = ?, status = 'done' WHERE id = ?",
        (total, scan_id)
    )
    await db.commit()
    return total
