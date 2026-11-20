"""Unit tests for daijin_nintei.decoder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daijin_nintei.decoder import (
    SEINOKYO_SEARCH_URL,
    CertDecoder,
    decode_cert_no,
    normalize_cert_no,
)


class TestNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("FP060BE-0123", "FP060BE-0123"),
            ("ＦＰ０６０ＢＥ－０１２３", "FP060BE-0123"),
            ("ｆｐ０６０ｂｅー０１２３", "FP060BE-0123"),
            ("QF045BE−1232", "QF045BE-1232"),
            ("QF045BE―1232", "QF045BE-1232"),
            ("QF045BE‐1232", "QF045BE-1232"),
            ("  fp060be - 0123  ", "FP060BE-0123"),
            ("ＱＦ０４５ＢＥ－１２３２（１）", "QF045BE-1232(1)"),
            ("\ufeffNM-8585", "NM-8585"),
        ],
    )
    def test_normalize_variants(self, raw: str, expected: str) -> None:
        assert normalize_cert_no(raw) == expected


class TestStructuredFireCodes:
    @pytest.mark.parametrize(
        ("cert_no", "st_ja", "dur", "part_sub", "load_bearing"),
        [
            ("FP060BE-0123", "耐火構造", 60, "外壁（耐力壁）", True),
            ("FP120CN-0042", "耐火構造", 120, "柱", None),
            ("FP180BM-0009", "耐火構造", 180, "はり", None),
            ("FP060FL-0010", "耐火構造", 60, "床", None),
            ("FP060RF-0021", "耐火構造", 60, "屋根", None),
            ("FP060ST-0005", "耐火構造", 60, "階段", None),
            ("FP060NP-0333", "耐火構造", 60, "間仕切壁（非耐力壁）", False),
            ("QF045BE-1232", "準耐火構造", 45, "外壁（耐力壁）", True),
            ("QF045BP-0088", "準耐火構造", 45, "間仕切壁（耐力壁）", True),
            ("QF060NE-0555", "準耐火構造", 60, "外壁（非耐力壁）", False),
            ("QF045CE-0012", "準耐火構造", 45, "天井", False),
            ("PC030BE-0100", "防火構造", 30, "外壁（耐力壁）", True),
            ("PC030RS-0200", "防火構造", 30, "軒裏", False),
            ("PC030PR-0015", "防火構造", 30, "ひさし", False),
            ("QP020BE-0001", "準防火", 20, "外壁（耐力壁）", True),
        ],
    )
    def test_fire_codes_builtin(
        self,
        cert_no: str,
        st_ja: str,
        dur: int,
        part_sub: str,
        load_bearing: bool | None,
    ) -> None:
        decoder = CertDecoder(use_builtin_only=True)
        res = decoder.decode(cert_no)
        assert res.is_valid_format is True
        assert res.cert_no == cert_no
        assert st_ja in (res.structure_type or "")
        assert res.duration_min == dur
        assert part_sub in (res.part or "")
        assert res.load_bearing is load_bearing
        assert res.law_article is not None
        assert res.warnings == []

    def test_brand_new_2026_unlisted_code_decodes_cleanly(self) -> None:
        decoder = CertDecoder(use_builtin_only=True)
        res = decoder.decode("ＦＰ０６０ＢＥ－９９９９（２）")
        assert res.is_valid_format is True
        assert res.cert_no == "FP060BE-9999(2)"
        assert res.prefix == "FP060BE"
        assert res.serial == "9999(2)"
        assert res.duration_min == 60
        assert res.load_bearing is True
        assert res.seinokyo_search_url == SEINOKYO_SEARCH_URL


class TestExactAndPenetrationCodes:
    @pytest.mark.parametrize(
        ("cert_no", "expected_prefix", "expected_cat_sub", "expected_dur"),
        [
            ("NM-8585", "NM", "防火材料", 20),
            ("NE-0123", "NE", "防火材料", 20),
            ("QM-0200", "QM", "防火材料", 10),
            ("QE-0050", "QE", "防火材料", 10),
            ("RM-0010", "RM", "防火材料", 5),
            ("RE-0011", "RE", "防火材料", 5),
            ("DR-0001", "DR", "屋根", None),
            ("DW-0002", "DW", "屋根", None),
            ("UR-0003", "UR", "屋根", None),
            ("UW-0004", "UW", "屋根", None),
            ("EA-0123", "EA", "防火設備", 60),
            ("EB-0001", "EB", "防火設備", 20),
            ("EC-0002", "EC", "防火設備", 20),
            ("ED-0001", "ED", "小規模な竪穴区画", None),
            ("EH090-0001", "EH090", "防火設備", 90),
            ("EH060-0003", "EH060", "防火設備", 60),
            ("EQ060-0001", "EQ060", "防火設備", 60),
            ("EBN-0012", "EBN", "防火設備", 20),
            ("EBN020-0012", "EBN020", "防火設備", 20),
            ("ECN-0034", "ECN", "防火設備", 20),
            ("ECN020-0034", "ECN020", "防火設備", 20),
            ("CAT-0001", "CAT", "防火設備", None),
            ("CAS-0002", "CAS", "防火設備", None),
            ("SOI-0160", "SOI", "遮音構造", None),
            ("SOC-0020", "SOC", "遮音構造", None),
            ("PS060WL-0123", "PS060WL", "貫通部", 60),
            ("PS060FL-0456", "PS060FL", "貫通部", 60),
            ("PS120WL-9999", "PS120WL", "貫通部", 120),
            ("FRM-0001", "FRM", "耐力壁", None),
            ("TBFC-0001", "TBFC", "耐力壁", None),
            ("QFRM-0001", "QFRM", "耐力壁", None),
            ("MCMU-0001", "MCMU", "指定建築材料", None),
            ("MCLT-0001", "MCLT", "指定建築材料", None),
            ("MBLK-0001", "MBLK", "指定建築材料", None),
            ("FPLH060BE-0001", "FPLH060BE", "耐火構造", 60),
            ("FPLH120FL-0002", "FPLH120FL", "耐火構造", 120),
            ("QPEP045BE-0001", "QPEP045BE", "準防火", 45),
            ("MF2-0001", "MF2", "ホルムアルデヒド", None),
            ("MF3-0002", "MF3", "ホルムアルデヒド", None),
            ("MFN-1234", "MFN", "ホルムアルデヒド", None),
            ("JM-0001", "JM", "鉄骨", None),
            ("JC-0002", "JC", "鉄骨", None),
            ("HSB-0003", "HSB", "高力ボルト", None),
            ("LAE-69", "LAE", "照明", None),
        ],
    )
    def test_exact_and_penetration_codes(
        self,
        cert_no: str,
        expected_prefix: str,
        expected_cat_sub: str,
        expected_dur: int | None,
    ) -> None:
        decoder = CertDecoder(use_builtin_only=True)
        res = decoder.decode(cert_no)
        assert res.is_valid_format is True
        assert res.prefix == expected_prefix
        assert expected_cat_sub in (res.category or "")
        assert res.duration_min == expected_dur

    def test_ed_is_small_vertical_shaft_opening_not_fire_damper(self) -> None:
        decoder = CertDecoder(use_builtin_only=True)
        res = decoder.decode("ED-0001")
        assert res.is_valid_format is True
        assert "小規模な竪穴区画" in (res.category or "")
        assert "112条第12項" in (res.law_article or "")
        assert "ダンパー" not in (res.description or "")
        assert "ダンパー" not in (res.category or "")

    def test_eh090_decodes_heat_insulating_fire_equipment(self) -> None:
        decoder = CertDecoder(use_builtin_only=True)
        res = decoder.decode("EH090-0001")
        assert res.is_valid_format is True
        assert res.prefix == "EH090"
        assert res.duration_min == 90
        assert "防火設備" in (res.category or "")

    def test_ne_standalone_is_exterior_noncombustible_not_wall(self) -> None:
        decoder = CertDecoder(use_builtin_only=True)
        res = decoder.decode("NE-0123")
        assert res.is_valid_format is True
        assert res.category == "防火材料"
        assert "不燃材料" in (res.structure_type or "")
        assert res.load_bearing is None

    @pytest.mark.parametrize(
        ("cert_no", "expected_cat_sub"),
        [
            ("HFEB-0001", "建築物"),
            ("MNNNNNN-12013-1", "建築物"),
            ("SNNNNNN-12379", "工作物"),
            ("ENNNUN-2796", "エレベーター"),
            ("ESNNF-0001", "エスカレーター"),
            ("ANSE-0076", "遊戯施設"),
            ("APCS-0013", "遊戯施設"),
        ],
    )
    def test_positional_bitmask_codes_builtin(
        self, cert_no: str, expected_cat_sub: str
    ) -> None:
        decoder = CertDecoder(use_builtin_only=True)
        res = decoder.decode(cert_no)
        assert res.is_valid_format is True
        assert expected_cat_sub in (res.category or "")
        assert res.law_article is not None


class TestInvalidFormats:
    @pytest.mark.parametrize(
        "bad_input",
        [
            "",
            "   ",
            "FP060BE",
            "FP060BE0123",
            "FP060XX-0123",
            "FP60BE-0123",
            "FP000BE-0123",
            "FP060BE-12",
            "FP060BE-ABCD",
            "XYZ-9999",
            "INVALID",
        ],
    )
    def test_invalid_formats_flagged(self, bad_input: str) -> None:
        decoder = CertDecoder(use_builtin_only=True)
        res = decoder.decode(bad_input)
        assert res.is_valid_format is False
        assert len(res.warnings) >= 1

    def test_omitted_hyphen_still_extracts_helpful_metadata(self) -> None:
        decoder = CertDecoder(use_builtin_only=True)
        res = decoder.decode("FP060BE0123")
        assert res.is_valid_format is False
        assert res.prefix == "FP060BE"
        assert res.serial == "0123"
        assert res.duration_min == 60
        assert any("ハイフン" in w for w in res.warnings)


class TestExternalRulesLoading:
    def test_loads_list_style_exact_codes(self, mock_data_dir: Path) -> None:
        dec = CertDecoder(rules_path=mock_data_dir / "code_rules.json")
        res = dec.decode("CUSTOMEX-0042")
        assert res.is_valid_format is True
        assert res.category == "テスト用特殊認定"
        assert res.duration_min == 90
        # Built-in rules remain intact
        assert dec.decode("NM-8585").is_valid_format is True

    def test_loads_dict_style_exact_codes(self, tmp_path: Path) -> None:
        rules_file = tmp_path / "code_rules.json"
        rules_file.write_text(
            json.dumps(
                {
                    "exact_codes": {
                        "DICTCODE": {
                            "category": "辞書形式テスト",
                            "duration_min": 30,
                            "law_article": "法テスト条",
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        res = decode_cert_no("DICTCODE-0100", rules_path=rules_file)
        assert res.is_valid_format is True
        assert res.category == "辞書形式テスト"
        assert res.duration_min == 30
