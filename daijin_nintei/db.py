"""Local database loader and search engine for MLIT Minister Certification records.

Reads ``data/manifest.json`` and lazily loads/indexes ``data/categories/*.json``
using pure Python standard library modules.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

from .decoder import normalize_cert_no

_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _normalize_search_text(text: str) -> str:
    """Normalize Japanese/ASCII text for case- and width-insensitive matching."""
    return unicodedata.normalize("NFKC", text).lower().strip()


class CertDatabase:
    """Indexed, lazy-loading interface over the local MLIT certification dataset."""

    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else _DEFAULT_DATA_DIR
        self.manifest_path = self.data_dir / "manifest.json"
        if not self.manifest_path.is_file():
            raise FileNotFoundError(
                f"manifest.json が見つかりません: {self.manifest_path}"
            )
        self._manifest: dict[str, Any] = json.loads(
            self.manifest_path.read_text(encoding="utf-8")
        )
        self._categories_meta: dict[str, dict[str, Any]] = {}
        self._prefix_to_categories: dict[str, list[str]] = {}
        self._categories_without_prefixes: list[str] = []

        for cat in self._manifest.get("categories", []):
            cat_id = str(cat["id"])
            self._categories_meta[cat_id] = cat
            prefixes = cat.get("prefixes") or []
            if not prefixes:
                self._categories_without_prefixes.append(cat_id)
            for pfx in prefixes:
                norm_pfx = normalize_cert_no(str(pfx))
                if not norm_pfx:
                    continue
                bucket = self._prefix_to_categories.setdefault(norm_pfx, [])
                if cat_id not in bucket:
                    bucket.append(cat_id)

        # Lazy caches
        self._loaded_categories: dict[str, list[dict[str, Any]]] = {}
        self._cert_index: dict[str, list[dict[str, Any]]] = {}
        self._all_loaded: bool = False

    @property
    def manifest(self) -> dict[str, Any]:
        """Return parsed ``manifest.json`` metadata."""
        return self._manifest

    @property
    def total_records(self) -> int:
        return int(self._manifest.get("total_records", 0))

    @property
    def data_period(self) -> str:
        return str(
            self._manifest.get(
                "data_period", "令和5年4月30日現在（国土交通省公開帳簿）"
            )
        )

    def list_categories(self) -> list[dict[str, Any]]:
        """Return category metadata entries from ``manifest.json``."""
        return list(self._manifest.get("categories", []))

    def get_category_meta(self, category_id: str) -> dict[str, Any] | None:
        return self._categories_meta.get(category_id)

    def _resolve_category_json_path(self, cat_meta: dict[str, Any]) -> Path:
        """Resolve a category JSON file path robustly across relative path styles."""
        json_file = cat_meta.get("json_file")
        cat_id = str(cat_meta["id"])
        candidates: list[Path] = []
        if isinstance(json_file, str) and json_file:
            p = Path(json_file)
            if p.is_absolute():
                candidates.append(p)
            else:
                candidates.append(self.data_dir / p)
                candidates.append(self.data_dir.parent / p)
                candidates.append(self.data_dir / "categories" / p.name)
        candidates.append(self.data_dir / "categories" / f"{cat_id}.json")

        for cand in candidates:
            if cand.is_file():
                return cand
        raise FileNotFoundError(
            f"カテゴリデータが見つかりません ({cat_id}): {candidates[0]}"
        )

    def load_category(self, category_id: str) -> list[dict[str, Any]]:
        """Lazily load and index a single category JSON file."""
        if category_id in self._loaded_categories:
            return self._loaded_categories[category_id]

        cat_meta = self._categories_meta.get(category_id)
        if cat_meta is None:
            raise KeyError(f"未知のカテゴリIDです: {category_id}")

        path = self._resolve_category_json_path(cat_meta)
        records = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError(f"カテゴリJSONが配列形式ではありません: {path}")

        self._loaded_categories[category_id] = records
        for rec in records:
            cert_no = rec.get("cert_no")
            if not isinstance(cert_no, str) or not cert_no.strip():
                continue
            key = normalize_cert_no(cert_no)
            if key:
                self._cert_index.setdefault(key, []).append(rec)

        if len(self._loaded_categories) == len(self._categories_meta):
            self._all_loaded = True
        return records

    def load_all(self) -> None:
        """Load all categories into memory."""
        if self._all_loaded:
            return
        for cat_id in self._categories_meta:
            if cat_id not in self._loaded_categories:
                self.load_category(cat_id)
        self._all_loaded = True

    def _candidate_categories_for_prefix(self, prefix: str) -> list[str]:
        norm_pfx = normalize_cert_no(prefix)
        cats = list(self._prefix_to_categories.get(norm_pfx, []))
        for cat_id in self._categories_without_prefixes:
            if cat_id not in cats:
                cats.append(cat_id)
        return cats

    def lookup_all(self, cert_no: str) -> list[dict[str, Any]]:
        """Look up all ledger records matching ``cert_no`` (width/dash normalized)."""
        key = normalize_cert_no(cert_no)
        if not key:
            return []

        if key in self._cert_index:
            return list(self._cert_index[key])

        prefix = key.split("-", 1)[0] if "-" in key else key
        target_cats = self._candidate_categories_for_prefix(prefix)
        for cat_id in target_cats:
            if cat_id not in self._loaded_categories:
                self.load_category(cat_id)

        if key in self._cert_index:
            return list(self._cert_index[key])

        # Fallback: if prefix wasn't indexed (e.g., unusual legacy key without hyphen),
        # check whether any remaining unloaded category could contain it when no
        # prefix-matched category existed and key has no hyphen.
        if not target_cats and "-" not in key and not self._all_loaded:
            self.load_all()
            return list(self._cert_index.get(key, []))

        return []

    def lookup(self, cert_no: str) -> dict[str, Any] | None:
        """Look up a single canonical ledger record for ``cert_no``.

        When multiple records share the same normalized ``cert_no``, prefers the
        most recent record (by ISO ``date`` descending, then ``H25_after``).
        """
        matches = self.lookup_all(cert_no)
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        return sorted(
            matches,
            key=lambda r: (
                str(r.get("date") or ""),
                1 if r.get("period") == "H25_after" else 0,
            ),
            reverse=True,
        )[0]

    def search_with_total(
        self,
        *,
        query: str | None = None,
        category_id: str | None = None,
        applicant: str | None = None,
        prefix: str | None = None,
        period: str | None = None,
        evaluator: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Search records with optional filters, returning ``(page_results, total_count)``."""
        # Determine minimal set of categories to load
        if category_id:
            if category_id not in self._categories_meta:
                return [], 0
            target_cats = [category_id]
        elif prefix:
            norm_pfx = normalize_cert_no(prefix)
            matched_cats: list[str] = []
            for pfx_key, cat_ids in self._prefix_to_categories.items():
                if pfx_key == norm_pfx or pfx_key.startswith(norm_pfx):
                    for cid in cat_ids:
                        if cid not in matched_cats:
                            matched_cats.append(cid)
            for cid in self._categories_without_prefixes:
                if cid not in matched_cats:
                    matched_cats.append(cid)
            target_cats = matched_cats
        else:
            target_cats = list(self._categories_meta.keys())

        query_terms = (
            [t for t in _normalize_search_text(query).split() if t]
            if query
            else []
        )
        norm_applicant = _normalize_search_text(applicant) if applicant else None
        norm_prefix = normalize_cert_no(prefix) if prefix else None
        norm_evaluator = _normalize_search_text(evaluator) if evaluator else None
        norm_period = period.strip() if period else None

        matched: list[dict[str, Any]] = []
        for cid in target_cats:
            records = self.load_category(cid)
            for rec in records:
                if norm_period and rec.get("period") != norm_period:
                    continue
                if norm_prefix:
                    rec_pfx = normalize_cert_no(str(rec.get("prefix") or ""))
                    rec_cert = normalize_cert_no(str(rec.get("cert_no") or ""))
                    if not (
                        rec_pfx == norm_pfx
                        or rec_pfx.startswith(norm_pfx)
                        or rec_cert.startswith(norm_pfx)
                    ):
                        continue
                if norm_applicant:
                    rec_app = _normalize_search_text(str(rec.get("applicant") or ""))
                    if norm_applicant not in rec_app:
                        continue
                if norm_evaluator:
                    rec_eval = _normalize_search_text(str(rec.get("evaluator") or ""))
                    if norm_evaluator not in rec_eval:
                        continue
                if query_terms:
                    haystack = _normalize_search_text(
                        " ".join(
                            str(rec.get(field) or "")
                            for field in (
                                "cert_no",
                                "prefix",
                                "title",
                                "applicant",
                                "address",
                                "evaluator",
                                "category_name",
                            )
                        )
                    )
                    if not all(term in haystack for term in query_terms):
                        continue
                matched.append(rec)

        total = len(matched)
        start = max(0, offset)
        if limit is not None and limit >= 0:
            page = matched[start : start + limit]
        else:
            page = matched[start:]
        return page, total

    def search(
        self,
        *,
        query: str | None = None,
        category_id: str | None = None,
        applicant: str | None = None,
        prefix: str | None = None,
        period: str | None = None,
        evaluator: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Search records with optional filters."""
        results, _ = self.search_with_total(
            query=query,
            category_id=category_id,
            applicant=applicant,
            prefix=prefix,
            period=period,
            evaluator=evaluator,
            limit=limit,
            offset=offset,
        )
        return results
