"""★ 관심 특허. claude.ai 브리핑 페이지에서 별표한 특허를 가져와 진행 상태(공개 → 등록 등)를 추적한다.

페이지는 별표를 페이지 데이터베이스의 'stars' 컬렉션에 저장한다. 스킬이 ArtifactData로 그 문서들을
폴더에 내려받으면 sync()가 로컬 DB와 맞추고, check()가 한국 특허의 진행 상태를 KIPRIS에서 다시 확인한다.
미국·중국 특허는 KIPRIS에서 진행 상태를 알 수 없어 추적하지 않는다.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .config import KST
from .patents import KiprisError


def read_page_docs(folder: Path) -> list[dict]:
    """ArtifactData가 내려받은 stars 문서들. 폴더 안 stars/*.json 또는 *.json을 읽는다."""
    folder = Path(folder)
    files = sorted((folder / "stars").glob("*.json")) or sorted(folder.glob("*.json"))
    docs = []
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(doc.get("data"), dict):  # 내려받은 형식이 {"id", "version", "data"}인 경우
            doc = doc["data"]
        if isinstance(doc.get("key"), str):
            docs.append(doc)
    return docs


def sync(conn: sqlite3.Connection, docs: list[dict]) -> tuple[int, int]:
    """페이지의 별표 목록과 맞춘다. (새로 추가한 수, 별표를 푼 수)"""
    keys = {d["key"] for d in docs}
    existing = {r["key"] for r in conn.execute("SELECT key FROM stars")}
    added = 0
    for d in docs:
        if d["key"] in existing:
            continue
        country, _, doc_key = d["key"].partition("-")
        row = conn.execute("SELECT id, status FROM patents WHERE country = ? AND doc_key = ?", (country, doc_key)).fetchone()
        conn.execute(
            "INSERT INTO stars (key, patent_id, starred_at, status, checked_at) VALUES (?, ?, ?, ?, ?)",
            (d["key"], row["id"] if row else None, d.get("starred_at") or datetime.now(KST).isoformat(timespec="seconds"),
             row["status"] if row else None, None),
        )
        added += 1
    removed = existing - keys
    conn.executemany("DELETE FROM stars WHERE key = ?", [(k,) for k in removed])
    conn.commit()
    return added, len(removed)


def check(conn: sqlite3.Connection, client) -> tuple[list[dict], list[str]]:
    """한국 관심 특허의 진행 상태를 다시 확인한다. (바뀐 것 목록, 경고)"""
    now = datetime.now(KST).isoformat(timespec="seconds")
    changes, warnings = [], []
    for s in conn.execute("SELECT key, patent_id, status FROM stars WHERE key LIKE 'KR-%'").fetchall():
        doc_key = s["key"].split("-", 1)[1]
        try:
            status = client.details("KR", doc_key).get("status")
        except KiprisError as e:
            warnings.append(f"{s['key']} 상태 확인 실패: {e}")
            continue
        changed = bool(status and s["status"] and status != s["status"])
        conn.execute(
            "UPDATE stars SET status = COALESCE(?, status), checked_at = ?, changed_at = CASE WHEN ? THEN ? ELSE changed_at END "
            "WHERE key = ?",
            (status, now, changed, now, s["key"]),
        )
        if s["patent_id"] and status:
            conn.execute("UPDATE patents SET status = ? WHERE id = ?", (status, s["patent_id"]))
        if changed:
            changes.append({"key": s["key"], "before": s["status"], "after": status})
    conn.commit()
    return changes, warnings


def listing(conn: sqlite3.Connection) -> list[dict]:
    """관심 특허 목록 (주간 리포트용)."""
    rows = conn.execute(
        """SELECT s.key, s.status, s.starred_at, s.changed_at, p.country, p.number, p.title, p.title_ko, p.url, p.applicant
           FROM stars s LEFT JOIN patents p ON p.id = s.patent_id ORDER BY s.starred_at DESC"""
    )
    return [dict(r) for r in rows]
