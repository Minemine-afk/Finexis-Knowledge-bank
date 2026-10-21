"""Keyword search plus metadata filters. Ranking is plain Postgres full-text (no model), and every
ordering has a tie-breaker, so the same query over the same data always returns the same list."""
from __future__ import annotations

import html
from dataclasses import asdict, dataclass
from datetime import date

import psycopg
from psycopg.types.json import Jsonb

# Sentinels instead of <mark> so the snippet can be HTML-escaped safely before highlighting.
_START, _STOP = "\x02", "\x03"
_HEADLINE_OPTS = f"MaxFragments=2, MinWords=6, MaxWords=24, FragmentDelimiter=\" … \", StartSel={_START}, StopSel={_STOP}"


@dataclass
class Filters:
    issuer: str | None = None
    product_type: str | None = None
    document_type: str | None = None
    visibility: str | None = None        # shared | private
    mine: bool = False                   # only documents I own
    date_from: date | None = None        # effective_date range
    date_to: date | None = None
    crm_entity_type: str | None = None   # documents linked to a CRM record
    crm_entity_id: str | None = None

    def sql(self) -> tuple[str, dict]:
        clauses, params = ["TRUE"], {}
        for key in ("issuer", "product_type", "document_type"):
            if getattr(self, key):
                clauses.append(f"d.{key} = %({key})s")
                params[key] = getattr(self, key)
        if self.visibility:
            clauses.append("d.visibility = %(visibility)s::visibility")
            params["visibility"] = self.visibility
        if self.mine:
            clauses.append("d.owner_id = kb_current_user()")
        if self.date_from:
            clauses.append("d.effective_date >= %(date_from)s")
            params["date_from"] = self.date_from
        if self.date_to:
            clauses.append("d.effective_date <= %(date_to)s")
            params["date_to"] = self.date_to
        if self.crm_entity_id:
            clauses.append("""EXISTS (SELECT 1 FROM document_links l WHERE l.document_id = d.id
                              AND l.system = 'crm' AND l.entity_id = %(crm_id)s
                              AND (%(crm_type)s::text IS NULL OR l.entity_type = %(crm_type)s))""")
            params["crm_id"] = self.crm_entity_id
            params["crm_type"] = self.crm_entity_type
        return " AND ".join(clauses), params

    def as_log(self) -> dict:
        return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in asdict(self).items() if v}


def highlight(snippet: str | None) -> str:
    """Escape, then turn sentinels into <mark>. Output is safe to render as HTML."""
    escaped = html.escape(snippet or "")
    return escaped.replace(_START, "<mark>").replace(_STOP, "</mark>")


DOC_COLUMNS = """d.id, d.title, d.file_name, d.mime_type, d.visibility::text AS visibility, d.owner_id,
                 u.display_name AS owner_name, d.issuer, d.product_type, d.policy_name, d.effective_date,
                 d.document_type, d.version, d.page_count"""


def search(conn: psycopg.Connection, user_id: int | None, q: str, filters: Filters,
           limit: int = 20, offset: int = 0, log: bool = True) -> dict:
    """One result per document: its best-matching page, a highlighted snippet and how many pages match."""
    where, params = filters.sql()
    params.update(q=q.strip(), limit=limit + 1, offset=offset)

    if params["q"]:
        rows = conn.execute(
            f"""
            WITH query AS (SELECT websearch_to_tsquery('english', %(q)s) AS tsq),
            hits AS (
                SELECT d.id AS document_id, p.page_no, p.text,
                       ts_rank_cd(p.tsv, query.tsq, 32) + 2 * ts_rank_cd(d.meta_tsv, query.tsq, 32) AS score,
                       p.tsv @@ query.tsq AS page_match
                FROM documents d JOIN pages p ON p.document_id = d.id, query
                WHERE (p.tsv @@ query.tsq OR d.meta_tsv @@ query.tsq) AND {where}
            ),
            best AS (
                SELECT *, row_number() OVER (PARTITION BY document_id ORDER BY page_match DESC, score DESC, page_no) AS rn,
                       count(*) FILTER (WHERE page_match) OVER (PARTITION BY document_id) AS matching_pages
                FROM hits
            )
            SELECT {DOC_COLUMNS}, b.page_no AS best_page, b.matching_pages, round(b.score::numeric, 6) AS score,
                   ts_headline('english', b.text, query.tsq, %(opts)s) AS snippet
            FROM best b JOIN documents d ON d.id = b.document_id JOIN users u ON u.id = d.owner_id, query
            WHERE b.rn = 1
            ORDER BY b.score DESC, d.effective_date DESC NULLS LAST, d.id
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {**params, "opts": _HEADLINE_OPTS},
        ).fetchall()
    else:
        rows = conn.execute(
            f"""SELECT {DOC_COLUMNS}, 1 AS best_page, 0 AS matching_pages, NULL::numeric AS score, NULL AS snippet
                FROM documents d JOIN users u ON u.id = d.owner_id
                WHERE {where}
                ORDER BY d.effective_date DESC NULLS LAST, d.title, d.id
                LIMIT %(limit)s OFFSET %(offset)s""",
            params,
        ).fetchall()

    has_more = len(rows) > limit
    rows = rows[:limit]
    for r in rows:
        r["snippet"] = highlight(r["snippet"])
        if r["score"] is not None:
            r["score"] = float(r["score"])

    if log:
        conn.execute("INSERT INTO search_log (user_id, query, filters, result_ids) VALUES (%s, %s, %s, %s)",
                     (user_id, params["q"], Jsonb({**filters.as_log(), "offset": offset}), [r["id"] for r in rows]))
    return {"query": params["q"], "filters": filters.as_log(), "offset": offset,
            "has_more": has_more, "results": rows}


def search_table_rows(conn: psycopg.Connection, q: str, filters: Filters, limit: int = 50) -> list[dict]:
    """Rows of extracted tables that match, with their header, so a figure can be read in context."""
    where, params = filters.sql()
    params.update(q=q.strip(), limit=limit)
    if not params["q"]:
        return []
    return conn.execute(
        f"""SELECT d.id AS document_id, d.title, t.id AS table_id, t.page_no, t.table_no, t.header,
                   r.row_no, r.cells, r.record,
                   ts_rank_cd(r.tsv, websearch_to_tsquery('english', %(q)s)) AS score
            FROM table_rows r JOIN doc_tables t ON t.id = r.table_id JOIN documents d ON d.id = r.document_id
            WHERE r.tsv @@ websearch_to_tsquery('english', %(q)s) AND {where}
            ORDER BY score DESC, d.id, t.page_no, t.table_no, r.row_no
            LIMIT %(limit)s""",
        params,
    ).fetchall()


def facets(conn: psycopg.Connection) -> dict[str, list[str]]:
    """Distinct values for the filter dropdowns, over the documents this user can see."""
    out = {}
    for key in ("issuer", "product_type", "document_type"):
        out[key] = [r["v"] for r in conn.execute(
            f"SELECT DISTINCT {key} AS v FROM documents WHERE {key} IS NOT NULL ORDER BY 1").fetchall()]
    return out
