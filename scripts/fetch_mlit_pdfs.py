#!/usr/bin/env python3
"""Download official MLIT Minister Certification ledger & reference PDFs.

Source: https://www.mlit.go.jp/jutakukentiku/build/jutakukentiku_house_tk_000042.html
License: Public Data License (Version 1.0) / PDL1.0

Idempotent by default: skips already-downloaded valid PDFs unless --force is given.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

SOURCE_URL = "https://www.mlit.go.jp/jutakukentiku/build/jutakukentiku_house_tk_000042.html"
BASE_URL = "https://www.mlit.go.jp"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Reference PDFs hosted on the official MLIT tk_000042.html page
REFERENCE_PDFS: list[dict[str, str]] = [
    {
        "id": "ref_buildings",
        "filename": "001069848.pdf",
        "url": "https://www.mlit.go.jp/common/001069848.pdf",
        "title": "認定の区分と対象条文（建築物・工作物関連）",
    },
    {
        "id": "ref_fire_materials",
        "filename": "001069853.pdf",
        "url": "https://www.mlit.go.jp/common/001069853.pdf",
        "title": "認定の区分と対象条文（防耐火構造・防火材料ほか）",
    },
    {
        "id": "ref_elevators",
        "filename": "001069849.pdf",
        "url": "https://www.mlit.go.jp/common/001069849.pdf",
        "title": "認定の区分と対象条文（昇降機・遊戯施設関連）",
    },
    {
        "id": "ref_variations",
        "filename": "001069850.pdf",
        "url": "https://www.mlit.go.jp/common/001069850.pdf",
        "title": "仕様にバリエーションがある場合の取扱いについて",
    },
    {
        "id": "ref_english_codes",
        "filename": "001709467.pdf",
        "url": "https://www.mlit.go.jp/jutakukentiku/build/content/001709467.pdf",
        "title": "（参考資料）認定記号の英語表記について",
    },
]

# Stable mapping from table row category keywords -> canonical (id, clean_name)
# Note: More specific keywords MUST appear before broader substrings (e.g. 柱の防火被覆 before 建築物).
CATEGORY_MAPPING: list[tuple[str, str, str]] = [
    ("柱の防火被覆", "fire_other", "その他（柱の防火被覆、不燃性を有する建築物の部分等、ほか）"),
    ("建築物", "buildings", "建築物（時刻歴・耐火・避難）"),
    ("工作物", "structures", "工作物"),
    ("準耐火構造", "quasi_fireproof", "準耐火構造"),
    ("防火構造", "fire_resistive", "防火構造・準防火構造"),
    ("耐火構造", "fireproof", "耐火構造"),
    ("飛び火構造", "fire_brand", "飛び火構造"),
    ("不燃材料", "fire_materials", "不燃材料・準不燃材料・難燃材料"),
    ("防火設備", "fire_doors", "防火設備・特定防火設備"),
    ("壁倍率", "wall_multiplier", "壁倍率"),
    ("鋼材の接合部", "steel_joints", "鋼材の接合部"),
    ("指定建築材料（１）", "designated_materials_1", "指定建築材料（１）（鋼材・ボルト・ケーブル・免震材料・木質パネル等）"),
    ("指定建築材料（２）", "designated_materials_2", "指定建築材料（２）（膜材料・石綿飛散防止材・コンクリート等）"),
    ("鉄骨工場", "steel_factories", "鉄骨工場"),
    ("区画貫通", "penetration", "区画貫通"),
    ("界壁遮音", "sound_insulation", "界壁遮音・天井遮音"),
    ("ホルムアルデヒド", "sick_house", "ホルムアルデヒド発散建築材料・その他"),
    ("図書省略", "doc_omission", "図書省略（構造計算プログラム・プレハブ駐車場・基礎杭等）"),
    ("浄化槽", "building_equipment", "浄化槽・換気設備・非常用照明"),
    ("昇降機", "elevators", "昇降機（エレベーター・エスカレーター・小荷物専用昇降機）"),
    ("遊戯施設", "amusement_facilities", "遊戯施設（コースター等）"),
]


class LedgerTableParser(HTMLParser):
    """Parse MLIT's table.tbl_nintei_list into structured rows."""

    def __init__(self) -> None:
        super().__init__()
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.current_cell_tag = ""
        self.current_cell_attrs: dict[str, str] = {}
        self.current_cell_text: list[str] = []
        self.current_cell_links: list[str] = []
        self.current_row: list[dict[str, Any]] = []
        self.rows: list[list[dict[str, Any]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k: (v or "") for k, v in attrs}
        if tag == "table" and "tbl_nintei_list" in attr_dict.get("class", ""):
            self.in_table = True
        elif self.in_table and tag == "tr":
            self.in_row = True
            self.current_row = []
        elif self.in_row and tag in ("td", "th"):
            self.in_cell = True
            self.current_cell_tag = tag
            self.current_cell_attrs = attr_dict
            self.current_cell_text = []
            self.current_cell_links = []
        elif self.in_cell and tag == "a":
            href = attr_dict.get("href", "").strip()
            if href:
                self.current_cell_links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self.in_table:
            self.in_table = False
        elif tag == "tr" and self.in_row:
            self.in_row = False
            if self.current_row:
                self.rows.append(self.current_row)
        elif tag in ("td", "th") and self.in_cell:
            self.in_cell = False
            text = re.sub(r"\s+", " ", "".join(self.current_cell_text)).strip()
            self.current_row.append(
                {
                    "tag": self.current_cell_tag,
                    "rowspan": int(self.current_cell_attrs.get("rowspan") or 1),
                    "colspan": int(self.current_cell_attrs.get("colspan") or 1),
                    "text": text,
                    "links": list(self.current_cell_links),
                }
            )

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.current_cell_text.append(data)


def map_category(raw_name: str) -> tuple[str, str]:
    """Match MLIT table row label to canonical (category_id, display_name)."""
    normalized = raw_name.replace(" ", "").replace("　", "")
    for keyword, cat_id, clean_name in CATEGORY_MAPPING:
        if keyword in normalized:
            return cat_id, clean_name
    raise ValueError(f"Unmapped MLIT ledger category: {raw_name!r}")


def parse_ledger_index(html_text: str) -> tuple[str, list[dict[str, Any]]]:
    """Extract page last-updated text and all 21 ledger category definitions."""
    m = re.search(r"最終更新日\s*([^<\r\n]+)", html_text)
    last_updated = m.group(1).strip() if m else ""

    parser = LedgerTableParser()
    parser.feed(html_text)

    categories: list[dict[str, Any]] = []
    current_group = ""
    group_remaining = 0

    for row in parser.rows:
        # Skip header row
        if all(cell["tag"] == "th" for cell in row):
            continue

        idx = 0
        if group_remaining > 0:
            group = current_group
            group_remaining -= 1
        else:
            group_cell = row[idx]
            group = group_cell["text"].strip()
            current_group = group
            group_remaining = group_cell["rowspan"] - 1
            idx += 1

        name_cell = row[idx]
        symbols_cell = row[idx + 1]
        h25_after_cell = row[idx + 2]
        h25_before_cell = row[idx + 3]

        cat_id, clean_name = map_category(name_cell["text"])
        symbols_raw = symbols_cell["text"].replace("\u00a0", "").strip()

        pdfs: list[dict[str, Any]] = []
        for period, cell in (("H25_after", h25_after_cell), ("H25_before", h25_before_cell)):
            if not cell["links"]:
                continue
            href = cell["links"][0]
            full_url = urljoin(BASE_URL, href)
            filename = Path(href).name
            pdfs.append(
                {
                    "period": period,
                    "filename": filename,
                    "url": full_url,
                }
            )

        categories.append(
            {
                "id": cat_id,
                "group": group,
                "name": clean_name,
                "name_raw": name_cell["text"],
                "symbols_raw": symbols_raw,
                "pdfs": pdfs,
            }
        )

    return last_updated, categories


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_valid_pdf(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 100:
        return False
    with path.open("rb") as f:
        return f.read(5) == b"%PDF-"


def download_pdf(
    session: requests.Session,
    url: str,
    dest: Path,
    *,
    force: bool = False,
    sleep_sec: float = 0.25,
    retries: int = 3,
) -> tuple[bool, int, str]:
    """Download a single PDF idempotently. Returns (downloaded, size_bytes, sha256)."""
    if not force and is_valid_pdf(dest):
        return False, dest.stat().st_size, sha256_file(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=60, stream=True)
            resp.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with tmp.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
            if not is_valid_pdf(tmp):
                tmp.unlink(missing_ok=True)
                raise ValueError(f"Downloaded file is not a valid PDF: {url}")
            tmp.replace(dest)
            time.sleep(sleep_sec)
            return True, dest.stat().st_size, sha256_file(dest)
        except Exception as exc:
            last_err = exc
            wait = sleep_sec * (2**attempt)
            print(f"  [retry {attempt}/{retries}] {url}: {exc} (wait {wait:.1f}s)", file=sys.stderr)
            time.sleep(wait)

    raise RuntimeError(f"Failed to download {url}: {last_err}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch MLIT Minister Certification PDFs.")
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / ".cache" / "pdfs",
        help="Destination directory for cached PDFs and sources.json",
    )
    ap.add_argument("--force", action="store_true", help="Re-download existing PDFs")
    ap.add_argument("--sleep", type=float, default=0.25, help="Polite delay between requests (sec)")
    args = ap.parse_args()

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    print(f"Fetching MLIT ledger index: {SOURCE_URL}")
    resp = session.get(SOURCE_URL, timeout=30)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    html_text = resp.text

    # Cache the raw HTML index for reproducibility
    (out_dir / "index.html").write_text(html_text, encoding="utf-8")

    last_updated, categories = parse_ledger_index(html_text)
    print(f"Index last updated: {last_updated} | Categories found: {len(categories)}")
    seen_ids = [c["id"] for c in categories]
    if len(seen_ids) != len(set(seen_ids)):
        raise RuntimeError(f"Duplicate category IDs detected: {seen_ids}")
    if len(categories) != 21:
        print(f"WARNING: Expected 21 categories, got {len(categories)}", file=sys.stderr)

    # Also check HTML for any linked reference PDFs to verify URLs
    ref_results: list[dict[str, Any]] = []
    downloaded_count = 0
    skipped_count = 0

    print(f"\nDownloading {len(REFERENCE_PDFS)} reference PDFs...")
    for ref in REFERENCE_PDFS:
        dest = out_dir / ref["filename"]
        dl, size, digest = download_pdf(
            session, ref["url"], dest, force=args.force, sleep_sec=args.sleep
        )
        if dl:
            downloaded_count += 1
            status = "DOWNLOADED"
        else:
            skipped_count += 1
            status = "CACHED"
        print(f"  [{status:10s}] {ref['filename']} ({size:,} bytes) - {ref['title']}")
        ref_results.append(
            {
                **ref,
                "size_bytes": size,
                "sha256": digest,
            }
        )

    total_ledger_pdfs = sum(len(c["pdfs"]) for c in categories)
    print(f"\nDownloading {total_ledger_pdfs} ledger PDFs across {len(categories)} categories...")
    for cat in categories:
        for pdf in cat["pdfs"]:
            dest = out_dir / pdf["filename"]
            dl, size, digest = download_pdf(
                session, pdf["url"], dest, force=args.force, sleep_sec=args.sleep
            )
            if dl:
                downloaded_count += 1
                status = "DOWNLOADED"
            else:
                skipped_count += 1
                status = "CACHED"
            pdf["size_bytes"] = size
            pdf["sha256"] = digest
            print(
                f"  [{status:10s}] {cat['id']:22s} {pdf['period']:10s} "
                f"{pdf['filename']} ({size:,} bytes)"
            )

    sources_meta = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_url": SOURCE_URL,
        "page_last_updated": last_updated,
        "reference_pdfs": ref_results,
        "categories": categories,
    }
    sources_path = out_dir / "sources.json"
    sources_path.write_text(
        json.dumps(sources_meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Prune obsolete cached PDFs from older MLIT index pages so .cache/pdfs/ stays clean
    active_filenames = {r["filename"] for r in ref_results}
    for cat in categories:
        for pdf in cat["pdfs"]:
            active_filenames.add(pdf["filename"])
    pruned = 0
    for existing_pdf in out_dir.glob("*.pdf"):
        if existing_pdf.name not in active_filenames:
            existing_pdf.unlink()
            pruned += 1
    if pruned:
        print(f"Pruned {pruned} obsolete PDF(s) from {out_dir}")

    print(
        f"\nDone. Downloaded: {downloaded_count}, Skipped (cached): {skipped_count}. "
        f"Metadata saved to {sources_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
