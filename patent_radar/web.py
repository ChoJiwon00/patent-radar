"""웹페이지와 JSON API."""

from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, news
from .config import KST, kipris_api_key

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Patent Radar")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def status() -> dict:
    with db.session() as conn:
        days = news.available_days(conn)
    return {
        "today": datetime.now(KST).date().isoformat(),
        "news_days": days,
        "kipris_connected": kipris_api_key() is not None,
    }


@app.get("/api/news")
def get_news(day: str | None = None) -> dict:
    try:
        target = date.fromisoformat(day) if day else datetime.now(KST).date()
    except ValueError:
        raise HTTPException(400, "day는 YYYY-MM-DD 형식이어야 합니다.")
    with db.session() as conn:
        items = news.news_for_day(conn, target)
    return {"day": target.isoformat(), "items": items}


@app.post("/api/news/refresh")
def refresh_news() -> dict:
    with db.session() as conn:
        results = news.collect(conn)
    return {"results": [asdict(r) for r in results], "added": sum(r.added for r in results)}
