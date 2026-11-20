#!/usr/bin/env python3
"""Parse all 42 official MLIT Minister Certification ledger PDFs into JSON and CSV.

Uses PyMuPDF exact cell-rectangle detection (from TCPDF vector rectangles) combined
with character-level (`rawdict`) coordinate extraction so multi-line wrapped Japanese
cells, hyphen/prolonged-sound marks, and vertically-merged joint-application rows
(共同申請) are extracted with zero column shifting or character scrambling.

Outputs:
  - data/categories/<category_id>.json  (pretty or compact UTF-8 JSON)
  - data/categories/<category_id>.csv   (UTF-8 with BOM for Excel compatibility)
  - data/manifest.json
  - docs/data/categories/<category_id>.json (minified UTF-8 JSON for GitHub Pages)
  - docs/data/manifest.json                 (minified UTF-8 JSON for GitHub Pages)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymupdf

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = REPO_ROOT / ".cache" / "pdfs"
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_DOCS_DATA_DIR = REPO_ROOT / "docs" / "data"

RECORD_FIELDS = [
    "cert_no",
    "prefix",
    "category_id",
    "category_name",
    "date_jp",
    "date",
    "title",
    "applicant",
    "address",
    "evaluator",
    "period",
]

ERA_BASE_YEAR = {
    "令和": 2018,
    "平成": 1988,
    "昭和": 1925,
    "大正": 1911,
    "明治": 1867,
}

JP_DATE_RE = re.compile(
    r"(令和|平成|昭和|大正|明治)\s*(元|\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)

HEADER_KEYWORDS = ("認定番号", "認定年月日", "構造方法等の名称")


def parse_jp_date_to_iso(date_jp: str) -> str:
    """Convert the first Japanese era date in `date_jp` to ISO YYYY-MM-DD."""
    if not date_jp:
        return ""
    norm = unicodedata.normalize("NFKC", date_jp)
    m = JP_DATE_RE.search(norm)
    if not m:
        return ""
    era, year_str, month_str, day_str = m.groups()
    era_year = 1 if year_str == "元" else int(year_str)
    year = ERA_BASE_YEAR[era] + era_year
    month = int(month_str)
    day = int(day_str)
    # Validate calendar date
    try:
        datetime(year, month, day)
    except ValueError:
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


def normalize_cert_no(raw: str) -> str:
    """Normalize certification number: collapse whitespace, NFKC alphanumerics/hyphens."""
    if not raw:
        return ""
    # Replace line breaks / internal spaces and normalize fullwidth ASCII & hyphen variants
    s = unicodedata.normalize("NFKC", raw)
    s = re.sub(r"[‐‑‒–—―−ー－]", "-", s)
    s = re.sub(r"\s+", "", s).strip()
    return s


def extract_prefix(cert_no: str) -> str:
    """Extract prefix (part before first hyphen, or full cert_no if no hyphen)."""
    if not cert_no:
        return ""
    if "-" in cert_no:
        return cert_no.split("-", 1)[0]
    return cert_no


def extract_page_chars(page: pymupdf.Page) -> list[tuple[float, float, float, float, float, str]]:
    """Extract all characters with (mx, my, baseline_y, x0, x1, char) from page."""
    raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
    chars: list[tuple[float, float, float, float, float, str]] = []
    for block in raw.get("blocks", ()):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                for ch in span.get("chars", ()):
                    c = ch.get("c", "")
                    if not c:
                        continue
                    x0, y0, x1, y1 = ch["bbox"]
                    _, oy = ch["origin"]
                    chars.append(((x0 + x1) * 0.5, (y0 + y1) * 0.5, oy, x0, x1, c))
    return chars


def extract_cell_lines(
    chars: list[tuple[float, float, float, float, float, str]],
    cx0: float,
    cy0: float,
    cx1: float,
    cy1: float,
) -> list[tuple[str, float]]:
    """Return list of (line_text, line_max_x1) inside the given cell box."""
    cell_chars = [
        (oy, x0, x1, c)
        for (mx, my, oy, x0, x1, c) in chars
        if cx0 <= mx <= cx1 and cy0 <= my <= cy1
    ]
    if not cell_chars:
        return []

    cell_chars.sort(key=lambda item: (item[0], item[1]))
    lines: list[list[tuple[float, float, float, str]]] = []
    cur_y = cell_chars[0][0]
    cur_line = [cell_chars[0]]
    for item in cell_chars[1:]:
        if abs(item[0] - cur_y) <= 2.5:
            cur_line.append(item)
        else:
            lines.append(cur_line)
            cur_y = item[0]
            cur_line = [item]
    lines.append(cur_line)

    result: list[tuple[str, float]] = []
    for ln in lines:
        ln.sort(key=lambda item: item[1])
        text = "".join(item[3] for item in ln).strip()
        if text:
            max_x1 = max(item[2] for item in ln)
            result.append((text, max_x1))
    return result


def join_wrapped_lines(
    lines: list[tuple[str, float]],
    cell_x1: float,
    *,
    hard_break_sep: str = " ",
    keep_ascii_word_space: bool = False,
) -> str:
    """Join multi-line cell text cleanly, distinguishing soft wraps from hard line breaks."""
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0][0]

    parts = [lines[0][0]]
    for i in range(1, len(lines)):
        prev_text, prev_max_x1 = lines[i - 1]
        cur_text, _ = lines[i]
        # Soft wrap if previous line came close to the right edge of the cell
        is_soft_wrap = (cell_x1 - prev_max_x1) < 14.0
        if is_soft_wrap:
            if (
                keep_ascii_word_space
                and prev_text[-1].isascii()
                and prev_text[-1].isalpha()
                and cur_text[0].isascii()
                and cur_text[0].isalpha()
            ):
                parts.append(" " + cur_text)
            else:
                parts.append(cur_text)
        else:
            # Hard line break (e.g. building name on next line, factory branch name, revocation line)
            if cur_text[0] in "（(「『［[・、。":
                parts.append(cur_text)
            else:
                parts.append(hard_break_sep + cur_text)
    return "".join(parts).strip()


def clean_whitespace(text: str) -> str:
    """Collapse internal runs of spaces/tabs while preserving single spaces."""
    text = text.replace("\u3000", " ").replace("\r", "").replace("\n", " ")
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def normalize_date_jp(date_lines: list[tuple[str, float]]) -> str:
    """Join & normalize wrapped date_jp lines (fixing split 取消/失効/ISO-year wraps)."""
    if not date_lines:
        return ""
    raw = " ".join(ln[0].strip() for ln in date_lines if ln[0].strip())
    raw = clean_whitespace(raw)
    # Rejoin split cancellation / expiration words caused by narrow column wraps
    raw = re.sub(r"取\s+消", "取消", raw)
    raw = re.sub(r"失\s+効", "失効", raw)
    raw = re.sub(r"日\s+(取消|失効)", r"日\1", raw)
    # Rejoin wrapped ISO-style trailing date (e.g. "令和5年03月22日202 3-03-22")
    raw = re.sub(r"(\d{1,3})\s+(\d{1,3}-\d{2}-\d{2})", r"\1\2", raw)
    raw = re.sub(r"日(\d{4}-\d{2}-\d{2})", r"日 \1", raw)
    # Ensure a single space between two Japanese era dates if concatenated
    raw = re.sub(r"日(令和|平成|昭和|大正|明治)", r"日 \1", raw)
    return re.sub(r" {2,}", " ", raw).strip()


def merge_joint_applicants(
    prev: dict[str, str],
    new_app_parts: list[str],
    new_addr_parts: list[str],
) -> None:
    """Merge cross-page joint-applicant continuation rows into `prev` without duplication."""
    prev_apps = [p.strip() for p in prev["applicant"].split(" / ") if p.strip()]
    prev_addrs = [p.strip() for p in prev["address"].split(" / ") if p.strip()]

    # Build existing (app, addr) pairs for exact-pair deduplication
    max_prev = max(len(prev_apps), len(prev_addrs))
    existing_pairs: set[tuple[str, str]] = set()
    for i in range(max_prev):
        a = prev_apps[i] if i < len(prev_apps) else ""
        d = prev_addrs[i] if i < len(prev_addrs) else ""
        existing_pairs.add((a, d))

    max_new = max(len(new_app_parts), len(new_addr_parts))
    for i in range(max_new):
        a = new_app_parts[i] if i < len(new_app_parts) else ""
        d = new_addr_parts[i] if i < len(new_addr_parts) else ""
        if not a and not d:
            continue
        if (a, d) in existing_pairs:
            continue
        existing_pairs.add((a, d))
        if a:
            prev_apps.append(a)
        if d:
            prev_addrs.append(d)

    prev["applicant"] = " / ".join(prev_apps)
    prev["address"] = " / ".join(prev_addrs)


def parse_ledger_pdf(
    pdf_path: Path,
    *,
    category_id: str,
    category_name: str,
    period: str,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Parse a single MLIT TCPDF ledger PDF and return (records, stats)."""
    doc = pymupdf.open(pdf_path)
    records: list[dict[str, str]] = []
    stats: dict[str, Any] = {
        "filename": pdf_path.name,
        "pages": len(doc),
        "raw_rows": 0,
        "joint_applicant_rows": 0,
        "cross_page_merged_rows": 0,
        "revoked_note_rows": 0,
        "bad_date_rows": 0,
        "empty_cert_no_rows": 0,
        "anomalies": [],
    }

    # Page 0 (page 1) is always the Table of Contents; data starts on page index 1
    for pno in range(1, len(doc)):
        page = doc[pno]
        rects_set: set[tuple[float, float, float, float]] = set()
        for d in page.get_drawings():
            for item in d["items"]:
                if item[0] == "re":
                    r = item[1]
                    if r.width > 15 and r.height > 10 and r.y0 >= 35 and r.y1 <= 575:
                        rects_set.add(
                            (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))
                        )

        if not rects_set:
            continue

        full_width_rects = [r for r in rects_set if (r[2] - r[0]) > 700]
        stats["revoked_note_rows"] += len(full_width_rects)

        cell_rects = [r for r in rects_set if (r[2] - r[0]) <= 700]
        x_spans = sorted(set((r[0], r[2]) for r in cell_rects))
        if len(x_spans) < 6:
            stats["anomalies"].append(f"p{pno + 1}: unexpected column count {len(x_spans)}")
            continue

        # Standard 6 columns (indices 0..5); wall_multiplier also has col 6 (壁倍率)
        col_title_x = x_spans[0]
        col_app_x = x_spans[1]
        col_addr_x = x_spans[2]
        col_eval_x = x_spans[3]
        col_cert_x = x_spans[4]
        col_date_x = x_spans[5]

        # Each certification row corresponds uniquely to a rect in col 4 (cert_no)
        cert_rects = sorted(
            [r for r in cell_rects if (r[0], r[2]) == col_cert_x],
            key=lambda r: r[1],
        )
        app_rects = sorted(
            [r for r in cell_rects if (r[0], r[2]) == col_app_x],
            key=lambda r: r[1],
        )
        addr_rects = sorted(
            [r for r in cell_rects if (r[0], r[2]) == col_addr_x],
            key=lambda r: r[1],
        )

        chars = extract_page_chars(page)

        for r4 in cert_rects:
            y0, y1 = r4[1], r4[3]
            cert_lines = extract_cell_lines(chars, col_cert_x[0], y0, col_cert_x[1], y1)
            cert_raw = "".join(ln[0] for ln in cert_lines)

            # Skip repeating table header row on each page
            if cert_raw.strip() == "認定番号" or any(
                kw == cert_raw.strip() for kw in HEADER_KEYWORDS
            ):
                continue

            title_lines = extract_cell_lines(chars, col_title_x[0], y0, col_title_x[1], y1)
            eval_lines = extract_cell_lines(chars, col_eval_x[0], y0, col_eval_x[1], y1)
            date_lines = extract_cell_lines(chars, col_date_x[0], y0, col_date_x[1], y1)

            # Joint applications have multiple sub-rectangles in col 1 & col 2 inside [y0, y1]
            sub_app_rects = [
                r for r in app_rects if r[1] >= y0 - 1.0 and r[3] <= y1 + 1.0
            ]
            sub_addr_rects = [
                r for r in addr_rects if r[1] >= y0 - 1.0 and r[3] <= y1 + 1.0
            ]

            if len(sub_app_rects) > 1:
                stats["joint_applicant_rows"] += 1

            if sub_app_rects:
                app_parts = [
                    clean_whitespace(
                        join_wrapped_lines(
                            extract_cell_lines(chars, r[0], r[1], r[2], r[3]),
                            r[2],
                            hard_break_sep=" ",
                        )
                    )
                    for r in sub_app_rects
                ]
            else:
                app_parts = [
                    clean_whitespace(
                        join_wrapped_lines(
                            extract_cell_lines(chars, col_app_x[0], y0, col_app_x[1], y1),
                            col_app_x[1],
                            hard_break_sep=" ",
                        )
                    )
                ]
            app_parts = [p for p in app_parts if p]
            applicant = " / ".join(app_parts)

            if sub_addr_rects:
                addr_parts = [
                    clean_whitespace(
                        join_wrapped_lines(
                            extract_cell_lines(chars, r[0], r[1], r[2], r[3]),
                            r[2],
                            hard_break_sep=" ",
                        )
                    )
                    for r in sub_addr_rects
                ]
            else:
                addr_parts = [
                    clean_whitespace(
                        join_wrapped_lines(
                            extract_cell_lines(chars, col_addr_x[0], y0, col_addr_x[1], y1),
                            col_addr_x[1],
                            hard_break_sep=" ",
                        )
                    )
                ]
            addr_parts = [p for p in addr_parts if p]
            address = " / ".join(addr_parts)

            title = clean_whitespace(
                join_wrapped_lines(title_lines, col_title_x[1], hard_break_sep="")
            )
            evaluator = clean_whitespace(
                join_wrapped_lines(eval_lines, col_eval_x[1], hard_break_sep="")
            )
            cert_no = normalize_cert_no(cert_raw)

            # Date cell may wrap cancellation/expiration words across lines (e.g. 取 消 / 失 効)
            date_jp = normalize_date_jp(date_lines)
            date_iso = parse_jp_date_to_iso(date_jp)

            stats["raw_rows"] += 1
            if not cert_no:
                stats["empty_cert_no_rows"] += 1
                stats["anomalies"].append(f"p{pno + 1} y={y0}: empty cert_no (title={title[:30]})")
                continue
            if not date_iso:
                stats["bad_date_rows"] += 1
                if len(stats["anomalies"]) < 20:
                    stats["anomalies"].append(
                        f"p{pno + 1} cert={cert_no}: unparsed date_jp={date_jp!r}"
                    )

            # Merge consecutive cross-page joint-applicant continuation rows from TCPDF
            if records and (
                records[-1]["cert_no"] == cert_no
                and records[-1]["date"] == date_iso
                and records[-1]["title"] == title
                and records[-1]["evaluator"] == evaluator
                and records[-1]["period"] == period
            ):
                merge_joint_applicants(records[-1], app_parts, addr_parts)
                if len(date_jp) > len(records[-1]["date_jp"]):
                    records[-1]["date_jp"] = date_jp
                stats["cross_page_merged_rows"] += 1
                continue

            records.append(
                {
                    "cert_no": cert_no,
                    "prefix": extract_prefix(cert_no),
                    "category_id": category_id,
                    "category_name": category_name,
                    "date_jp": date_jp,
                    "date": date_iso,
                    "title": title,
                    "applicant": applicant,
                    "address": address,
                    "evaluator": evaluator,
                    "period": period,
                }
            )

    doc.close()
    return records, stats


def deduplicate_records(records: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    """Remove exact duplicate records while preserving order."""
    seen: set[tuple[str, ...]] = set()
    unique: list[dict[str, str]] = []
    dups = 0
    for rec in records:
        key = tuple(rec[f] for f in RECORD_FIELDS)
        if key in seen:
            dups += 1
            continue
        seen.add(key)
        unique.append(rec)
    return unique, dups


def write_category_outputs(
    category_id: str,
    records: list[dict[str, str]],
    data_dir: Path,
    docs_data_dir: Path,
) -> tuple[int, int, int]:
    """Write data JSON, data CSV (UTF-8 BOM), and minified docs JSON. Return sizes."""
    cat_data_dir = data_dir / "categories"
    cat_docs_dir = docs_data_dir / "categories"
    cat_data_dir.mkdir(parents=True, exist_ok=True)
    cat_docs_dir.mkdir(parents=True, exist_ok=True)

    json_path = cat_data_dir / f"{category_id}.json"
    csv_path = cat_data_dir / f"{category_id}.csv"
    docs_json_path = cat_docs_dir / f"{category_id}.json"

    # Compact JSON in both data/ and docs/data/ keeps repo size lean & fast
    # (data/ uses one-record-per-line readable JSON array; docs/data/ is fully minified)
    docs_payload = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    docs_json_path.write_text(docs_payload, encoding="utf-8")

    # Readable line-oriented JSON array in data/categories/ (git-diff friendly & small)
    lines = ["["]
    for i, rec in enumerate(records):
        comma = "," if i < len(records) - 1 else ""
        lines.append(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + comma)
    lines.append("]")
    json_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RECORD_FIELDS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(records)

    return (
        json_path.stat().st_size,
        csv_path.stat().st_size,
        docs_json_path.stat().st_size,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Parse all 42 MLIT ledger PDFs into JSON/CSV.")
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    ap.add_argument("--docs-data-dir", type=Path, default=DEFAULT_DOCS_DATA_DIR)
    args = ap.parse_args()

    sources_path = args.cache_dir / "sources.json"
    if not sources_path.exists():
        print(
            f"ERROR: {sources_path} not found. Run scripts/fetch_mlit_pdfs.py first.",
            file=sys.stderr,
        )
        return 1

    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    t0 = time.time()

    manifest_categories: list[dict[str, Any]] = []
    total_records = 0
    total_dups = 0
    total_joint = 0
    total_cross_page_merged = 0
    total_revoked_notes = 0
    total_bad_dates = 0
    total_empty_certs = 0
    all_dates: list[str] = []
    all_prefixes: Counter[str] = Counter()
    all_anomalies: list[str] = []
    max_file_bytes = 0

    print(f"Parsing {len(sources['categories'])} categories from {args.cache_dir}...\n")

    for cat in sources["categories"]:
        cat_id = cat["id"]
        cat_name = cat["name"]
        cat_group = cat["group"]

        cat_records: list[dict[str, str]] = []
        source_pdfs_meta: list[dict[str, Any]] = []

        for pdf_meta in cat["pdfs"]:
            pdf_path = args.cache_dir / pdf_meta["filename"]
            if not pdf_path.exists():
                raise FileNotFoundError(f"Missing cached PDF: {pdf_path}")

            recs, st = parse_ledger_pdf(
                pdf_path,
                category_id=cat_id,
                category_name=cat_name,
                period=pdf_meta["period"],
            )
            cat_records.extend(recs)
            total_joint += st["joint_applicant_rows"]
            total_cross_page_merged += st["cross_page_merged_rows"]
            total_revoked_notes += st["revoked_note_rows"]
            total_bad_dates += st["bad_date_rows"]
            total_empty_certs += st["empty_cert_no_rows"]
            for anom in st["anomalies"]:
                all_anomalies.append(f"[{cat_id}/{pdf_meta['filename']}] {anom}")

            source_pdfs_meta.append(
                {
                    "period": pdf_meta["period"],
                    "filename": pdf_meta["filename"],
                    "url": pdf_meta["url"],
                    "pages": st["pages"],
                    "raw_records": st["raw_rows"],
                }
            )

        deduped, dup_count = deduplicate_records(cat_records)
        total_dups += dup_count
        total_records += len(deduped)

        # Sort newest first (date desc, cert_no asc) for deterministic output
        deduped.sort(key=lambda r: (r["date"] or "0000-00-00", r["cert_no"]), reverse=True)

        prefixes = sorted({r["prefix"] for r in deduped if r["prefix"]})
        for r in deduped:
            if r["date"]:
                all_dates.append(r["date"])
            if r["prefix"]:
                all_prefixes[r["prefix"]] += 1

        j_sz, c_sz, dj_sz = write_category_outputs(
            cat_id, deduped, args.data_dir, args.docs_data_dir
        )
        max_file_bytes = max(max_file_bytes, j_sz, c_sz, dj_sz)

        manifest_categories.append(
            {
                "id": cat_id,
                "name": cat_name,
                "group": cat_group,
                "prefixes": prefixes,
                "record_count": len(deduped),
                "json_file": f"categories/{cat_id}.json",
                "csv_file": f"categories/{cat_id}.csv",
                "source_pdfs": source_pdfs_meta,
            }
        )

        print(
            f"  {cat_id:24s} | {len(deduped):6,d} recs "
            f"(dups={dup_count:2d}, prefixes={len(prefixes):3d}) | "
            f"docs_json={dj_sz / 1024 / 1024:5.2f} MB | csv={c_sz / 1024 / 1024:5.2f} MB"
        )

    min_date = min(all_dates) if all_dates else ""
    max_date = max(all_dates) if all_dates else ""

    manifest = {
        "schema_version": "1.0.0",
        "source_name": "建築基準法に基づく構造方法等の認定に係る帳簿等（国土交通省）",
        "source_url": sources["source_url"],
        "source_license": "公共データ利用規約（第1.0版）（PDL1.0）",
        "data_period": "平成12年04月01日～令和5年04月30日",
        "date_range": {"min": min_date, "max": max_date},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_records": total_records,
        "categories": manifest_categories,
    }

    args.data_dir.mkdir(parents=True, exist_ok=True)
    args.docs_data_dir.mkdir(parents=True, exist_ok=True)

    (args.data_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.docs_data_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    elapsed = time.time() - t0
    print(f"\n=== Parse Summary ({elapsed:.2f}s) ===")
    print(f"Total unique records : {total_records:,d} (exact duplicates removed: {total_dups:,d})")
    print(f"Unique prefixes      : {len(all_prefixes):,d}")
    print(f"Joint applicant rows : {total_joint:,d}")
    print(f"Cross-page merged    : {total_cross_page_merged:,d}")
    print(f"Revocation note rows : {total_revoked_notes:,d}")
    print(f"Date range (ISO)     : {min_date} .. {max_date}")
    print(f"Empty cert_no rows   : {total_empty_certs}")
    print(f"Unparsed date rows   : {total_bad_dates}")
    print(f"Largest output file  : {max_file_bytes / 1024 / 1024:.2f} MB")
    if all_anomalies:
        print(f"Anomalies ({len(all_anomalies)}):")
        for a in all_anomalies[:30]:
            print(f"  - {a}")
    else:
        print("Anomalies            : 0")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
