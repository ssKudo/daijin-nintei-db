"""Unit tests for daijin_nintei.db."""

from __future__ import annotations

from pathlib import Path

import pytest

from daijin_nintei.db import CertDatabase


class TestCertDatabase:
    def test_manifest_and_categories(self, mock_db: CertDatabase) -> None:
        assert mock_db.total_records == 6
        assert "令和5年4月30日" in mock_db.data_period
        cats = mock_db.list_categories()
        assert len(cats) == 3
        assert mock_db.get_category_meta("fireproof") is not None
        assert mock_db.get_category_meta("nonexistent") is None

    def test_lazy_lookup_with_prefix_routing(self, mock_db: CertDatabase) -> None:
        # Before lookup, no category JSON should be loaded yet
        assert len(mock_db._loaded_categories) == 0

        rec = mock_db.lookup("ＦＰ０６０ＢＥ－０１２３")
        assert rec is not None
        assert rec["cert_no"] == "FP060BE-0123"
        assert rec["applicant"] == "吉野石膏株式会社"
        # Only the fireproof category should have been loaded!
        assert list(mock_db._loaded_categories.keys()) == ["fireproof"]

        # Unlisted cert_no with valid prefix in same category returns None cleanly
        assert mock_db.lookup("FP060BE-9999") is None

        # Lookup in another category lazily loads only that category
        rec_nm = mock_db.lookup("nmー8585")
        assert rec_nm is not None
        assert rec_nm["cert_no"] == "NM-8585"
        assert set(mock_db._loaded_categories.keys()) == {"fireproof", "fire_material"}

    def test_search_filters(self, mock_db: CertDatabase) -> None:
        # Multi-term query (AND search)
        hits = mock_db.search(query="吉野石膏 せっこうボード")
        assert len(hits) == 2
        assert {r["cert_no"] for r in hits} == {"FP060BE-0123", "SOI-0160"}

        # Category filter
        hits_cat = mock_db.search(category_id="fireproof", applicant="ニチハ")
        assert len(hits_cat) == 1
        assert hits_cat[0]["cert_no"] == "QF045BE-1232"

        # Prefix filter (partial prefix match)
        hits_pfx = mock_db.search(prefix="QF045")
        assert len(hits_pfx) == 2
        assert {r["cert_no"] for r in hits_pfx} == {"QF045BE-1232", "QF045NE-0001"}

        # Period filter
        hits_before = mock_db.search(period="H25_before")
        assert {r["cert_no"] for r in hits_before} == {"QF045NE-0001", "NM-8585"}

        # Evaluator filter + pagination
        page, total = mock_db.search_with_total(evaluator="建材試験センター", limit=2, offset=0)
        assert total == 3
        assert len(page) == 2

    def test_missing_manifest_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError) as exc_info:
            CertDatabase(data_dir=tmp_path / "missing")
        msg = str(exc_info.value)
        assert "https://github.com/ssKudo/daijin-nintei-db" in msg
        assert "--data-dir" in msg
        assert "DAIJIN_NINTEI_DATA_DIR" in msg

    def test_env_var_data_dir_resolution(
        self, mock_data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DAIJIN_NINTEI_DATA_DIR", str(mock_data_dir))
        db = CertDatabase()
        assert db.data_dir == mock_data_dir
        assert db.total_records == 6
        assert db.lookup("FP060BE-0123") is not None
