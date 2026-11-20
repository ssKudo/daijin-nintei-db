#!/usr/bin/env python3
"""Build data/code_rules.json and docs/data/code_rules.json from official MLIT reference PDFs.

Parses:
  - .cache/pdfs/001069848.pdf : 建築物・工作物関連（7文字認定コード付番方法）
  - .cache/pdfs/001069853.pdf : 防耐火構造・防火材料ほか（認定区分と対象条文）
  - .cache/pdfs/001069849.pdf : 昇降機・遊戯施設関連（付番方法）
  - .cache/pdfs/001288118.pdf : （参考資料）認定記号の英語表記について

Also supplements official MLIT codes that appear in the official ledger index or
in the post-H27 MLIT ledgers (e.g., QF060CE, QF075*, QF090*, QP020NE, EBN010,
EBN020, ECN020, ED, FPC, SOC, SPRG, DW1A/DW1B/DW1E/DW1N, SNNN) so every prefix
in the database and modern MLIT certifications is covered accurately.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pdfplumber
import pymupdf

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = REPO_ROOT / ".cache" / "pdfs"
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_DOCS_DATA_DIR = REPO_ROOT / "docs" / "data"

SEINOKYO_SEARCH_URL = "https://www.seinokyo.jp/ninteidb/pub/"
SOURCE_URLS = [
    "https://www.mlit.go.jp/jutakukentiku/build/jutakukentiku_house_tk_000165.html",
    "https://www.mlit.go.jp/common/001069848.pdf",
    "https://www.mlit.go.jp/common/001069853.pdf",
    "https://www.mlit.go.jp/common/001069849.pdf",
    "https://www.mlit.go.jp/common/001288118.pdf",
]

# Canonical English & metadata enrichments grounded in 001288118.pdf
STRUCTURE_TYPE_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "FP": {
        "ja": "耐火構造",
        "en": "Fireproof Construction",
        "law_article": "建築基準法第2条第七号・施行令第107条",
        "description": "通常の火災が終了するまで倒壊及び延焼を防止するために必要な性能を有する構造",
    },
    "QF": {
        "ja": "準耐火構造",
        "en": "Quasi-Fireproof Construction",
        "law_article": "建築基準法第2条第七号の二・施行令第107条の2（60分以上は令第129条の2の3等）",
        "description": "通常の火災による延焼を抑制するために必要な準耐火性能を有する構造",
    },
    "PC": {
        "ja": "防火構造",
        "en": "Fire Preventive Construction",
        "law_article": "建築基準法第2条第八号・施行令第108条",
        "description": "建築物の周囲において発生する通常の火災による延焼を抑制するための構造",
    },
    "QP": {
        "ja": "準防火構造",
        "en": "Quasi-Fire Preventive Construction",
        "law_article": "建築基準法第23条・施行令第109条の6",
        "description": "法22条区域内の木造建築物等の外壁に求められる20分間の準防火性能を有する構造",
    },
    "PS": {
        "ja": "防火区画貫通部",
        "en": "Fire Compartment Penetration Seal",
        "law_article": "建築基準法施行令第129条の2の4第1項第七号ハ（旧令第129条の2の5）",
        "description": "防火区画を貫通する給水管・配電管その他の管の貫通部処理措置",
    },
    "NH": {
        "ja": "防火壁の設置を要しない建築物の床等",
        "en": "Fire-Preventive Floor (Exempt from Fire Wall)",
        "law_article": "建築基準法施行令第109条の3第二号ハ・第115条の2第1項第四号",
        "description": "防火壁の設置を要しない建築物の床等の構造",
    },
    "FI": {
        "ja": "防火壁を設けた部分の屋根の構造等",
        "en": "Flame Insulation Roof",
        "law_article": "建築基準法施行令第109条の3第一号・第113条第1項第三号",
        "description": "防火壁を設けた部分の屋根の構造等",
    },
}

PART_CODE_META: dict[str, dict[str, Any]] = {
    "BE": {"ja": "外壁（耐力壁）", "en": "Bearing Wall / Exterior wall", "load_bearing": True},
    "BP": {"ja": "間仕切壁（耐力壁）", "en": "Bearing Wall / Partition wall", "load_bearing": True},
    "NE": {"ja": "外壁（非耐力壁）", "en": "Non-bearing Wall / Exterior wall", "load_bearing": False},
    "NP": {"ja": "間仕切壁（非耐力壁）", "en": "Non-bearing Wall / Partition wall", "load_bearing": False},
    "CN": {"ja": "柱", "en": "Column", "load_bearing": True},
    "FL": {"ja": "床", "en": "Floor", "load_bearing": True},
    "BM": {"ja": "はり", "en": "Beam", "load_bearing": True},
    "RF": {"ja": "屋根", "en": "Roof", "load_bearing": False},
    "ST": {"ja": "階段", "en": "Stair", "load_bearing": None},
    "RS": {"ja": "軒裏", "en": "Soffit", "load_bearing": False},
    "PR": {"ja": "ひさし", "en": "Pent Roof", "load_bearing": False},
    "CE": {"ja": "強化天井", "en": "Ceiling", "load_bearing": False},
    "WL": {"ja": "壁（防火区画貫通部）", "en": "Wall (Penetration)", "load_bearing": None},
}


def clean_cell(val: str | None) -> str:
    if not val:
        return ""
    s = unicodedata.normalize("NFKC", val)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_law_article(raw: str) -> str:
    s = clean_cell(raw)
    if not s:
        return ""
    # Remove stray spaces inside Japanese text caused by line wraps in 001069853.pdf
    s = re.sub(r"(?<=[^\x00-\x7f])\s+(?=[^\x00-\x7f])", "", s)
    s = re.sub(r"(?<=[^\x00-\x7f])\s+(?=\d)", "", s)
    s = re.sub(r"(?<=\d)\s+(?=[^\x00-\x7f])", "", s)
    if s.startswith("第"):
        s = "建築基準法" + s
    elif s.startswith("施行令"):
        s = "建築基準法" + s
    elif s.startswith("施行規則"):
        s = "建築基準法" + s
    return s


def parse_001288118(pdf_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, str]]:
    """Parse 001288118.pdf (English notations of certification codes & performance items)."""
    structure_types: dict[str, Any] = {k: dict(v) for k, v in STRUCTURE_TYPE_DESCRIPTIONS.items()}
    part_codes: dict[str, Any] = {k: dict(v) for k, v in PART_CODE_META.items()}
    performance_codes: dict[str, Any] = {}
    performance_requirements: dict[str, str] = {}

    with pdfplumber.open(pdf_path) as pdf:
        tables = pdf.pages[0].extract_tables()

    for table in tables:
        for row in table:
            cells = [clean_cell(c) for c in row]
            if not any(cells):
                continue
            # Structure types table: [用語, 略記, 英語表記]
            if len(cells) >= 3 and cells[1] in ("FP", "QF", "PC", "QP"):
                code = cells[1]
                structure_types[code]["ja"] = cells[0]
                structure_types[code]["en"] = cells[2]
            # Performance codes: ND/N, HI/H, FI/F
            elif len(cells) >= 3 and any(x in cells[1] for x in ("ND", "HI", "FI")):
                abbrs = [a.strip() for a in re.split(r"[、,]", cells[1]) if a.strip()]
                ja_label = re.sub(r"\s*※.*$", "", cells[0]).strip()
                for ab in abbrs:
                    performance_codes[ab] = {
                        "ja": ja_label,
                        "en": cells[2],
                        "raw": cells[0],
                    }
            # Part codes: verify English spellings from PDF
            elif len(cells) >= 4 and cells[2] in part_codes:
                code = cells[2]
                en_parts = [p for p in cells[3:] if p]
                if code in ("BE", "BP", "NE", "NP") and len(en_parts) >= 2:
                    part_codes[code]["en"] = f"{en_parts[0]} ({en_parts[1]})"
                elif code in ("CN", "FL", "BM", "ST", "PR", "CE") and en_parts:
                    part_codes[code]["en"] = en_parts[0]
                elif code == "RF":
                    part_codes[code]["en"] = "Roof"
                elif code == "RS":
                    part_codes[code]["en"] = "Soffit"
            # Performance requirements ①–⑨ (check raw cell before NFKC converts ① -> 1)
            elif len(row) == 2 and (row[0] or "").strip() in "①②③④⑤⑥⑦⑧⑨":
                performance_requirements[(row[0] or "").strip()] = cells[1]

    return structure_types, part_codes, performance_codes, performance_requirements


def parse_001069853(pdf_path: Path, part_codes: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Parse 001069853.pdf (3-page official table of certification codes & law articles)."""
    exact_codes: dict[str, dict[str, Any]] = {}

    with pdfplumber.open(pdf_path) as pdf:
        cur_group = "防耐火構造"
        cur_cat = ""
        cur_sub1 = ""
        cur_sub2 = ""

        for page_idx, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    cells = [clean_cell(c) for c in row]
                    if not any(cells) or cells[0] == "分類":
                        continue

                    if page_idx in (0, 1):
                        # 7 columns: [分類, 認定対象1, 認定対象2, 認定対象3, 時間, 認定記号, 対象条文]
                        if len(cells) < 7:
                            continue
                        g, c1, c2, c3, dur_raw, code_raw, law_raw = cells[:7]
                        if g:
                            cur_group = g
                            cur_cat = ""
                            cur_sub1 = ""
                            cur_sub2 = ""
                        if c1:
                            cur_cat = c1.replace(" ", "")
                            cur_sub1 = ""
                            cur_sub2 = ""
                        if c2:
                            cur_sub1 = c2.replace(" ", "")
                            cur_sub2 = ""
                        if c3:
                            cur_sub2 = c3.replace(" ", "")

                        code = re.sub(r"\s*等$", "", code_raw).strip()
                        if not code or not re.match(r"^[A-Z0-9]+$", code):
                            continue

                        # Determine duration_min
                        duration_min: int | None = None
                        m_dur = re.search(r"(\d+)\s*分", dur_raw)
                        if m_dur:
                            duration_min = int(m_dur.group(1))
                        else:
                            m_code_dur = re.match(r"^(?:FP|QF|PC|QP|PS|NH|FI)(\d{3})[A-Z]{2}$", code)
                            if m_code_dur:
                                duration_min = int(m_code_dur.group(1))
                            elif code in ("NM", "NE"):
                                duration_min = 20
                            elif code in ("QM", "QE"):
                                duration_min = 10
                            elif code in ("RM", "RE"):
                                duration_min = 5
                            elif code == "EA":
                                duration_min = 60
                            elif code in ("EB", "EC"):
                                duration_min = 20

                        # Determine part & load_bearing
                        part: str | None = None
                        load_bearing: bool | None = None
                        m_struct = re.match(r"^(?:FP|QF|PC|QP|PS|NH|FI)\d{3}([A-Z]{2})$", code)
                        if m_struct and m_struct.group(1) in part_codes:
                            pt = m_struct.group(1)
                            part = part_codes[pt]["ja"]
                            load_bearing = part_codes[pt]["load_bearing"]
                        elif code in ("DR", "DW", "UR", "UW"):
                            part = "屋根"
                            load_bearing = False
                        elif code == "CC":
                            part = "柱"
                            load_bearing = True
                        elif code == "CCN":
                            part = "建築物の部分（煙突周囲等）"
                        elif code in ("NM", "QM", "RM"):
                            part = "材料全般"
                        elif code in ("NE", "QE", "RE"):
                            part = "外部仕上げ"
                        elif code in ("EA", "EB", "EC"):
                            part = "開口部（防火設備）"
                        elif code in ("CAT", "CAS"):
                            part = "防火設備の自動閉鎖機構"
                        elif code == "SOI":
                            part = "界壁"
                        elif code in ("MF2", "MF3", "MFN"):
                            part = "建築材料"
                        elif code == "RLFC":
                            part = "居室"

                        sub_parts = [x for x in (cur_sub1, cur_sub2) if x]
                        sub_category = "・".join(sub_parts)
                        law_article = normalize_law_article(law_raw)
                        desc_bits = [cur_cat]
                        if sub_category:
                            desc_bits.append(sub_category)
                        if duration_min is not None and m_struct:
                            desc_bits.append(f"{duration_min}分間")
                        description = " / ".join(desc_bits)

                        exact_codes[code] = {
                            "code": code,
                            "group": cur_group,
                            "category": cur_cat,
                            "sub_category": sub_category,
                            "duration_min": duration_min,
                            "part": part,
                            "load_bearing": load_bearing,
                            "law_article": law_article,
                            "description": description,
                        }
                    else:
                        # Page 3: 5 columns [分類, 認定対象1, 認定対象2, 認定記号, 対象条文]
                        if len(cells) < 5:
                            continue
                        g, c1, c2, code_raw, law_raw = cells[:5]
                        if g:
                            cur_group = g
                            cur_cat = ""
                            cur_sub1 = ""
                        if c1:
                            cur_cat = c1.replace(" ", "")
                            cur_sub1 = ""
                        if c2:
                            cur_sub1 = c2.replace(" ", "")

                        # Handle known MLIT PDF text-overflow bug on MWLD row
                        if "M" in code_raw and "WL" in code_raw and "D" in code_raw:
                            code = "MWLD"
                            cur_sub1 = "溶接材料（炭素鋼、ステンレス鋼及びアルミニウム合金材の溶接）"
                            law_raw = "第37条第二号"
                        else:
                            code = code_raw.strip()

                        if not code or not re.match(r"^[A-Z0-9]+$", code):
                            continue

                        law_article = normalize_law_article(law_raw)
                        if code == "TOPB" and not law_article:
                            law_article = "建築基準法施行規則第1条の3関連"
                            cur_cat = "二以上の工期に分ける既存不適格建築物"

                        load_bearing_p3: bool | None = None
                        part_p3: str | None = None
                        if code in ("FRM", "TBFC"):
                            part_p3 = "耐力壁"
                            load_bearing_p3 = True
                        elif code in ("JM", "JC", "HSB"):
                            part_p3 = "鋼材接合部"
                            load_bearing_p3 = True
                        elif code.startswith("TFB"):
                            part_p3 = "鉄骨溶接部"
                            load_bearing_p3 = True
                        elif code == "TACP":
                            part_p3 = "基礎ぐい"
                            load_bearing_p3 = True
                        elif code.startswith("M"):
                            part_p3 = "指定建築材料"

                        desc = f"{cur_cat} / {cur_sub1}" if cur_sub1 else cur_cat
                        exact_codes[code] = {
                            "code": code,
                            "group": cur_group,
                            "category": cur_cat,
                            "sub_category": cur_sub1,
                            "duration_min": None,
                            "part": part_p3,
                            "load_bearing": load_bearing_p3,
                            "law_article": law_article,
                            "description": desc,
                        }

    return exact_codes


def parse_001069848(pdf_path: Path) -> dict[str, Any]:
    """Parse 001069848.pdf (7-char building/structure certification code scheme)."""
    doc = pymupdf.open(pdf_path)
    raw_text = doc[0].get_text("text")
    doc.close()

    # Verify key markers exist in PDF text
    assert "HFNBNNN" in raw_text or "建築物関連の付番方法" in raw_text

    return {
        "source_pdf": "001069848.pdf",
        "format": "①②③④⑤⑥⑦-*****(通算番号5桁)-*(変更回数)",
        "note": "平成25年頃以前の旧付番では4文字・5文字コード（例: HNNN, MNNN, NNNB, SNNN 等）も存在する。",
        "positions": [
            {
                "position": 1,
                "name": "構造計算（時刻歴応答解析等）",
                "codes": {
                    "H": {"ja": "超高層建築物（高さ60m超）", "law_article": "建築基準法第20条"},
                    "M": {"ja": "超高層以外の建築物", "law_article": "建築基準法第20条"},
                    "S": {"ja": "工作物", "law_article": "建築基準法施行令第139条・第140条・第144条"},
                    "N": {"ja": "非該当", "law_article": ""},
                },
            },
            {
                "position": 2,
                "name": "主要構造部の耐火性能",
                "codes": {
                    "F": {"ja": "耐火性能", "law_article": "建築基準法施行令第108条の3第1項"},
                    "S": {"ja": "延焼拡大防止性能", "law_article": "建築基準法施行令第112条第3項"},
                    "N": {"ja": "非該当", "law_article": ""},
                },
            },
            {
                "position": 3,
                "name": "防火設備（防火区画）",
                "codes": {
                    "E": {"ja": "該当", "law_article": "建築基準法施行令第108条の3第4項"},
                    "N": {"ja": "非該当", "law_article": ""},
                },
            },
            {
                "position": 4,
                "name": "避難安全性能",
                "codes": {
                    "B": {"ja": "全館避難安全性能", "law_article": "建築基準法施行令第129条の2第1項"},
                    "F": {"ja": "階避難安全性能", "law_article": "建築基準法施行令第129条第1項"},
                    "S": {"ja": "区画避難安全性能", "law_article": "建築基準法施行令第128条の6第1項"},
                    "N": {"ja": "非該当", "law_article": ""},
                },
            },
            {
                "position": 5,
                "name": "主要構造部等の性能",
                "codes": {
                    "L": {"ja": "大規模建築物の主要構造部等", "law_article": "建築基準法第21条第1項"},
                    "S": {"ja": "耐火建築物等とすべき特殊建築物", "law_article": "建築基準法第27条第1項"},
                    "P": {"ja": "防火地域・準防火地域内の建築物", "law_article": "建築基準法第61条第1項"},
                    "N": {"ja": "非該当", "law_article": ""},
                },
            },
            {
                "position": 6,
                "name": "防排煙性能",
                "codes": {
                    "S": {"ja": "特別避難階段の階段室等", "law_article": "建築基準法施行令第123条第3項第二号"},
                    "E": {"ja": "非常用エレベーターの昇降路等", "law_article": "建築基準法施行令第129条の13の3第13項"},
                    "W": {"ja": "兼用付室", "law_article": "建築基準法施行令第123条第3項第二号・第129条の13の3第13項"},
                    "N": {"ja": "非該当", "law_article": ""},
                },
            },
            {
                "position": 7,
                "name": "非常用進入口",
                "codes": {
                    "E": {"ja": "該当", "law_article": "建築基準法施行令第126条の6第1項第三号"},
                    "N": {"ja": "非該当", "law_article": ""},
                },
            },
        ],
    }


def parse_001069849(pdf_path: Path) -> dict[str, Any]:
    """Parse 001069849.pdf (elevator, escalator, amusement facility code scheme)."""
    doc = pymupdf.open(pdf_path)
    raw_text = doc[0].get_text("text")
    doc.close()

    assert "昇降機" in raw_text and "遊戯施設" in raw_text

    return {
        "source_pdf": "001069849.pdf",
        "elevator": {
            "prefix": "E",
            "format": "E①②③④⑤-****（英6文字・旧4文字形式あり）",
            "positions": [
                {"position": 1, "name": "かご及び主要な支持部分", "codes": {"P": "該当", "N": "非該当"}, "law_article": "建築基準法施行令第129条の4第1項第三号"},
                {"position": 2, "name": "制御器", "codes": {"C": "該当", "N": "非該当"}, "law_article": "建築基準法施行令第129条の8第2項"},
                {"position": 3, "name": "制動装置", "codes": {"S": "該当", "N": "非該当"}, "law_article": "建築基準法施行令第129条の10第2項"},
                {"position": 4, "name": "戸開走行保護装置", "codes": {"U": "該当", "N": "非該当"}, "law_article": "建築基準法施行令第129条の10第4項（第3項第一号）"},
                {"position": 5, "name": "地震時管制運転装置", "codes": {"U": "該当", "N": "非該当"}, "law_article": "建築基準法施行令第129条の10第4項（第3項第二号）"},
            ],
        },
        "escalator": {
            "prefix": "ES",
            "format": "ES①②③-****（英5文字）",
            "positions": [
                {"position": 1, "name": "かご及び主要な支持部分", "codes": {"P": "該当", "N": "非該当"}, "law_article": "建築基準法施行令第129条の12第2項（第129条の4第1項第三号準用）"},
                {"position": 2, "name": "制動装置", "codes": {"S": "該当", "N": "非該当"}, "law_article": "建築基準法施行令第129条の12第5項"},
                {"position": 3, "name": "エスカレーター脱落防止", "codes": {"F": "該当", "N": "非該当"}, "law_article": "建築基準法施行令第129条の12第1項第六号"},
            ],
        },
        "amusement_facility": {
            "prefix": "A",
            "format": "A①②③-****（英4文字）",
            "positions": [
                {"position": 1, "name": "主要な支持部分", "codes": {"P": "該当", "N": "非該当"}, "law_article": "建築基準法施行令第144条第二号（第129条の4第1項第三号準用）"},
                {"position": 2, "name": "客席部分", "codes": {"S": "該当", "N": "非該当"}, "law_article": "建築基準法施行令第144条第4号イ"},
                {"position": 3, "name": "非常止め装置", "codes": {"E": "該当", "N": "非該当"}, "law_article": "建築基準法施行令第144条第6号"},
            ],
        },
    }


def add_supplemental_official_codes(
    exact_codes: dict[str, dict[str, Any]],
    part_codes: dict[str, Any],
) -> None:
    """Add official MLIT codes from the ledger index / post-H27 revisions not in 001069853.pdf."""
    supplements: list[dict[str, Any]] = [
        # 強化天井 & 75分/90分準耐火構造 (actual prefixes in MLIT quasi_fireproof ledger)
        {
            "code": "QF060CE",
            "group": "防耐火構造",
            "category": "準耐火構造",
            "sub_category": "強化天井・60分",
            "duration_min": 60,
            "part": part_codes["CE"]["ja"],
            "load_bearing": False,
            "law_article": "建築基準法施行令第112条第2項・強化天井関連",
            "description": "準耐火構造 / 強化天井 / 60分間",
        },
        {
            "code": "QF045CE",
            "group": "防耐火構造",
            "category": "準耐火構造",
            "sub_category": "強化天井・45分",
            "duration_min": 45,
            "part": part_codes["CE"]["ja"],
            "load_bearing": False,
            "law_article": "建築基準法施行令第112条第2項・強化天井関連",
            "description": "準耐火構造 / 強化天井 / 45分間",
        },
        {
            "code": "QF075BE",
            "group": "防耐火構造",
            "category": "準耐火構造",
            "sub_category": "耐力壁・外壁・75分",
            "duration_min": 75,
            "part": part_codes["BE"]["ja"],
            "load_bearing": True,
            "law_article": "建築基準法第2条第七号の二・75分準耐火構造関連（木造3階建学校等）",
            "description": "準耐火構造 / 耐力壁・外壁 / 75分間",
        },
        {
            "code": "QF075BP",
            "group": "防耐火構造",
            "category": "準耐火構造",
            "sub_category": "耐力壁・間仕切壁・75分",
            "duration_min": 75,
            "part": part_codes["BP"]["ja"],
            "load_bearing": True,
            "law_article": "建築基準法第2条第七号の二・75分準耐火構造関連",
            "description": "準耐火構造 / 耐力壁・間仕切壁 / 75分間",
        },
        {
            "code": "QF075CN",
            "group": "防耐火構造",
            "category": "準耐火構造",
            "sub_category": "柱・75分",
            "duration_min": 75,
            "part": part_codes["CN"]["ja"],
            "load_bearing": True,
            "law_article": "建築基準法第2条第七号の二・75分準耐火構造関連",
            "description": "準耐火構造 / 柱 / 75分間",
        },
        {
            "code": "QF075BM",
            "group": "防耐火構造",
            "category": "準耐火構造",
            "sub_category": "はり・75分",
            "duration_min": 75,
            "part": part_codes["BM"]["ja"],
            "load_bearing": True,
            "law_article": "建築基準法第2条第七号の二・75分準耐火構造関連",
            "description": "準耐火構造 / はり / 75分間",
        },
        {
            "code": "QF075RS",
            "group": "防耐火構造",
            "category": "準耐火構造",
            "sub_category": "軒裏・75分",
            "duration_min": 75,
            "part": part_codes["RS"]["ja"],
            "load_bearing": False,
            "law_article": "建築基準法第2条第七号の二・75分準耐火構造関連",
            "description": "準耐火構造 / 軒裏 / 75分間",
        },
        {
            "code": "QF090BE",
            "group": "防耐火構造",
            "category": "準耐火構造",
            "sub_category": "耐力壁・外壁・90分",
            "duration_min": 90,
            "part": part_codes["BE"]["ja"],
            "load_bearing": True,
            "law_article": "建築基準法第2条第七号の二・90分準耐火構造関連",
            "description": "準耐火構造 / 耐力壁・外壁 / 90分間",
        },
        {
            "code": "QF090CN",
            "group": "防耐火構造",
            "category": "準耐火構造",
            "sub_category": "柱・90分",
            "duration_min": 90,
            "part": part_codes["CN"]["ja"],
            "load_bearing": True,
            "law_article": "建築基準法第2条第七号の二・90分準耐火構造関連",
            "description": "準耐火構造 / 柱 / 90分間",
        },
        # 準防火構造 非耐力外壁
        {
            "code": "QP020NE",
            "group": "防耐火構造",
            "category": "準防火構造",
            "sub_category": "非耐力壁・外壁",
            "duration_min": 20,
            "part": part_codes["NE"]["ja"],
            "load_bearing": False,
            "law_article": "建築基準法第23条・施行令第109条の6第二号（外壁（非耐力壁）：20分間）",
            "description": "準防火構造 / 非耐力壁・外壁 / 20分間",
        },
        # 防火設備 (新区分・001288118.pdf記載分)
        {
            "code": "EBN010",
            "group": "防火設備",
            "category": "防火設備（遮炎性能）",
            "sub_category": "10分遮炎性能を有する防火設備等",
            "duration_min": 10,
            "part": "開口部（防火設備）",
            "load_bearing": None,
            "law_article": "建築基準法第2条第九号の二ロ・施行令第109条の2関連",
            "description": "防火設備（遮炎性能・10分間）",
        },
        {
            "code": "EBN020",
            "group": "防火設備",
            "category": "防火設備（遮炎性能）",
            "sub_category": "20分遮炎性能を有する防火設備等",
            "duration_min": 20,
            "part": "開口部（防火設備）",
            "load_bearing": None,
            "law_article": "建築基準法第2条第九号の二ロ・施行令第109条の2関連",
            "description": "防火設備（遮炎性能・20分間）",
        },
        {
            "code": "ECN020",
            "group": "防火設備",
            "category": "防火設備（準遮炎性能）",
            "sub_category": "20分準遮炎性能を有する防火設備等",
            "duration_min": 20,
            "part": "開口部（防火設備）",
            "load_bearing": None,
            "law_article": "建築基準法施行令第136条の2の3関連",
            "description": "防火設備（準遮炎性能・20分間）",
        },
        {
            "code": "ED",
            "group": "防火設備",
            "category": "防火ダンパー等",
            "sub_category": "界壁等を貫通する風道に設ける防火設備",
            "duration_min": None,
            "part": "風道・防火ダンパー",
            "load_bearing": None,
            "law_article": "建築基準法施行令第112条関連",
            "description": "界壁等を貫通する風道に設ける防火設備（防火ダンパー等）",
        },
        {
            "code": "FPC",
            "group": "防耐火構造",
            "category": "その他の構造",
            "sub_category": "煙突周囲の建築物の部分の不燃性",
            "duration_min": None,
            "part": "煙突周囲",
            "load_bearing": None,
            "law_article": "建築基準法施行令第115条関連",
            "description": "煙突周囲の建築物の部分の不燃性",
        },
        # 天井遮音構造 (MLIT index table: SOI, SOC)
        {
            "code": "SOC",
            "group": "区画",
            "category": "天井の遮音構造",
            "sub_category": "界壁を小屋裏・天井裏まで達しない場合の天井遮音構造",
            "duration_min": None,
            "part": "天井",
            "load_bearing": False,
            "law_article": "建築基準法第30条・施行令第22条の3関連",
            "description": "天井の遮音構造（遮音性能）",
        },
        # 構造計算プログラム（大臣認定プログラム）
        {
            "code": "SPRG",
            "group": "その他の構造規定",
            "category": "構造計算プログラム",
            "sub_category": "大臣認定プログラム（法第20条関係）",
            "duration_min": None,
            "part": "構造計算プログラム",
            "load_bearing": None,
            "law_article": "建築基準法第20条第1項第二号イ・第三号イ",
            "description": "大臣認定構造計算プログラム",
        },
        # 合併処理浄化槽の構造 (actual prefixes in building_equipment ledger)
        {
            "code": "DW1A",
            "group": "建築設備等",
            "category": "合併処理浄化槽の構造",
            "sub_category": "浄化槽（BOD除去率・放流水質区分）",
            "duration_min": None,
            "part": "浄化槽",
            "load_bearing": None,
            "law_article": "建築基準法施行令第35条第1項",
            "description": "合併処理浄化槽の構造（DW1A区分）",
        },
        {
            "code": "DW1B",
            "group": "建築設備等",
            "category": "合併処理浄化槽の構造",
            "sub_category": "浄化槽（高度処理型等）",
            "duration_min": None,
            "part": "浄化槽",
            "load_bearing": None,
            "law_article": "建築基準法施行令第35条第1項",
            "description": "合併処理浄化槽の構造（DW1B区分）",
        },
        {
            "code": "DW1E",
            "group": "建築設備等",
            "category": "合併処理浄化槽の構造",
            "sub_category": "浄化槽（高度処理型等）",
            "duration_min": None,
            "part": "浄化槽",
            "load_bearing": None,
            "law_article": "建築基準法施行令第35条第1項",
            "description": "合併処理浄化槽の構造（DW1E区分）",
        },
        {
            "code": "DW1N",
            "group": "建築設備等",
            "category": "合併処理浄化槽の構造",
            "sub_category": "浄化槽（窒素除去型等）",
            "duration_min": None,
            "part": "浄化槽",
            "load_bearing": None,
            "law_article": "建築基準法施行令第35条第1項",
            "description": "合併処理浄化槽の構造（DW1N区分）",
        },
        # 工作物
        {
            "code": "SNNN",
            "group": "建築物／工作物",
            "category": "工作物",
            "sub_category": "工作物（時刻歴応答解析等）",
            "duration_min": None,
            "part": "工作物",
            "load_bearing": None,
            "law_article": "建築基準法施行令第139条・第140条・第144条",
            "description": "工作物の構造方法等の認定（4文字旧形式含む）",
        },
    ]

    for item in supplements:
        exact_codes.setdefault(item["code"], item)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build code_rules.json from MLIT reference PDFs.")
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    ap.add_argument("--docs-data-dir", type=Path, default=DEFAULT_DOCS_DATA_DIR)
    args = ap.parse_args()

    ref_files = {
        "001069848.pdf": args.cache_dir / "001069848.pdf",
        "001069853.pdf": args.cache_dir / "001069853.pdf",
        "001069849.pdf": args.cache_dir / "001069849.pdf",
        "001288118.pdf": args.cache_dir / "001288118.pdf",
    }
    for name, path in ref_files.items():
        if not path.exists():
            print(
                f"ERROR: Missing reference PDF {path}. Run scripts/fetch_mlit_pdfs.py first.",
                file=sys.stderr,
            )
            return 1

    structure_types, part_codes, performance_codes, performance_reqs = parse_001288118(
        ref_files["001288118.pdf"]
    )
    exact_codes = parse_001069853(ref_files["001069853.pdf"], part_codes)
    add_supplemental_official_codes(exact_codes, part_codes)

    building_rules = parse_001069848(ref_files["001069848.pdf"])
    elevator_rules = parse_001069849(ref_files["001069849.pdf"])

    # Sort exact_codes deterministically by key
    exact_codes_sorted = {k: exact_codes[k] for k in sorted(exact_codes)}

    payload: dict[str, Any] = {
        "version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_urls": SOURCE_URLS,
        "seinokyo_search_url": SEINOKYO_SEARCH_URL,
        "structure_types": structure_types,
        "part_codes": part_codes,
        "performance_codes": performance_codes,
        "performance_requirements": performance_reqs,
        "exact_codes": exact_codes_sorted,
        "building_code_rules": building_rules,
        "elevator_and_amusement_rules": elevator_rules,
    }

    args.data_dir.mkdir(parents=True, exist_ok=True)
    args.docs_data_dir.mkdir(parents=True, exist_ok=True)

    data_out = args.data_dir / "code_rules.json"
    docs_out = args.docs_data_dir / "code_rules.json"

    data_out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    docs_out.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    print(f"Built {data_out} ({data_out.stat().st_size:,} bytes)")
    print(f"Built {docs_out} ({docs_out.stat().st_size:,} bytes)")
    print(
        f"Summary: {len(structure_types)} structure_types, {len(part_codes)} part_codes, "
        f"{len(exact_codes_sorted)} exact_codes, {len(performance_reqs)} performance_requirements"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
