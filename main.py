from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
import psycopg2.extras
import os
from typing import Optional

app = FastAPI(title="TenderWatch India API")

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
    return {"status": "ok", "message": "TenderWatch India API"}

@app.get("/tenders")
def search_tenders(
    q:                Optional[str]  = Query(None),
    organisation:     Optional[str]  = Query(None),
    portal:           Optional[str]  = Query(None),
    state:            Optional[str]  = Query(None),
    city:             Optional[str]  = Query(None),
    product_category: Optional[str]  = Query(None),
    status:           Optional[str]  = Query("active"),
    page:             int            = Query(1, ge=1),
    limit:            int            = Query(10, le=100),
):
    conn = get_conn()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    conditions = []
    params     = []

    if q:
        conditions.append(
            "to_tsvector('english', COALESCE(title,'') || ' ' || "
            "COALESCE(organisation,'') || ' ' || COALESCE(work_description,'')) "
            "@@ plainto_tsquery('english', %s)"
        )
        params.append(q)

    if organisation:
        conditions.append("organisation ILIKE %s")
        params.append(f"%{organisation}%")

    if portal:
        conditions.append("source_portal = %s")
        params.append(portal)

    if state:
        conditions.append("state ILIKE %s")
        params.append(f"%{state}%")

    if city:
        conditions.append("(city ILIKE %s OR district ILIKE %s OR location ILIKE %s)")
        params.extend([f"%{city}%", f"%{city}%", f"%{city}%"])

    if product_category:
        conditions.append("product_category ILIKE %s")
        params.append(f"%{product_category}%")

    if status and status != "all":
        conditions.append("status = %s")
        params.append(status)

    where  = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * limit

    cur.execute(f"SELECT COUNT(*) FROM tenders {where}", params)
    total = cur.fetchone()["count"]

    cur.execute(
        f"""SELECT id, source_portal, source_url, published_date, published_at,
                   closing_date, title, organisation, tender_value,
                   detail_url, tender_id, location, city, district, state,
                   product_category, emd_amount, tender_fee, status,
                   work_description, inviting_authority, inviting_address,
                   period_of_work, bid_validity
            FROM tenders {where}
            ORDER BY published_at DESC NULLS LAST, created_at DESC
            LIMIT %s OFFSET %s""",
        params + [limit, offset],
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return {
        "total": total,
        "page":  page,
        "limit": limit,
        "pages": max(1, (total + limit - 1) // limit),
        "results": [dict(r) for r in rows],
    }

@app.get("/tenders/{tender_id}")
def get_tender(tender_id: str):
    conn = get_conn()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM tenders WHERE tender_id = %s LIMIT 1", (tender_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Tender not found")
    return dict(row)

@app.get("/portals")
def list_portals():
    conn = get_conn()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
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
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT COUNT(*) as total FROM tenders WHERE status='active'")
    total = cur.fetchone()["total"]
    cur.execute("SELECT COUNT(DISTINCT source_portal) as portals FROM tenders")
    portals = cur.fetchone()["portals"]
    cur.execute("SELECT MAX(scraped_at) as last_updated FROM tenders")
    last_updated = cur.fetchone()["last_updated"]
    cur.close()
    conn.close()
    return {
        "total_tenders":   total,
        "portals_covered": portals,
        "last_updated":    str(last_updated)
    }

@app.post("/mark-expired")
def mark_expired():
    """Mark tenders as closed when closing date has passed. Call daily."""
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        UPDATE tenders
        SET status = 'closed'
        WHERE status = 'active'
          AND closing_date IS NOT NULL
          AND closing_date NOT IN ('NA', '', 'N/A')
          AND (
            TO_TIMESTAMP(closing_date, 'DD-Mon-YYYY HH12:MI AM') < NOW()
            OR TO_TIMESTAMP(closing_date, 'DD/MM/YYYY HH24:MI') < NOW()
          )
    """)
    updated = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return {"marked_closed": updated}
