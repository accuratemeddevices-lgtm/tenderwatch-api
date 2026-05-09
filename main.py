from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
import psycopg2.extras
import os
from typing import Optional
from datetime import datetime, date, timedelta

app = FastAPI(title="India Tenders API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_conn():
    return psycopg2.connect(os.environ["NEON_URL"])

@app.get("/")
def root():
    return {"status": "ok", "message": "India Tenders API"}

@app.get("/tenders")
def search_tenders(
    q: Optional[str] = Query(None, description="Keyword search"),
    organisation: Optional[str] = Query(None),
    portal: Optional[str] = Query(None),
    closing_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    closing_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    closing_this_week: Optional[bool] = Query(False),
    page: int = Query(1, ge=1),
    limit: int = Query(20, le=100),
):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    conditions = []
    params = []

    if q:
        conditions.append(
            "to_tsvector('english', COALESCE(title,'') || ' ' || COALESCE(organisation,'')) "
            "@@ plainto_tsquery('english', %s)"
        )
        params.append(q)

    if organisation:
        conditions.append("organisation ILIKE %s")
        params.append(f"%{organisation}%")

    if portal:
        conditions.append("source_portal = %s")
        params.append(portal)

    if closing_this_week:
        today = date.today()
        week_end = today + timedelta(days=7)
        # closing_date is stored as text like "14-May-2026 03:00 PM"
        # We filter using scraped_at range as proxy, or just return recent
        conditions.append("closing_date IS NOT NULL AND closing_date != 'NA'")

    if closing_from:
        conditions.append("scraped_at::date >= %s")
        params.append(closing_from)

    if closing_to:
        conditions.append("scraped_at::date <= %s")
        params.append(closing_to)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    offset = (page - 1) * limit

    # Count
    cur.execute(f"SELECT COUNT(*) FROM tenders {where}", params)
    total = cur.fetchone()["count"]

    # Data
    cur.execute(
        f"""SELECT id, source_portal, source_url, published_date, closing_date,
                   opening_date, title, organisation, tender_value, detail_url, scraped_at
            FROM tenders {where}
            ORDER BY published_at DESC NULLS LAST
            LIMIT %s OFFSET %s""",
        params + [limit, offset],
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit,
        "results": [dict(r) for r in rows],
    }

@app.get("/portals")
def list_portals():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT source_portal, COUNT(*) as count
        FROM tenders
        GROUP BY source_portal
        ORDER BY count DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/stats")
def stats():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT COUNT(*) as total FROM tenders")
    total = cur.fetchone()["total"]
    cur.execute("SELECT COUNT(DISTINCT source_portal) as portals FROM tenders")
    portals = cur.fetchone()["portals"]
    cur.execute("SELECT MAX(scraped_at) as last_updated FROM tenders")
    last_updated = cur.fetchone()["last_updated"]
    cur.close()
    conn.close()
    return {"total_tenders": total, "portals_covered": portals, "last_updated": str(last_updated)}
