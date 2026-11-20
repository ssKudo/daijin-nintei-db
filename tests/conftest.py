"""Shared pytest fixtures for daijin-nintei-db tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from daijin_nintei.db import CertDatabase
from daijin_nintei.decoder import SEINOKYO_SEARCH_URL, CertDecoder
from daijin_nintei.screener import CertScreener


@pytest.fixture()
def mock_data_dir(tmp_path: Path) -> Path:
    """Create a realistic mock ``data/`` directory matching the shared schema contract."""
    data_dir = tmp_path / "data"
    cat_dir = data_dir / "categories"
    cat_dir.mkdir(parents=True)

    fireproof_records: list[dict[str, Any]] = [
        {
            "cert_no": "FP060BE-0123",
            "prefix": "FP060BE",
            "category_id": "fireproof",
            "category_name": "耐火構造",
            "date_jp": "令和2年06月15日",
            "date": "2020-06-15",
            "title": "強化せっこうボード張り木造軸組耐火外壁",
            "applicant": "吉野石膏株式会社",
            "address": "東京都千代田区丸の内3-3-1",
            "evaluator": "一般財団法人日本建築総合試験所",
            "period": "H25_after",
        },
        {
            "cert_no": "FP060NE-0001",
            "prefix": "FP060NE",
            "category_id": "fireproof",
            "category_name": "耐火構造",
            "date_jp": "平成26年04月01日",
            "date": "2014-04-01",
            "title": "押出成形セメント板外壁（非耐力壁）",
            "applicant": "旭化成建材株式会社",
            "address": "東京都千代田区有楽町1-1-2",
            "evaluator": "一般財団法人建材試験センター",
            "period": "H25_after",
        },
        {
            "cert_no": "QF045BE-1232",
            "prefix": "QF045BE",
            "category_id": "fireproof",
            "category_name": "準耐火構造",
            "date_jp": "平成29年10月10日",
            "date": "2017-10-10",
            "title": "窯業系サイディング張り準耐火構造外壁",
            "applicant": "ニチハ株式会社",
            "address": "愛知県名古屋市中区錦2-18-19",
            "evaluator": "一般財団法人ベターリビング",
            "period": "H25_after",
        },
        {
            "cert_no": "QF045NE-0001",
            "prefix": "QF045NE",
            "category_id": "fireproof",
            "category_name": "準耐火構造",
            "date_jp": "平成22年03月20日",
            "date": "2010-03-20",
            "title": "金属サイディング張り非耐力外壁",
            "applicant": "ケイミュー株式会社",
            "address": "大阪府大阪市中央区城見1-2-27",
            "evaluator": "一般財団法人建材試験センター",
            "period": "H25_before",
        },
    ]

    material_records: list[dict[str, Any]] = [
        {
            "cert_no": "NM-8585",
            "prefix": "NM",
            "category_id": "fire_material",
            "category_name": "防火材料（不燃・準不燃・難燃）",
            "date_jp": "平成12年11月01日",
            "date": "2000-11-01",
            "title": "せっこうボード（不燃材料）",
            "applicant": "一般社団法人石膏ボード工業会",
            "address": "東京都港区西新橋2-13-12",
            "evaluator": "一般財団法人建材試験センター",
            "period": "H25_before",
        },
        {
            "cert_no": "SOI-0160",
            "prefix": "SOI",
            "category_id": "sound_insulation",
            "category_name": "遮音構造",
            "date_jp": "令和3年08月12日",
            "date": "2021-08-12",
            "title": "強化せっこうボード二重張り遮音界壁",
            "applicant": "吉野石膏株式会社",
            "address": "東京都千代田区丸の内3-3-1",
            "evaluator": "一般財団法人日本建築総合試験所",
            "period": "H25_after",
        },
    ]

    (cat_dir / "fireproof.json").write_text(
        json.dumps(fireproof_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (cat_dir / "fire_material.json").write_text(
        json.dumps([material_records[0]], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (cat_dir / "sound_insulation.json").write_text(
        json.dumps([material_records[1]], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    manifest = {
        "schema_version": "1.0.0",
        "source_name": "建築基準法に基づく構造方法等の認定に係る帳簿等（テスト用モック）",
        "source_url": "https://www.mlit.go.jp/jutakukentiku/build/jutakukentiku_house_tk_000165.html",
        "source_license": "公共データ利用規約（第1.0版）（PDL1.0）",
        "data_period": "令和5年4月30日現在",
        "generated_at": "2026-11-20T00:00:00Z",
        "total_records": 6,
        "categories": [
            {
                "id": "fireproof",
                "name": "防耐火構造",
                "group": "防火関係",
                "prefixes": ["FP060BE", "FP060NE", "QF045BE", "QF045NE"],
                "record_count": 4,
                "json_file": "categories/fireproof.json",
                "csv_file": "categories/fireproof.csv",
                "source_pdfs": ["mock_fireproof.pdf"],
            },
            {
                "id": "fire_material",
                "name": "防火材料",
                "group": "防火関係",
                "prefixes": ["NM", "NE", "QM", "QE", "RM", "RE"],
                "record_count": 1,
                "json_file": "categories/fire_material.json",
                "csv_file": "categories/fire_material.csv",
                "source_pdfs": ["mock_material.pdf"],
            },
            {
                "id": "sound_insulation",
                "name": "遮音構造",
                "group": "一般構造・設備",
                "prefixes": ["SOI", "SOC"],
                "record_count": 1,
                "json_file": "categories/sound_insulation.json",
                "csv_file": "categories/sound_insulation.csv",
                "source_pdfs": ["mock_sound.pdf"],
            },
        ],
    }
    (data_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    code_rules = {
        "version": "1.0.0",
        "source_urls": [],
        "seinokyo_search_url": SEINOKYO_SEARCH_URL,
        "structure_types": {
            "FP": {"ja": "耐火構造", "en": "Fireproof Construction"},
            "QF": {"ja": "準耐火構造", "en": "Quasi-Fireproof Construction"},
        },
        "part_codes": {
            "BE": {"ja": "外壁（耐力壁）", "en": "Exterior Load-Bearing Wall", "load_bearing": True},
            "NE": {"ja": "外壁（非耐力壁）", "en": "Exterior Non-Load-Bearing Wall", "load_bearing": False},
        },
        "exact_codes": [
            {
                "code": "CUSTOMEX",
                "group": "テスト関係",
                "category": "テスト用特殊認定",
                "sub_category": "外部ルール読込確認",
                "duration_min": 90,
                "part": "外壁",
                "load_bearing": None,
                "law_article": "テスト条文第1条",
                "description": "code_rules.json から動的に読み込まれた記号",
            }
        ],
    }
    (data_dir / "code_rules.json").write_text(
        json.dumps(code_rules, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return data_dir


@pytest.fixture()
def mock_db(mock_data_dir: Path) -> CertDatabase:
    return CertDatabase(data_dir=mock_data_dir)


@pytest.fixture()
def mock_decoder(mock_data_dir: Path) -> CertDecoder:
    return CertDecoder(rules_path=mock_data_dir / "code_rules.json")


@pytest.fixture()
def mock_screener(mock_db: CertDatabase, mock_decoder: CertDecoder) -> CertScreener:
    return CertScreener(db=mock_db, decoder=mock_decoder)
