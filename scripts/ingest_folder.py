"""Bulk-ingest a folder and write an extraction-quality report.

    # Check extraction only, no database (use this first on the 20-30 sample documents):
    python -m scripts.ingest_folder ./samples --dry-run

    # Ingest into the database as a user, all shared:
    python -m scripts.ingest_folder ./samples --owner alice --visibility shared

Optional metadata sidecar: a `metadata.csv` in the folder with a `file_name` column and any of
title, issuer, product_type, policy_name, effective_date, document_type, version, visibility,
crm_entity_type, crm_entity_id. Values there override what is detected from the text.

The report (reports/extraction-<timestamp>.csv and .md) lists, per file: pages, characters,
OCR pages, tables and rows found, detected metadata and warnings, so table extraction can be
checked by eye against the originals.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.extract import MIME_TYPES, extract  # noqa: E402

REPORT_COLUMNS = ["file_name", "status", "seconds", "pages", "chars", "ocr_pages", "empty_pages",
                  "chart_candidate_pages", "tables", "table_rows", "issuer", "document_type",
                  "effective_date", "version", "document_id", "warnings"]


def load_sidecar(folder: Path) -> dict[str, dict]:
    path = folder / "metadata.csv"
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        return {row["file_name"]: {k: v for k, v in row.items() if k != "file_name" and v} for row in csv.DictReader(f)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--owner", help="username that will own the documents (required unless --dry-run)")
    ap.add_argument("--visibility", choices=["shared", "private"], default="private")
    ap.add_argument("--dry-run", action="store_true", help="extract and report only; no database")
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--reports", type=Path, default=Path("reports"))
    args = ap.parse_args()

    files = sorted(p for p in args.folder.rglob("*") if p.suffix.lower() in MIME_TYPES and p.is_file())
    if not files:
        print(f"No PDF/DOCX/XLSX files under {args.folder}")
        return 1
    sidecar = load_sidecar(args.folder)

    user = None
    if not args.dry_run:
        from app import db
        if not args.owner or (user := db.user_by_name(args.owner)) is None:
            print("--owner must name an existing user")
            return 1

    rows = []
    for path in files:
        meta = dict(sidecar.get(path.name, {}))
        started = time.perf_counter()
        row = {"file_name": str(path.relative_to(args.folder))}
        try:
            ex = extract(path, ocr=not args.no_ocr)
            rep = ex.report()
            row.update({k: rep[k] for k in ("pages", "chars", "tables", "table_rows")})
            for k in ("ocr_pages", "empty_pages", "chart_candidate_pages"):
                row[k] = " ".join(map(str, rep[k]))
            row.update({k: meta.get(k) or ex.metadata.get(k) or "" for k in ("issuer", "document_type", "effective_date", "version")})
            row["warnings"] = "; ".join(rep["warnings"])
            if not args.dry_run:
                from app import db
                from app.ingest import ingest_file
                links = [{"system": "crm", "entity_type": meta.pop("crm_entity_type", "client"),
                          "entity_id": meta.pop("crm_entity_id")}] if meta.get("crm_entity_id") else []
                visibility = meta.pop("visibility", args.visibility)
                with db.as_user(user["id"]) as conn:
                    doc_id, created = ingest_file(conn, user["id"], path, path.name, visibility, meta, links,
                                                  ocr=not args.no_ocr)
                row["document_id"] = doc_id
                row["status"] = "ingested" if created else "already present"
            else:
                row["status"] = "extracted"
        except Exception as e:
            row["status"], row["warnings"] = "FAILED", f"{type(e).__name__}: {e}"
        row["seconds"] = round(time.perf_counter() - started, 2)
        rows.append(row)
        print(f"{row['status']:>15}  {row['file_name']}  pages={row.get('pages', '-')} tables={row.get('tables', '-')}"
              f"{'  ' + row['warnings'] if row.get('warnings') else ''}")

    args.reports.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    csv_path = args.reports / f"extraction-{stamp}.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REPORT_COLUMNS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in REPORT_COLUMNS} for r in rows)

    failed = [r for r in rows if r["status"] == "FAILED"]
    md = [f"# Extraction report {stamp}", "",
          f"{len(rows)} files, {len(failed)} failed, "
          f"{sum(int(r.get('tables') or 0) for r in rows)} tables, "
          f"{sum(1 for r in rows if r.get('ocr_pages'))} files needed OCR.", "",
          "| File | Status | Pages | Tables | Rows | OCR pages | Issuer | Type | Effective | Warnings |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    md += [f"| {r['file_name']} | {r['status']} | {r.get('pages', '')} | {r.get('tables', '')} | {r.get('table_rows', '')} | "
           f"{r.get('ocr_pages', '')} | {r.get('issuer', '')} | {r.get('document_type', '')} | {r.get('effective_date', '')} | "
           f"{(r.get('warnings') or '').replace('|', '/')} |" for r in rows]
    (args.reports / f"extraction-{stamp}.md").write_text("\n".join(md) + "\n")
    print(f"\nReport: {csv_path} (+ .md)")
    if not args.dry_run:
        from app import db
        db.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
