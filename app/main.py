import logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from app.database import init_db
from app.routes import router as api_router
from app.views import router as views_router
from app.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    start_scheduler(interval_hours=24)
    yield
    stop_scheduler()


app = FastAPI(title="Compider", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(api_router)
app.include_router(views_router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
