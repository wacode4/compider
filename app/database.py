import aiosqlite
import os

DB_PATH = os.environ.get("COMPIDER_DB", "compider.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS sites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 0,
                url TEXT NOT NULL,
                name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id INTEGER NOT NULL,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP,
                total_urls INTEGER DEFAULT 0,
                status TEXT DEFAULT 'running',
                FOREIGN KEY (site_id) REFERENCES sites(id)
            );
            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                status_code INTEGER,
                title TEXT,
                description TEXT,
                referred_by TEXT,
                FOREIGN KEY (scan_id) REFERENCES scans(id)
            );
            CREATE INDEX IF NOT EXISTS idx_pages_scan_id ON pages(scan_id);
            CREATE INDEX IF NOT EXISTS idx_pages_url ON pages(url);
            CREATE INDEX IF NOT EXISTS idx_scans_site_id ON scans(site_id);
            CREATE INDEX IF NOT EXISTS idx_sites_user_id ON sites(user_id);
        """)
        # Migrations for older schemas
        for col_sql in [
            "ALTER TABLE sites ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE sites ADD COLUMN schedule TEXT DEFAULT NULL",
        ]:
            try:
                await db.execute(col_sql)
            except Exception:
                pass
        await db.commit()


async def get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db
