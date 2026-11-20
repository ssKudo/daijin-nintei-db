"""Integration tests against the real MLIT dataset in ``data/``.

Automatically skips when ``data/manifest.json`` has not been generated yet,
and runs full validation against the real ledger once ``data/`` is present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from daijin_nintei.db import CertDatabase
from daijin_nintei.decoder import CertDecoder
from daijin_nintei.screener import (
    STATUS_FOUND,
    STATUS_INVALID_FORMAT,
    STATUS_UNLISTED_VALID_FORMAT,
    CertScreener,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REAL_DATA_DIR = _REPO_ROOT / "data"
_HAS_REAL_DATA = (
    (_REAL_DATA_DIR / "manifest.json").is_file()
    and (_REAL_DATA_DIR / "code_rules.json").is_file()
)

pytestmark = pytest.mark.skipif(
    not _HAS_REAL_DATA,
    reason="Real data/manifest.json has not been generated yet",
)


class TestRealDataIntegration:
    def test_manifest_and_code_rules_load_cleanly(self) -> None:
        db = CertDatabase(data_dir=_REAL_DATA_DIR)
        assert db.total_records >= 93000
        assert "tk_000042.html" in db.source_url
        assert "令和8年" in db.data_period
        cats = db.list_categories()
        assert len(cats) == 21

        rules_path = _REAL_DATA_DIR / "code_rules.json"
        assert rules_path.is_file()
        decoder = CertDecoder(rules_path=rules_path)
        assert decoder.rules_source == str(rules_path)
        assert len(decoder.exact_codes) >= 180

        # Bundled wheel code_rules.json must stay in sync with data/code_rules.json
        bundled = _REPO_ROOT / "daijin_nintei" / "bundled_code_rules.json"
        assert bundled.is_file()
        assert bundled.read_text(encoding="utf-8") == rules_path.read_text(encoding="utf-8")

    def test_all_real_records_decode_and_extract_zero_invalid(self) -> None:
        db = CertDatabase(data_dir=_REAL_DATA_DIR)
        decoder = CertDecoder(rules_path=_REAL_DATA_DIR / "code_rules.json")
        screener = CertScreener(db=db, decoder=decoder)
        db.load_all()

        invalid_examples: list[tuple[str, str, list[str]]] = []
        unextracted_examples: list[tuple[str, str]] = []
        total_checked = 0
        for cid, records in db._loaded_categories.items():
            for rec in records:
                total_checked += 1
                cn = rec["cert_no"]
                res = decoder.decode(cn)
                if not res.is_valid_format:
                    if len(invalid_examples) < 10:
                        invalid_examples.append((cid, cn, res.warnings))
                cands = screener.extract_candidates_from_line(
                    f"仕様: {cn}", source="audit", location="L1"
                )
                if not any(c.cert_no == cn for c in cands):
                    if len(unextracted_examples) < 10:
                        unextracted_examples.append((cid, cn))

        assert total_checked == db.total_records
        assert invalid_examples == [], f"Unexpected invalid decodes: {invalid_examples}"
        assert unextracted_examples == [], f"Unexpected unextracted certs: {unextracted_examples}"

    def test_real_v2_symbols_ed_eh090_and_multi_cert_line(self) -> None:
        from daijin_nintei.cli import main

        db = CertDatabase(data_dir=_REAL_DATA_DIR)
        decoder = CertDecoder(rules_path=_REAL_DATA_DIR / "code_rules.json")
        screener = CertScreener(db=db, decoder=decoder)

        # ED-0001: 小規模な竪穴区画の開口部 (令第112条第12項), NOT 防火ダンパー
        dec_ed = decoder.decode("ED-0001")
        assert dec_ed.is_valid_format is True
        assert "小規模な竪穴区画" in (dec_ed.category or "")
        assert "112条第12項" in (dec_ed.law_article or "")
        assert "ダンパー" not in (dec_ed.description or "")
        assert db.lookup("ED-0001") is not None

        # EH090-0001 & EH060-0003: 防火設備（遮熱・90分 / 60分）
        dec_eh90 = decoder.decode("EH090-0001")
        assert dec_eh90.is_valid_format is True
        assert dec_eh90.duration_min == 90
        assert "防火設備" in (dec_eh90.category or "")
        assert db.lookup("EH090-0001") is not None
        assert db.lookup("EH060-0003") is not None

        # Screener extracts & matches ED-0001, EH090-0001, QFRM-0001, MNNWNNN-12104-1
        rep = screener.screen_text(
            "竪穴開口部 ED-0001\n火熱遮断壁防火設備 EH090-0001\n準耐力壁 QFRM-0001\n建築物 MNNWNNN-12104-1\n"
        )
        by_cert = {o.cert_no: o for o in rep.occurrences}
        for code in ("ED-0001", "EH090-0001", "QFRM-0001", "MNNWNNN-12104-1"):
            assert code in by_cert
            assert by_cert[code].status == STATUS_FOUND

        # Review item 4 exact regression against real data
        rep_multi = screener.screen_text(
            "外壁 60分 QF045BE-1232 ／ 間仕切壁 45分 QF045BP-0001"
        )
        multi_map = {o.cert_no: o for o in rep_multi.occurrences}
        assert {w["code"] for w in multi_map["QF045BE-1232"].warnings} == {
            "DURATION_MISMATCH"
        }
        assert multi_map["QF045BP-0001"].warnings == []

        # Review item 2: SOI-0249 exists in DB, yet --no-db MUST report UNCHECKED
        assert db.lookup("SOI-0249") is not None
        assert main(["decode", "SOI-0249", "--no-db", "--json"]) == 0

    def test_real_lookup_search_and_decode_roundtrip(self) -> None:
        db = CertDatabase(data_dir=_REAL_DATA_DIR)
        decoder = CertDecoder(rules_path=_REAL_DATA_DIR / "code_rules.json")

        # Search well-known prefixes across multiple categories
        for pfx in ("NM", "FP060BE", "QF045BE", "SOI", "PS060WL", "TFBJ"):
            hits = db.search(prefix=pfx, limit=3)
            assert len(hits) >= 1, f"Expected hits for prefix {pfx}"
            sample_cert = hits[0]["cert_no"]

            looked_up = db.lookup(sample_cert)
            assert looked_up is not None
            assert looked_up["cert_no"] == sample_cert

            dec = decoder.decode(sample_cert)
            assert dec.is_valid_format is True
            assert dec.prefix == pfx

    def test_real_batch_screening_mixed_scenario(self) -> None:
        db = CertDatabase(data_dir=_REAL_DATA_DIR)
        decoder = CertDecoder(rules_path=_REAL_DATA_DIR / "code_rules.json")
        screener = CertScreener(db=db, decoder=decoder)

        # Grab genuine records including revoked ones (from fireproof & CAS-0284) and non-fire categories
        fp_hits = db.search(prefix="FP060BE", limit=1)
        nm_hits = db.search(prefix="NM", limit=1)
        tfb_hits = db.search(prefix="TFBJ", limit=1)
        elev_hits = db.search(category_id="elevators", limit=1)
        revoked_fp_list = [
            r
            for r in db.load_category("fireproof")
            if "取消" in r.get("date_jp", "") or "失効" in r.get("date_jp", "")
        ]
        revoked_cas = db.lookup("CAS-0284")
        assert fp_hits and nm_hits and tfb_hits and elev_hits and revoked_fp_list and revoked_cas

        real_fp = fp_hits[0]["cert_no"]
        real_nm = nm_hits[0]["cert_no"]
        real_tfb = tfb_hits[0]["cert_no"]
        real_elev = elev_hits[0]["cert_no"]
        real_revoked_fp = revoked_fp_list[0]["cert_no"]

        sample_text = "\n".join(
            [
                f"外壁仕様: {real_fp}",
                f"内装仕上（不燃材料）: {real_nm}",
                f"鉄骨工場認定: {real_tfb}",
                f"エレベーター認定: {real_elev}",
                f"取消済テスト1: {real_revoked_fp}",
                "取消済テスト2: CAS-0284",
                "新規未掲載認定候補: QF045BE-99999（45分準耐火外壁・耐力壁）",
                "矛盾テスト: 60分耐火外壁 QF045NE-99998",
                "不正形式: FP060XX-0001",
            ]
        )
        report = screener.screen_text(sample_text, source="real_integration.txt")
        by_cert = {o.cert_no: o for o in report.occurrences}

        assert by_cert[real_fp].status == STATUS_FOUND
        assert by_cert[real_nm].status == STATUS_FOUND
        assert by_cert[real_tfb].status == STATUS_FOUND
        assert by_cert[real_elev].status == STATUS_FOUND
        assert by_cert[real_revoked_fp].status == STATUS_FOUND
        assert any(
            w["code"] == "CERT_REVOKED_OR_EXPIRED"
            for w in by_cert[real_revoked_fp].warnings
        )
        assert by_cert["CAS-0284"].status == STATUS_FOUND
        assert any(
            w["code"] == "CERT_REVOKED_OR_EXPIRED"
            for w in by_cert["CAS-0284"].warnings
        )
        assert by_cert["QF045BE-99999"].status == STATUS_UNLISTED_VALID_FORMAT
        assert by_cert["QF045NE-99998"].status == STATUS_UNLISTED_VALID_FORMAT
        assert {w["code"] for w in by_cert["QF045NE-99998"].warnings} >= {
            "DURATION_MISMATCH",
            "STRUCTURE_TYPE_MISMATCH",
        }
        assert by_cert["FP060XX-0001"].status == STATUS_INVALID_FORMAT

    def test_real_non_wall_cli_and_csv_load_bearing_labels(self, capsys) -> None:
        import csv
        import io
        from daijin_nintei.cli import main

        rc = main(["decode", "FP120CN-0001", "FP030RF-0001"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "耐力あり（主要構造部等）" in out
        assert "耐力区分     : 耐力壁" not in out
        assert "耐力区分     : 非耐力壁" not in out
        assert "耐力区分     : 非耐力" in out

        db = CertDatabase(data_dir=_REAL_DATA_DIR)
        decoder = CertDecoder(rules_path=_REAL_DATA_DIR / "code_rules.json")
        screener = CertScreener(db=db, decoder=decoder)
        report = screener.screen_text("柱 FP120CN-0001\n屋根 FP030RF-0001\n")
        rows = {r["cert_no"]: r for r in csv.DictReader(io.StringIO(report.to_csv()))}
        assert rows["FP120CN-0001"]["load_bearing"] == "耐力あり（主要構造部等）"
        assert rows["FP030RF-0001"]["load_bearing"] == "非耐力"
