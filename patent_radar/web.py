"""웹페이지와 JSON API."""

from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import briefing, db, news
from .config import KST, kipris_api_key, load_news_settings

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Patent Radar")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def revalidate_pages(request, call_next):
    """코드를 고친 뒤 브라우저가 예전 화면 파일을 계속 쓰지 않게, 매번 바뀌었는지 확인하게 한다."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


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
    # config/topics.toml에 적힌 주제 순서대로 (같은 주제 안에서는 최신순 그대로)
    order = [t.name for t in load_news_settings().topics]
    items.sort(key=lambda a: order.index(a["topic"]) if a["topic"] in order else len(order))
    return {"day": target.isoformat(), "items": items}


@app.get("/api/briefing")
def get_briefing(day: str | None = None) -> dict:
    try:
        target = date.fromisoformat(day) if day else datetime.now(KST).date()
    except ValueError:
        raise HTTPException(400, "day는 YYYY-MM-DD 형식이어야 합니다.")
    with db.session() as conn:
        found = briefing.get(conn, target)
    return {"day": target.isoformat(), "briefing": found}


@app.post("/api/news/refresh")
def refresh_news() -> dict:
    with db.session() as conn:
        results = news.collect(conn)
    return {"results": [asdict(r) for r in results], "added": sum(r.added for r in results)}
