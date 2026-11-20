"""Unit tests for daijin_nintei.screener."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from daijin_nintei.decoder import SEINOKYO_SEARCH_URL
from daijin_nintei.screener import (
    STATUS_FOUND,
    STATUS_INVALID_FORMAT,
    STATUS_UNCHECKED,
    STATUS_UNLISTED_VALID_FORMAT,
    CertScreener,
)


class TestExtractionAndStatusClassification:
    def test_status_classification_and_noise_exclusion(
        self, mock_screener: CertScreener
    ) -> None:
        sample = "\n".join(
            [
                "1. 外壁（耐力壁）60分耐火構造: ＦＰ０６０ＢＥ－０１２３（吉野石膏）",
                "2. 最新2026年認定予定番号: QF045BE-9999（45分準耐火外壁・耐力壁）",
                "3. 形式不正例: FP060XX-0001 および 大臣認定番号：XYZ-9999",
                "4. ノイズ除外対象: 2024-05-01, TEL 03-1234-5678, JIS-A-5430, ISO-9001, UTF-8, DR-01, https://example.com/FP060BE-0123",
            ]
        )
        report = mock_screener.screen_text(sample, source="sample.txt")

        by_cert = {o.cert_no: o for o in report.occurrences}
        assert set(by_cert.keys()) == {
            "FP060BE-0123",
            "QF045BE-9999",
            "FP060XX-0001",
            "XYZ-9999",
        }

        # FOUND
        occ_found = by_cert["FP060BE-0123"]
        assert occ_found.status == STATUS_FOUND
        assert occ_found.ledger_record is not None
        assert occ_found.ledger_record["applicant"] == "吉野石膏株式会社"
        assert occ_found.warnings == []

        # UNLISTED_VALID_FORMAT (zero-hallucination: never condemned as invalid!)
        occ_unlisted = by_cert["QF045BE-9999"]
        assert occ_unlisted.status == STATUS_UNLISTED_VALID_FORMAT
        assert "未掲載" in occ_unlisted.status_label
        assert occ_unlisted.seinokyo_url == SEINOKYO_SEARCH_URL
        assert occ_unlisted.ledger_record is None
        assert occ_unlisted.warnings == []

        # INVALID_FORMAT
        assert by_cert["FP060XX-0001"].status == STATUS_INVALID_FORMAT
        assert by_cert["XYZ-9999"].status == STATUS_INVALID_FORMAT

        # Summary counts
        assert report.summary["total_occurrences"] == 4
        assert report.summary["found_count"] == 1
        assert report.summary["unlisted_valid_count"] == 1
        assert report.summary["unchecked_count"] == 0
        assert report.summary["invalid_format_count"] == 2

    def test_no_db_mode_returns_unchecked_never_unlisted(self) -> None:
        screener = CertScreener(db=None, auto_load_db=False)
        sample = "遮音界壁: SOI-0249\n防火設備: EH090-0001\n不正形式: FP060XX-0001\n"
        report = screener.screen_text(sample, source="no_db.txt")
        by_cert = {o.cert_no: o for o in report.occurrences}

        assert by_cert["SOI-0249"].status == STATUS_UNCHECKED
        assert by_cert["EH090-0001"].status == STATUS_UNCHECKED
        assert by_cert["FP060XX-0001"].status == STATUS_INVALID_FORMAT
        assert report.summary["unchecked_count"] == 2
        assert report.summary["unique_unchecked_count"] == 2
        assert report.summary["unlisted_valid_count"] == 0
        assert report.summary["found_count"] == 0
        md = report.to_markdown()
        assert "UNCHECKED" in md
        assert "未掲載ではありません" in md
        csv_rows = list(csv.DictReader(io.StringIO(report.to_csv())))
        assert {r["cert_no"]: r["status"] for r in csv_rows} == {
            "SOI-0249": STATUS_UNCHECKED,
            "EH090-0001": STATUS_UNCHECKED,
            "FP060XX-0001": STATUS_INVALID_FORMAT,
        }

    def test_extracts_latest_v2_symbols(self, mock_screener: CertScreener) -> None:
        sample = (
            "開口部: ED-0001 / 防火設備: EH090-0001, EH060-0003, EQ060-0001 / "
            "耐火壁: FPLH060BE-0001 / 準防火外壁: QPEP045BE-0001 / "
            "木造耐力壁: QFRM-0001 / 材料: MCMU-0001, MCLT-0001, MBLK-0001"
        )
        report = mock_screener.screen_text(sample)
        found_nos = {o.cert_no for o in report.occurrences}
        expected = {
            "ED-0001",
            "EH090-0001",
            "EH060-0003",
            "EQ060-0001",
            "FPLH060BE-0001",
            "QPEP045BE-0001",
            "QFRM-0001",
            "MCMU-0001",
            "MCLT-0001",
            "MBLK-0001",
        }
        assert expected <= found_nos
        for occ in report.occurrences:
            assert occ.decode["is_valid_format"] is True


class TestContradictionDetection:
    def test_duration_and_structure_type_mismatch(
        self, mock_screener: CertScreener
    ) -> None:
        text = "外壁仕様：60分耐火外壁 QF045NE-0001"
        report = mock_screener.screen_text(text)
        assert len(report.occurrences) == 1
        codes = {w["code"] for w in report.occurrences[0].warnings}
        assert "DURATION_MISMATCH" in codes
        assert "STRUCTURE_TYPE_MISMATCH" in codes

    def test_hour_unit_duration_mismatch(self, mock_screener: CertScreener) -> None:
        text = "1時間耐火壁 QF045BE-1232"
        report = mock_screener.screen_text(text)
        codes = {w["code"] for w in report.occurrences[0].warnings}
        assert "DURATION_MISMATCH" in codes

    def test_load_bearing_mismatch_both_directions(
        self, mock_screener: CertScreener
    ) -> None:
        # Context says 耐力壁 while code is NE (non-load-bearing)
        r1 = mock_screener.screen_text("耐力壁（外壁）FP060NE-0001")
        assert any(w["code"] == "LOAD_BEARING_MISMATCH" for w in r1.occurrences[0].warnings)

        # Context says 非耐力壁 while code is BE (load-bearing)
        r2 = mock_screener.screen_text("非耐力壁（外壁）FP060BE-0123")
        assert any(w["code"] == "LOAD_BEARING_MISMATCH" for w in r2.occurrences[0].warnings)

        # Matching 非耐力壁 + NE produces NO load-bearing warning
        r3 = mock_screener.screen_text("非耐力壁（外壁）60分耐火構造 FP060NE-0001")
        assert r3.occurrences[0].warnings == []

    def test_part_mismatch_and_no_false_positive_compounds(
        self, mock_screener: CertScreener
    ) -> None:
        # Context says 床 while code is BE (exterior wall)
        r1 = mock_screener.screen_text("2階床構造：FP060BE-0123")
        assert any(w["code"] == "PART_MISMATCH" for w in r1.occurrences[0].warnings)

        # Compound words like 床面積 or 階段室 must NOT cause false PART_MISMATCH
        r2 = mock_screener.screen_text("延床面積500m2の階段室周囲の外壁 FP060BE-0123")
        assert not any(w["code"] == "PART_MISMATCH" for w in r2.occurrences[0].warnings)

    def test_applicant_mismatch_and_alias_match(
        self, mock_screener: CertScreener
    ) -> None:
        # Ledger applicant for FP060BE-0123 is 吉野石膏株式会社, context says ニチハ
        r1 = mock_screener.screen_text("外壁仕様（ニチハ製）FP060BE-0123")
        assert any(w["code"] == "APPLICANT_MISMATCH" for w in r1.occurrences[0].warnings)

        # Full corporate name "ニチハ株式会社" must NOT duplicate as "ニチハ株式会社・ニチハ"
        r_corp = mock_screener.screen_text("外壁仕様（ニチハ株式会社）FP060BE-0123")
        app_warns = [
            w for w in r_corp.occurrences[0].warnings if w["code"] == "APPLICANT_MISMATCH"
        ]
        assert len(app_warns) == 1
        assert "ニチハ株式会社・ニチハ" not in app_warns[0]["message"]
        assert "ニチハ株式会社" in app_warns[0]["message"]

        # Alias match: KMEW <-> ケイミュー株式会社 should NOT trigger mismatch
        r2 = mock_screener.screen_text("外壁（KMEW）QF045NE-0001")
        assert not any(
            w["code"] == "APPLICANT_MISMATCH" for w in r2.occurrences[0].warnings
        )

    def test_non_wall_load_bearing_csv_label(self) -> None:
        from daijin_nintei.decoder import format_load_bearing_label

        assert format_load_bearing_label(True, part_code="BE", part_group="外壁") == "耐力壁"
        assert format_load_bearing_label(False, part_code="NE", part_group="外壁") == "非耐力壁"
        assert (
            format_load_bearing_label(True, part_code="CN", part_group="柱")
            == "耐力あり（主要構造部等）"
        )
        assert format_load_bearing_label(False, part_code="RF", part_group="屋根") == "非耐力"
        assert format_load_bearing_label(None, part_code="ST", part_group="階段", empty="") == ""

    def test_multi_cert_line_isolates_local_context(
        self, mock_screener: CertScreener
    ) -> None:
        line = "外壁（耐力壁・45分準耐火）QF045BE-1232 / 60分耐火床 FP060FL-9999"
        report = mock_screener.screen_text(line)
        assert len(report.occurrences) == 2
        for occ in report.occurrences:
            assert occ.warnings == []

    def test_multi_cert_line_fullwidth_slash_isolates_mismatch(
        self, mock_screener: CertScreener
    ) -> None:
        # Exact regression case from review item 4:
        # Only QF045BE-1232 should get DURATION_MISMATCH; QF045BP-0001 must have 0 warnings.
        line = "外壁 60分 QF045BE-1232 ／ 間仕切壁 45分 QF045BP-0001"
        report = mock_screener.screen_text(line)
        assert len(report.occurrences) == 2
        by_cert = {o.cert_no: o for o in report.occurrences}
        assert {w["code"] for w in by_cert["QF045BE-1232"].warnings} == {
            "DURATION_MISMATCH"
        }
        assert by_cert["QF045BP-0001"].warnings == []

        # Also test fullwidth semicolon & comma separators
        line2 = "外壁 60分 QF045BE-1232（ニチハ）； 間仕切壁 45分 QF045BP-0001"
        r2 = mock_screener.screen_text(line2)
        by_cert2 = {o.cert_no: o for o in r2.occurrences}
        assert {w["code"] for w in by_cert2["QF045BE-1232"].warnings} == {
            "DURATION_MISMATCH"
        }
        assert by_cert2["QF045BP-0001"].warnings == []


class TestFileFormatsAndExports:
    def test_csv_json_and_recursive_directory_inventory(
        self, mock_screener: CertScreener, tmp_path: Path
    ) -> None:
        project = tmp_path / "project"
        sub = project / "specs"
        sub.mkdir(parents=True)

        # CSV with contradiction (60分 vs QF045BE-1232)
        (project / "schedule.csv").write_text(
            "部位,要求仕様,メーカー,認定番号\n"
            "外壁（耐力壁）,60分準耐火構造,ニチハ,QF045BE-1232\n"
            "界壁,遮音構造,吉野石膏,SOI-0160\n",
            encoding="utf-8",
        )
        # JSON referencing the same cert_no + a new unlisted one
        (sub / "bom.json").write_text(
            json.dumps(
                {
                    "walls": [
                        {
                            "part": "外壁",
                            "spec": "45分準耐火構造",
                            "cert_no": "QF045BE-1232",
                        },
                        {
                            "part": "外壁",
                            "spec": "60分耐火構造",
                            "cert_no": "FP060BE-9999",
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        # Markdown note
        (sub / "note.md").write_text(
            "# 仕上表メモ\n天井下地：不燃材料 NM-8585 を使用。\n",
            encoding="utf-8",
        )

        report = mock_screener.screen_path(project)
        assert report.scanned_file_count == 3
        assert report.summary["total_occurrences"] == 5
        assert report.summary["unique_cert_count"] == 4

        # QF045BE-1232 appears in 2 files and is deduplicated in inventory
        inv_map = {item.cert_no: item for item in report.inventory}
        assert inv_map["QF045BE-1232"].count == 2
        assert len(inv_map["QF045BE-1232"].files) == 2
        assert inv_map["QF045BE-1232"].warning_count == 1  # from CSV row 2

        # Markdown export contains inventory table, seinokyo link, and disclaimer
        md = report.to_markdown()
        assert "クロスファイル認定番号一覧" in md
        assert SEINOKYO_SEARCH_URL in md
        assert "免責事項" in md
        assert "DURATION_MISMATCH" in md

        # JSON export is valid JSON
        parsed_json = json.loads(report.to_json())
        assert parsed_json["summary"]["unique_cert_count"] == 4
        assert len(parsed_json["inventory"]) == 4

        # CSV export parses cleanly
        csv_rows = list(csv.DictReader(io.StringIO(report.to_csv())))
        assert len(csv_rows) == 5
        assert {r["cert_no"] for r in csv_rows} == {
            "QF045BE-1232",
            "SOI-0160",
            "FP060BE-9999",
            "NM-8585",
        }

    def test_pdf_extraction_when_pymupdf_available(
        self, mock_screener: CertScreener, tmp_path: Path
    ) -> None:
        pymupdf = pytest.importorskip("pymupdf")
        pdf_path = tmp_path / "spec.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Exterior wall cert: FP060BE-0123 and NM-8585")
        doc.save(str(pdf_path))
        doc.close()

        report = mock_screener.screen_file(pdf_path)
        assert report.scanned_file_count == 1
        assert {o.cert_no for o in report.occurrences} == {"FP060BE-0123", "NM-8585"}
        assert all(o.location.startswith("p.1:") for o in report.occurrences)

    def test_dxf_extraction_when_ezdxf_available(
        self, mock_screener: CertScreener, tmp_path: Path
    ) -> None:
        ezdxf = pytest.importorskip("ezdxf")
        dxf_path = tmp_path / "drawing.dxf"
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_text("QF045BE-1232", dxfattribs={"layer": "A-NOTE"})
        msp.add_mtext("Wall spec\\PFP060NE-0001", dxfattribs={"layer": "A-SPEC"})
        doc.saveas(str(dxf_path))

        report = mock_screener.screen_file(dxf_path)
        assert report.scanned_file_count == 1
        assert {o.cert_no for o in report.occurrences} == {"QF045BE-1232", "FP060NE-0001"}

    def test_ifc_skips_gracefully_when_ifcopenshell_missing(
        self, mock_screener: CertScreener, tmp_path: Path
    ) -> None:
        try:
            import ifcopenshell  # noqa: F401

            pytest.skip("ifcopenshell is installed; skip missing-dependency fallback test")
        except ImportError:
            pass
        ifc_path = tmp_path / "model.ifc"
        ifc_path.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8")
        report = mock_screener.screen_file(ifc_path)
        assert report.scanned_file_count == 0
        assert len(report.skipped_files) == 1
        assert "ifcopenshell" in report.skipped_files[0]["reason"]

    def test_cp932_shift_jis_file_supported(
        self, mock_screener: CertScreener, tmp_path: Path
    ) -> None:
        sjis_file = tmp_path / "sjis_spec.txt"
        sjis_file.write_bytes(
            "外壁仕様：60分耐火構造 FP060BE-0123（吉野石膏）\n".encode("cp932")
        )
        report = mock_screener.screen_file(sjis_file)
        assert report.scanned_file_count == 1
        assert len(report.occurrences) == 1
        assert report.occurrences[0].cert_no == "FP060BE-0123"
        assert report.occurrences[0].status == STATUS_FOUND
        assert report.occurrences[0].warnings == []
