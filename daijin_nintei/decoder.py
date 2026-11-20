"""Deterministic decoder for Japanese MLIT Minister Certification numbers (大臣認定番号).

Works purely on official MLIT symbol rules (from ``data/code_rules.json`` when
available, with a complete built-in fallback table), so it can decode brand-new
certifications issued after the R5.4.30 MLIT public ledger cutoff without any
network access or external dependencies.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SEINOKYO_SEARCH_URL = "https://www.seinokyo.jp/ninteidb/pub/"

# Default repository data path relative to this module
_DEFAULT_CODE_RULES_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "code_rules.json"
)

# Dash / hyphen / minus / prolonged-sound variants normalized to ASCII hyphen '-'
_HYPHEN_CHARS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uff0d\u30fc\uff70"
_HYPHEN_TRANSLATION = str.maketrans({ch: "-" for ch in _HYPHEN_CHARS})

# Fullwidth ASCII letters, digits, parentheses, and space
_FULLWIDTH_MAP: dict[int, str] = {}
for _code in range(0xFF01, 0xFF5F):
    _FULLWIDTH_MAP[_code] = chr(_code - 0xFEE0)
_FULLWIDTH_MAP[0x3000] = " "
_FULLWIDTH_TRANSLATION = str.maketrans(_FULLWIDTH_MAP)

# Built-in fallback rule tables based on official MLIT symbol specifications
BUILTIN_STRUCTURE_TYPES: dict[str, dict[str, str]] = {
    "FP": {
        "ja": "耐火構造",
        "en": "Fireproof Construction",
        "law_article": "法第2条第7号・令第107条",
        "category": "防耐火構造",
        "description": "通常の火災が終了するまで倒壊・延焼を防止する耐火性能を有する構造",
    },
    "QF": {
        "ja": "準耐火構造",
        "en": "Quasi-Fireproof Construction",
        "law_article": "法第2条第7号の2・令第107条の2",
        "category": "防耐火構造",
        "description": "通常の火災による延焼を抑制するための準耐火性能を有する構造",
    },
    "PC": {
        "ja": "防火構造",
        "en": "Fire-Preventive Construction",
        "law_article": "法第2条第8号・令第108条",
        "category": "防耐火構造",
        "description": "建築物の周囲における通常の火災による延焼を抑制するための防火性能を有する構造",
    },
    "QP": {
        "ja": "準防火構造",
        "en": "Quasi-Fire-Preventive Construction",
        "law_article": "法第23条・令第109条の6",
        "category": "防耐火構造",
        "description": "法22条区域内の木造建築物等の外壁に求められる20分間の準防火性能を有する構造",
    },
    "NH": {
        "ja": "防火壁の設置を要しない建築物の床等",
        "en": "Fire-Preventive Floor (Exempt from Fire Wall)",
        "law_article": "令第109条の3第2号ハ・令第115条の2第1項第4号",
        "category": "防耐火構造",
        "description": "防火壁の設置を要しない建築物の床等の構造",
    },
    "FI": {
        "ja": "防火壁を設けた部分の屋根の構造等",
        "en": "Flame Insulation Roof",
        "law_article": "令第109条の3第1号・令第113条第1項第3号",
        "category": "防耐火構造",
        "description": "防火壁を設けた部分の屋根の構造等",
    },
}

BUILTIN_PART_CODES: dict[str, dict[str, Any]] = {
    "BE": {
        "ja": "外壁（耐力壁）",
        "en": "Exterior Load-Bearing Wall",
        "load_bearing": True,
        "part_group": "外壁",
    },
    "BP": {
        "ja": "間仕切壁（耐力壁）",
        "en": "Partition Load-Bearing Wall",
        "load_bearing": True,
        "part_group": "間仕切壁",
    },
    "NE": {
        "ja": "外壁（非耐力壁）",
        "en": "Exterior Non-Load-Bearing Wall",
        "load_bearing": False,
        "part_group": "外壁",
    },
    "NP": {
        "ja": "間仕切壁（非耐力壁）",
        "en": "Partition Non-Load-Bearing Wall",
        "load_bearing": False,
        "part_group": "間仕切壁",
    },
    "CN": {
        "ja": "柱",
        "en": "Column",
        "load_bearing": None,
        "part_group": "柱",
    },
    "FL": {
        "ja": "床",
        "en": "Floor",
        "load_bearing": None,
        "part_group": "床",
    },
    "BM": {
        "ja": "はり（梁）",
        "en": "Beam",
        "load_bearing": None,
        "part_group": "はり",
    },
    "RF": {
        "ja": "屋根",
        "en": "Roof",
        "load_bearing": None,
        "part_group": "屋根",
    },
    "ST": {
        "ja": "階段",
        "en": "Stairs",
        "load_bearing": None,
        "part_group": "階段",
    },
    "RS": {
        "ja": "軒裏",
        "en": "Eaves Soffit",
        "load_bearing": False,
        "part_group": "軒裏",
    },
    "PR": {
        "ja": "ひさし（小庇）",
        "en": "Pent Roof / Eaves Projection",
        "load_bearing": False,
        "part_group": "ひさし",
    },
    "CE": {
        "ja": "天井",
        "en": "Ceiling",
        "load_bearing": False,
        "part_group": "天井",
    },
    "WL": {
        "ja": "壁",
        "en": "Wall",
        "load_bearing": None,
        "part_group": "壁",
    },
}

# Built-in fallback for building / structure positional bitmask codes (4, 5, or 7 chars)
BUILTIN_BUILDING_POSITIONS: list[dict[str, Any]] = [
    {
        "position": 1,
        "name": "構造計算（時刻歴応答解析等）",
        "codes": {
            "H": {"ja": "超高層建築物（高さ60m超）", "law_article": "建築基準法第20条"},
            "M": {"ja": "超高層以外の建築物", "law_article": "建築基準法第20条"},
            "S": {
                "ja": "工作物",
                "law_article": "建築基準法施行令第139条・第140条・第144条",
            },
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
            "E": {"ja": "防火設備該当", "law_article": "建築基準法施行令第108条の3第4項"},
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
            "E": {
                "ja": "非常用エレベーターの昇降路等",
                "law_article": "建築基準法施行令第129条の13の3第13項",
            },
            "W": {
                "ja": "兼用付室",
                "law_article": "建築基準法施行令第123条第3項第二号・第129条の13の3第13項",
            },
            "N": {"ja": "非該当", "law_article": ""},
        },
    },
    {
        "position": 7,
        "name": "非常用進入口",
        "codes": {
            "E": {"ja": "非常用進入口該当", "law_article": "建築基準法施行令第126条の6第1項第三号"},
            "N": {"ja": "非該当", "law_article": ""},
        },
    },
]

# Built-in fallback for elevator / escalator / amusement positional bitmask rules
BUILTIN_ELEVATOR_AMUSEMENT_RULES: dict[str, Any] = {
    "elevator": {
        "prefix": "E",
        "positions": [
            {
                "position": 1,
                "name": "かご及び主要な支持部分",
                "codes": {"P": "該当", "N": "非該当"},
                "law_article": "建築基準法施行令第129条の4第1項第三号",
            },
            {
                "position": 2,
                "name": "制御器",
                "codes": {"C": "該当", "N": "非該当"},
                "law_article": "建築基準法施行令第129条の8第2項",
            },
            {
                "position": 3,
                "name": "制動装置",
                "codes": {"S": "該当", "N": "非該当"},
                "law_article": "建築基準法施行令第129条の10第2項",
            },
            {
                "position": 4,
                "name": "戸開走行保護装置",
                "codes": {"U": "該当", "N": "非該当"},
                "law_article": "建築基準法施行令第129条の10第4項（第3項第一号）",
            },
            {
                "position": 5,
                "name": "地震時管制運転装置",
                "codes": {"U": "該当", "N": "非該当"},
                "law_article": "建築基準法施行令第129条の10第4項（第3項第二号）",
            },
        ],
    },
    "escalator": {
        "prefix": "ES",
        "positions": [
            {
                "position": 1,
                "name": "かご及び主要な支持部分",
                "codes": {"P": "該当", "N": "非該当"},
                "law_article": "建築基準法施行令第129条の12第2項",
            },
            {
                "position": 2,
                "name": "制動装置",
                "codes": {"S": "該当", "N": "非該当"},
                "law_article": "建築基準法施行令第129条の12第5項",
            },
            {
                "position": 3,
                "name": "エスカレーター脱落防止",
                "codes": {"F": "該当", "N": "非該当"},
                "law_article": "建築基準法施行令第129条の12第1項第六号",
            },
        ],
    },
    "amusement_facility": {
        "prefix": "A",
        "positions": [
            {
                "position": 1,
                "name": "主要な支持部分",
                "codes": {"P": "該当", "N": "非該当"},
                "law_article": "建築基準法施行令第144条第二号",
            },
            {
                "position": 2,
                "name": "客席部分／制御器",
                "codes": {"S": "客席部分該当", "C": "制御器該当（旧区分）", "N": "非該当"},
                "law_article": "建築基準法施行令第144条第4号イ",
            },
            {
                "position": 3,
                "name": "非常止め装置／制動装置",
                "codes": {"E": "非常止め装置該当", "S": "制動装置該当（旧区分）", "N": "非該当"},
                "law_article": "建築基準法施行令第144条第6号",
            },
        ],
    },
}

# Exact prefix codes covering all major MLIT certification categories
BUILTIN_EXACT_CODES: dict[str, dict[str, Any]] = {
    # 防火材料 (Fire-retardant / Noncombustible materials)
    "NM": {
        "code": "NM",
        "group": "防火関係",
        "category": "防火材料",
        "sub_category": "不燃材料",
        "structure_type": "不燃材料",
        "duration_min": 20,
        "part": None,
        "load_bearing": None,
        "law_article": "法第2条第9号・令第108条の2",
        "description": "不燃材料（20分間燃焼しない等の不燃性能）",
    },
    "NE": {
        "code": "NE",
        "group": "防火関係",
        "category": "防火材料",
        "sub_category": "不燃材料（外部仕上げ用）",
        "structure_type": "不燃材料（外部仕上げ用）",
        "duration_min": 20,
        "part": "外部仕上げ",
        "load_bearing": None,
        "law_article": "法第2条第9号・令第108条の2",
        "description": "外部の仕上げに用いる不燃材料",
    },
    "QM": {
        "code": "QM",
        "group": "防火関係",
        "category": "防火材料",
        "sub_category": "準不燃材料",
        "structure_type": "準不燃材料",
        "duration_min": 10,
        "part": None,
        "load_bearing": None,
        "law_article": "令第1条第5号",
        "description": "準不燃材料（10分間準不燃性能）",
    },
    "QE": {
        "code": "QE",
        "group": "防火関係",
        "category": "防火材料",
        "sub_category": "準不燃材料（外部仕上げ用）",
        "structure_type": "準不燃材料（外部仕上げ用）",
        "duration_min": 10,
        "part": "外部仕上げ",
        "load_bearing": None,
        "law_article": "令第1条第5号",
        "description": "外部の仕上げに用いる準不燃材料",
    },
    "RM": {
        "code": "RM",
        "group": "防火関係",
        "category": "防火材料",
        "sub_category": "難燃材料",
        "structure_type": "難燃材料",
        "duration_min": 5,
        "part": None,
        "load_bearing": None,
        "law_article": "令第1条第6号",
        "description": "難燃材料（5分間難燃性能）",
    },
    "RE": {
        "code": "RE",
        "group": "防火関係",
        "category": "防火材料",
        "sub_category": "難燃材料（外部仕上げ用）",
        "structure_type": "難燃材料（外部仕上げ用）",
        "duration_min": 5,
        "part": "外部仕上げ",
        "load_bearing": None,
        "law_article": "令第1条第6号",
        "description": "外部の仕上げに用いる難燃材料",
    },
    # 屋根の不燃性能 (Roof fire-spread prevention / 飛び火構造)
    "DR": {
        "code": "DR",
        "group": "防火関係",
        "category": "屋根の不燃性能（飛び火構造）",
        "sub_category": "防火・準防火地域内の建築物の屋根",
        "structure_type": "防火・準防火地域内屋根",
        "duration_min": None,
        "part": "屋根",
        "load_bearing": None,
        "law_article": "法第63条・令第136条の2の2",
        "description": "防火地域・準防火地域内の建築物の屋根（飛び火性能）",
    },
    "DW": {
        "code": "DW",
        "group": "防火関係",
        "category": "屋根の不燃性能（飛び火構造）",
        "sub_category": "防火・準防火地域内の不燃性物品保管倉庫等の屋根",
        "structure_type": "防火・準防火地域内倉庫等屋根",
        "duration_min": None,
        "part": "屋根",
        "load_bearing": None,
        "law_article": "法第63条・令第136条の2の2",
        "description": "防火地域・準防火地域内の不燃性物品保管倉庫等の屋根",
    },
    "UR": {
        "code": "UR",
        "group": "防火関係",
        "category": "屋根の不燃性能（飛び火構造）",
        "sub_category": "法22条区域内の建築物の屋根",
        "structure_type": "法22条区域内屋根",
        "duration_min": None,
        "part": "屋根",
        "load_bearing": None,
        "law_article": "法第22条第1項・令第109条の8",
        "description": "法第22条第1項区域内の建築物の屋根（飛び火性能）",
    },
    "UW": {
        "code": "UW",
        "group": "防火関係",
        "category": "屋根の不燃性能（飛び火構造）",
        "sub_category": "法22条区域内の不燃性物品保管倉庫等の屋根",
        "structure_type": "法22条区域内倉庫等屋根",
        "duration_min": None,
        "part": "屋根",
        "load_bearing": None,
        "law_article": "法第22条第1項・令第109条の8",
        "description": "法第22条第1項区域内の不燃性物品保管倉庫等の屋根",
    },
    # 防火設備 (Fire-preventive equipment / doors / shutters)
    "EA": {
        "code": "EA",
        "group": "防火関係",
        "category": "防火設備",
        "sub_category": "特定防火設備（60分間遮炎性能）",
        "structure_type": "特定防火設備",
        "duration_min": 60,
        "part": "開口部・防火設備",
        "load_bearing": None,
        "law_article": "令第112条第1項（特定防火設備）",
        "description": "60分間遮炎性能を有する特定防火設備",
    },
    "EB": {
        "code": "EB",
        "group": "防火関係",
        "category": "防火設備",
        "sub_category": "防火設備（20分間遮炎性能）",
        "structure_type": "防火設備（遮炎性能）",
        "duration_min": 20,
        "part": "開口部・防火設備",
        "load_bearing": None,
        "law_article": "法第2条第9号の2ロ・令第109条の2",
        "description": "20分間遮炎性能を有する防火設備（両面／片面）",
    },
    "EC": {
        "code": "EC",
        "group": "防火関係",
        "category": "防火設備",
        "sub_category": "防火設備（20分間準遮炎性能）",
        "structure_type": "防火設備（準遮炎性能）",
        "duration_min": 20,
        "part": "開口部・防火設備",
        "load_bearing": None,
        "law_article": "法第61条・令第136条の2",
        "description": "防火地域・準防火地域等の外壁開口部に設ける防火設備（20分間準遮炎性能）",
    },
    "EBN": {
        "code": "EBN",
        "group": "防火関係",
        "category": "防火設備",
        "sub_category": "防火設備（遮炎性能・外壁開口部等）",
        "structure_type": "防火設備（遮炎性能）",
        "duration_min": 20,
        "part": "開口部・防火設備",
        "load_bearing": None,
        "law_article": "法第2条第9号の2ロ・令第109条の2",
        "description": "遮炎性能を有する防火設備（屋外面・屋内面の別区分を含む）",
    },
    "ECN": {
        "code": "ECN",
        "group": "防火関係",
        "category": "防火設備",
        "sub_category": "防火設備（準遮炎性能・外壁開口部等）",
        "structure_type": "防火設備（準遮炎性能）",
        "duration_min": 20,
        "part": "開口部・防火設備",
        "load_bearing": None,
        "law_article": "法第61条・令第136条の2",
        "description": "準遮炎性能を有する防火設備（外壁開口部等）",
    },
    "CAT": {
        "code": "CAT",
        "group": "防火関係",
        "category": "防火設備（閉鎖機構）",
        "sub_category": "熱感知器連動閉鎖機構",
        "structure_type": "防火設備閉鎖機構（熱感知）",
        "duration_min": None,
        "part": "防火設備",
        "load_bearing": None,
        "law_article": "令第112条第19項等",
        "description": "温度ヒューズ・熱感知器等と連動して閉鎖する防火設備の構造",
    },
    "CAS": {
        "code": "CAS",
        "group": "防火関係",
        "category": "防火設備（閉鎖機構）",
        "sub_category": "煙感知器連動閉鎖機構",
        "structure_type": "防火設備閉鎖機構（煙感知）",
        "duration_min": None,
        "part": "防火設備",
        "load_bearing": None,
        "law_article": "令第112条第19項等",
        "description": "煙感知器と連動して閉鎖する防火設備の構造（竪穴区画・異種用途区画等）",
    },
    # 遮音構造 (Sound insulation)
    "SOI": {
        "code": "SOI",
        "group": "一般構造・設備",
        "category": "遮音構造",
        "sub_category": "界壁の遮音構造",
        "structure_type": "界壁の遮音構造",
        "duration_min": None,
        "part": "界壁",
        "load_bearing": None,
        "law_article": "法第30条・令第22条の3",
        "description": "長屋・共同住宅の界壁の遮音構造",
    },
    "SOC": {
        "code": "SOC",
        "group": "一般構造・設備",
        "category": "遮音構造",
        "sub_category": "天井の遮音構造",
        "structure_type": "天井の遮音構造",
        "duration_min": None,
        "part": "天井",
        "load_bearing": None,
        "law_article": "令第22条の3・令第114条等",
        "description": "共同住宅等の天井の遮音構造",
    },
    # 木造耐力壁・壁倍率 (Timber shear walls)
    "FRM": {
        "code": "FRM",
        "group": "構造関係",
        "category": "木造軸組工法耐力壁（壁倍率）",
        "sub_category": "木造軸組耐力壁",
        "structure_type": "木造軸組工法耐力壁",
        "duration_min": None,
        "part": "壁（耐力壁）",
        "load_bearing": True,
        "law_article": "令第46条第4項表1（八）",
        "description": "木造軸組工法における壁倍率の大臣認定（耐力壁）",
    },
    "TBFC": {
        "code": "TBFC",
        "group": "構造関係",
        "category": "枠組壁工法耐力壁",
        "sub_category": "枠組壁工法（2×4）耐力壁",
        "structure_type": "枠組壁工法耐力壁",
        "duration_min": None,
        "part": "壁（耐力壁）",
        "load_bearing": True,
        "law_article": "平成13年国交省告示第1540号・令第80条の2",
        "description": "枠組壁工法（ツーバイフォー）における耐力壁等の大臣認定",
    },
    # ホルムアルデヒド発散建築材料 (Formaldehyde-emitting materials)
    "MF2": {
        "code": "MF2",
        "group": "一般構造・設備",
        "category": "ホルムアルデヒド発散建築材料",
        "sub_category": "第二種ホルムアルデヒド発散建築材料（F☆☆相当）",
        "structure_type": "第二種ホルムアルデヒド発散建築材料",
        "duration_min": None,
        "part": None,
        "load_bearing": None,
        "law_article": "法第28条の2・令第20条の7",
        "description": "第二種ホルムアルデヒド発散建築材料（F☆☆相当）",
    },
    "MF3": {
        "code": "MF3",
        "group": "一般構造・設備",
        "category": "ホルムアルデヒド発散建築材料",
        "sub_category": "第三種ホルムアルデヒド発散建築材料（F☆☆☆相当）",
        "structure_type": "第三種ホルムアルデヒド発散建築材料",
        "duration_min": None,
        "part": None,
        "load_bearing": None,
        "law_article": "法第28条の2・令第20条の7",
        "description": "第三種ホルムアルデヒド発散建築材料（F☆☆☆相当）",
    },
    "MFN": {
        "code": "MFN",
        "group": "一般構造・設備",
        "category": "ホルムアルデヒド発散建築材料",
        "sub_category": "規制対象外ホルムアルデヒド発散建築材料（F☆☆☆☆相当）",
        "structure_type": "規制対象外建築材料（F☆☆☆☆）",
        "duration_min": None,
        "part": None,
        "load_bearing": None,
        "law_article": "法第28条の2・令第20条の7第4項",
        "description": "ホルムアルデヒド発散建築材料のうち規制対象外（F☆☆☆☆相当）",
    },
    # 鋼材接合・高力ボルト等 (Steel joints & high-strength bolts)
    "JM": {
        "code": "JM",
        "group": "構造関係",
        "category": "鉄骨造接合部（鋼材の接合）",
        "sub_category": "鋼材の接合方法",
        "structure_type": "鋼材の接合",
        "duration_min": None,
        "part": "接合部",
        "load_bearing": None,
        "law_article": "令第67条第1項",
        "description": "鉄骨造における鋼材の接合方法に関する大臣認定",
    },
    "JC": {
        "code": "JC",
        "group": "構造関係",
        "category": "鉄骨造接合部（継手・仕口）",
        "sub_category": "継手又は仕口の構造",
        "structure_type": "継手・仕口の構造",
        "duration_min": None,
        "part": "接合部",
        "load_bearing": None,
        "law_article": "令第67条第2項",
        "description": "鉄骨造における継手又は仕口の構造方法に関する大臣認定",
    },
    "HSB": {
        "code": "HSB",
        "group": "構造関係",
        "category": "指定建築材料（高力ボルト等）",
        "sub_category": "高力ボルト",
        "structure_type": "高力ボルト（指定建築材料）",
        "duration_min": None,
        "part": "接合部",
        "load_bearing": None,
        "law_article": "法第37条第2号・令第68条",
        "description": "指定建築材料（高力ボルト等）に関する大臣認定",
    },
    # 非常用照明装置 (LAE permits 1-2 digit serials in MLIT ledger)
    "LAE": {
        "code": "LAE",
        "group": "建築設備等",
        "category": "非常用の照明装置",
        "sub_category": "非常用照明装置",
        "structure_type": "非常用の照明装置",
        "duration_min": None,
        "part": "照明設備",
        "load_bearing": None,
        "law_article": "令第126条の5第二号",
        "description": "非常用の照明装置の構造方法",
    },
    # 防火区画貫通部 (Fire-compartment penetration - also matched dynamically by PS###WL/FL)
    "PS060WL": {
        "code": "PS060WL",
        "group": "防火関係",
        "category": "防火区画貫通部",
        "sub_category": "壁貫通部（60分間）",
        "structure_type": "防火区画貫通部",
        "duration_min": 60,
        "part": "壁（貫通部）",
        "load_bearing": None,
        "law_article": "令第129条の2の4第1項第7号ハ（旧令第129条の2の5）",
        "description": "耐火構造等の壁を貫通する給水管・配電管等の60分間遮炎措置",
    },
    "PS060FL": {
        "code": "PS060FL",
        "group": "防火関係",
        "category": "防火区画貫通部",
        "sub_category": "床貫通部（60分間）",
        "structure_type": "防火区画貫通部",
        "duration_min": 60,
        "part": "床（貫通部）",
        "load_bearing": None,
        "law_article": "令第129条の2の4第1項第7号ハ（旧令第129条の2の5）",
        "description": "耐火構造等の床を貫通する給水管・配電管等の60分間遮炎措置",
    },
}

# Standard architectural fire durations (minutes)
STANDARD_FIRE_DURATIONS: frozenset[int] = frozenset(
    {20, 30, 45, 60, 75, 90, 120, 150, 180, 240}
)

# Prefixes that legitimately use 1-2 digit serials in MLIT's official ledger
_SHORT_SERIAL_PREFIXES: frozenset[str] = frozenset(
    {"LAE", "DW1A", "DW1B", "DW1E", "DW1N", "DW3N"}
)

# Serial validation patterns:
# - Standard: 3-6 digits, optional up to 2 hyphenated sub-numbers (e.g. 0002-08-01), optional (1)
_SERIAL_STANDARD_RE = re.compile(
    r"^\d{3,6}(?:-\d{1,3}){0,2}(?:\([0-9A-Z]{1,4}\))?$"
)
# - Short-serial prefixes like LAE-69
_SERIAL_SHORT_OK_RE = re.compile(
    r"^\d{1,6}(?:-\d{1,3}){0,2}(?:\([0-9A-Z]{1,4}\))?$"
)
# - Structured fire codes (FP/QF/PC/QP): strictly 3-5 digits
_SERIAL_FIRE_RE = re.compile(r"^\d{3,5}(?:-\d{1,3})?(?:\([0-9A-Z]{1,4}\))?$")

# Penetration code pattern (e.g., PS060WL, PS120FL, PS045WL)
_PENETRATION_RE = re.compile(r"^PS(\d{3})(WL|FL)$")


def normalize_cert_no(raw: str) -> str:
    """Normalize a certification number to canonical halfwidth uppercase ASCII.

    Handles fullwidth alphanumeric characters, fullwidth parentheses, fullwidth
    spaces, and Japanese/Unicode hyphen/dash/prolonged-sound variants.
    """
    if not isinstance(raw, str):
        return ""
    text = raw.strip().lstrip("\ufeff")
    text = text.translate(_FULLWIDTH_TRANSLATION)
    text = text.translate(_HYPHEN_TRANSLATION)
    # Remove spaces around hyphens and parentheses, collapse remaining whitespace
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"\s*\(\s*", "(", text)
    text = re.sub(r"\s*\)\s*", ")", text)
    text = re.sub(r"\s+", "", text)
    return text.upper()


def format_load_bearing_label(
    load_bearing: bool | None,
    *,
    part_code: str | None = None,
    part_group: str | None = None,
    empty: str = "-",
) -> str:
    """Format ``load_bearing`` appropriately for wall vs. non-wall building parts."""
    if load_bearing is None:
        return empty
    is_wall = (
        part_code in {"BE", "BP", "NE", "NP"}
        or part_group in {"外壁", "間仕切壁", "界壁", "壁"}
    )
    if is_wall:
        return "耐力壁" if load_bearing else "非耐力壁"
    return "耐力あり（主要構造部等）" if load_bearing else "非耐力"


@dataclass
class DecodeResult:
    """Structured result of decoding a Japanese Minister Certification number."""

    cert_no: str
    prefix: str
    serial: str | None
    structure_type: str | None
    duration_min: int | None
    part: str | None
    load_bearing: bool | None
    category: str | None
    law_article: str | None
    is_valid_format: bool
    warnings: list[str] = field(default_factory=list)
    # Supplemental structured fields
    raw_input: str = ""
    structure_type_code: str | None = None
    part_code: str | None = None
    part_group: str | None = None
    group: str | None = None
    sub_category: str | None = None
    description: str | None = None
    seinokyo_search_url: str = SEINOKYO_SEARCH_URL

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CertDecoder:
    """Deterministic MLIT Minister Certification number decoder."""

    def __init__(
        self,
        rules_path: Path | str | None = None,
        *,
        use_builtin_only: bool = False,
    ) -> None:
        self.seinokyo_search_url: str = SEINOKYO_SEARCH_URL
        self.structure_types: dict[str, dict[str, Any]] = {
            k: dict(v) for k, v in BUILTIN_STRUCTURE_TYPES.items()
        }
        self.part_codes: dict[str, dict[str, Any]] = {
            k: dict(v) for k, v in BUILTIN_PART_CODES.items()
        }
        self.exact_codes: dict[str, dict[str, Any]] = {
            k: dict(v) for k, v in BUILTIN_EXACT_CODES.items()
        }
        self.building_positions: list[dict[str, Any]] = list(BUILTIN_BUILDING_POSITIONS)
        self.elevator_amusement_rules: dict[str, Any] = dict(
            BUILTIN_ELEVATOR_AMUSEMENT_RULES
        )
        self.rules_source: str = "builtin"

        if not use_builtin_only:
            candidate_path = (
                Path(rules_path) if rules_path is not None else _DEFAULT_CODE_RULES_PATH
            )
            if candidate_path.is_file():
                self._load_rules_file(candidate_path)

        # Build dynamic structured fire-code pattern (excluding PS which uses _PENETRATION_RE)
        fire_st_keys = [k for k in self.structure_types if k != "PS"]
        st_pattern = "|".join(
            sorted((re.escape(k) for k in fire_st_keys), key=len, reverse=True)
        )
        pt_pattern = "|".join(
            sorted((re.escape(k) for k in self.part_codes), key=len, reverse=True)
        )
        self._structured_fire_re = re.compile(
            rf"^({st_pattern})(\d{{3}})({pt_pattern})$"
        )

    def _load_rules_file(self, path: Path) -> None:
        """Load and merge rules from ``data/code_rules.json``."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        if not isinstance(raw, dict):
            return

        if isinstance(raw.get("seinokyo_search_url"), str) and raw["seinokyo_search_url"]:
            self.seinokyo_search_url = raw["seinokyo_search_url"]

        if isinstance(raw.get("structure_types"), dict):
            for code, info in raw["structure_types"].items():
                if isinstance(info, dict):
                    key = normalize_cert_no(code)
                    merged = dict(self.structure_types.get(key, {}))
                    merged.update(info)
                    self.structure_types[key] = merged

        if isinstance(raw.get("part_codes"), dict):
            for code, info in raw["part_codes"].items():
                if isinstance(info, dict):
                    key = normalize_cert_no(code)
                    merged = dict(self.part_codes.get(key, {}))
                    merged.update(info)
                    self.part_codes[key] = merged

        exact = raw.get("exact_codes")
        if isinstance(exact, dict):
            for code, info in exact.items():
                if isinstance(info, dict):
                    key = normalize_cert_no(code)
                    merged = dict(self.exact_codes.get(key, {}))
                    merged.update(info)
                    merged.setdefault("code", key)
                    self.exact_codes[key] = merged
        elif isinstance(exact, list):
            for info in exact:
                if isinstance(info, dict) and info.get("code"):
                    key = normalize_cert_no(str(info["code"]))
                    merged = dict(self.exact_codes.get(key, {}))
                    merged.update(info)
                    merged["code"] = key
                    self.exact_codes[key] = merged

        bcr = raw.get("building_code_rules")
        if isinstance(bcr, dict) and isinstance(bcr.get("positions"), list):
            self.building_positions = bcr["positions"]

        ear = raw.get("elevator_and_amusement_rules")
        if isinstance(ear, dict):
            # Merge while preserving legacy amusement fallback codes (C/S)
            merged_ear = dict(self.elevator_amusement_rules)
            for k, v in ear.items():
                if isinstance(v, dict):
                    merged_ear[k] = v
            self.elevator_amusement_rules = merged_ear

        self.rules_source = str(path)

    @property
    def known_exact_prefixes(self) -> list[str]:
        """Return all known exact prefixes sorted longest-first."""
        return sorted(self.exact_codes.keys(), key=lambda s: (-len(s), s))

    def _infer_part_group(self, part: str | None, part_code: str | None) -> str | None:
        if part_code and part_code in self.part_codes:
            pg = self.part_codes[part_code].get("part_group")
            if pg:
                return str(pg)
        if not part:
            return None
        for token, group in (
            ("外壁", "外壁"),
            ("間仕切壁", "間仕切壁"),
            ("界壁", "界壁"),
            ("軒裏", "軒裏"),
            ("ひさし", "ひさし"),
            ("天井", "天井"),
            ("階段", "階段"),
            ("屋根", "屋根"),
            ("はり", "はり"),
            ("梁", "はり"),
            ("柱", "柱"),
            ("床", "床"),
            ("壁", "壁"),
        ):
            if token in part:
                return group
        return None

    def _decode_building_bitmask(self, prefix: str) -> dict[str, Any] | None:
        """Decode MLIT 4-, 5-, or 7-character building/structure bitmask prefix."""
        if len(prefix) not in (4, 5, 7) or not prefix.isalpha():
            return None
        if len(self.building_positions) < len(prefix):
            return None

        active_labels: list[str] = []
        law_articles: list[str] = []
        for idx, ch in enumerate(prefix):
            pos_rule = self.building_positions[idx]
            codes_map = pos_rule.get("codes") or {}
            if ch not in codes_map:
                return None
            entry = codes_map[ch]
            if ch != "N":
                ja = entry.get("ja") if isinstance(entry, dict) else str(entry)
                if ja and ja != "非該当":
                    active_labels.append(f"{pos_rule.get('name', '')}:{ja}")
                art = entry.get("law_article") if isinstance(entry, dict) else ""
                if art and art not in law_articles:
                    law_articles.append(art)

        is_structure = prefix[0] == "S"
        category = "工作物" if is_structure else "建築物（時刻歴・耐火・避難等）"
        st_type = (
            "工作物（構造計算等）"
            if is_structure
            else (" / ".join(active_labels) if active_labels else "建築物（一般認定）")
        )
        return {
            "structure_type": st_type,
            "structure_type_code": prefix,
            "duration_min": None,
            "part": "工作物全体" if is_structure else "建築物全体",
            "part_code": None,
            "part_group": None,
            "load_bearing": None,
            "category": category,
            "law_article": " / ".join(law_articles) if law_articles else "建築基準法第20条等",
            "group": "建築物／工作物",
            "sub_category": "・".join(active_labels) if active_labels else category,
            "description": f"{category}（記号: {prefix}）",
            "_is_structured_fire": False,
        }

    def _decode_elevator_or_amusement(self, prefix: str) -> dict[str, Any] | None:
        """Decode elevator (E*** / E*****), escalator (ES***), or amusement (A***) prefix."""
        if not prefix.isalpha():
            return None

        # Escalator: ES + 3 chars (5 chars total)
        if prefix.startswith("ES") and len(prefix) == 5:
            esc = self.elevator_amusement_rules.get("escalator") or {}
            positions = esc.get("positions") or []
            if len(positions) == 3:
                return self._match_positional_rule(
                    prefix,
                    body=prefix[2:],
                    positions=positions,
                    category="昇降機（エスカレーター）",
                    group="昇降機・遊戯施設",
                    default_law="建築基準法施行令第129条の12",
                    extra_allowed={},
                )

        # Elevator: E + 3 chars (legacy 4 chars) or E + 5 chars (current 6 chars)
        if prefix.startswith("E") and len(prefix) in (4, 6):
            elev = self.elevator_amusement_rules.get("elevator") or {}
            positions = elev.get("positions") or []
            body = prefix[1:]
            if len(positions) >= len(body):
                return self._match_positional_rule(
                    prefix,
                    body=body,
                    positions=positions[: len(body)],
                    category="昇降機（エレベーター）",
                    group="昇降機・遊戯施設",
                    default_law="建築基準法施行令第129条の4〜10",
                    extra_allowed={},
                )

        # Amusement facility: A + 3 chars (4 chars total; allow legacy pos2='C', pos3='S')
        if prefix.startswith("A") and len(prefix) == 4:
            amu = self.elevator_amusement_rules.get("amusement_facility") or {}
            positions = amu.get("positions") or []
            body = prefix[1:]
            if len(positions) == 3:
                return self._match_positional_rule(
                    prefix,
                    body=body,
                    positions=positions,
                    category="遊戯施設",
                    group="昇降機・遊戯施設",
                    default_law="建築基準法施行令第144条",
                    extra_allowed={1: {"C": "制御器（旧区分）"}, 2: {"S": "制動装置（旧区分）"}},
                )

        return None

    def _match_positional_rule(
        self,
        prefix: str,
        *,
        body: str,
        positions: list[dict[str, Any]],
        category: str,
        group: str,
        default_law: str,
        extra_allowed: dict[int, dict[str, str]],
    ) -> dict[str, Any] | None:
        active_items: list[str] = []
        laws: list[str] = []
        for idx, ch in enumerate(body):
            pos = positions[idx]
            codes = dict(pos.get("codes") or {})
            if idx in extra_allowed:
                for ek, ev in extra_allowed[idx].items():
                    codes.setdefault(ek, ev)
            if ch not in codes:
                return None
            if ch != "N":
                label = pos.get("name", "")
                val = codes[ch]
                active_items.append(f"{label}({val})" if val not in ("該当", "") else label)
                art = pos.get("law_article") or ""
                if art and art not in laws:
                    laws.append(art)

        summary = "・".join(active_items) if active_items else category
        return {
            "structure_type": category,
            "structure_type_code": prefix,
            "duration_min": None,
            "part": summary if active_items else None,
            "part_code": None,
            "part_group": None,
            "load_bearing": None,
            "category": category,
            "law_article": " / ".join(laws) if laws else default_law,
            "group": group,
            "sub_category": summary,
            "description": f"{category} / {summary}",
            "_is_structured_fire": False,
        }

    def _decode_prefix_metadata(
        self, prefix: str
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """Decode the prefix portion of a certification number."""
        warnings: list[str] = []

        # 1. Structured fire-resistive / fire-preventive code (FP060BE, QF045NE, NH030FL, FI020RF, ...)
        m_fire = self._structured_fire_re.match(prefix)
        exact_meta = self.exact_codes.get(prefix)

        if m_fire:
            st_code, dur_str, pt_code = m_fire.groups()
            dur = int(dur_str)
            st_info = self.structure_types[st_code]
            pt_info = self.part_codes[pt_code]

            if dur == 0:
                warnings.append("性能時間が000分（0分）となっています。")
                return None, warnings
            if dur not in STANDARD_FIRE_DURATIONS:
                warnings.append(
                    f"一般的な防耐火性能時間（20〜240分）以外の時間表記です: {dur}分"
                )

            meta: dict[str, Any] = {
                "structure_type": st_info.get("ja") or st_code,
                "structure_type_code": st_code,
                "duration_min": dur,
                "part": pt_info.get("ja") or pt_code,
                "part_code": pt_code,
                "part_group": self._infer_part_group(pt_info.get("ja"), pt_code),
                "load_bearing": pt_info.get("load_bearing"),
                "category": st_info.get("category") or "防耐火構造",
                "law_article": st_info.get("law_article"),
                "group": "防耐火構造",
                "sub_category": f"{st_info.get('ja', st_code)}（{dur}分間・{pt_info.get('ja', pt_code)}）",
                "description": st_info.get("description"),
                "_is_structured_fire": True,
            }
            if exact_meta:
                for k in ("category", "law_article", "group", "sub_category", "description"):
                    if exact_meta.get(k):
                        meta[k] = exact_meta[k]
                if exact_meta.get("load_bearing") is not None:
                    meta["load_bearing"] = exact_meta["load_bearing"]
            return meta, warnings

        # 2. Fire-compartment penetration code (PS060WL, PS120FL, ...)
        m_ps = _PENETRATION_RE.match(prefix)
        if m_ps:
            dur_str, pt_code = m_ps.groups()
            dur = int(dur_str)
            if dur == 0:
                warnings.append("性能時間が000分（0分）となっています。")
                return None, warnings
            if dur not in STANDARD_FIRE_DURATIONS:
                warnings.append(f"一般的な貫通部性能時間以外の表記です: {dur}分")
            part_label = "壁（貫通部）" if pt_code == "WL" else "床（貫通部）"
            meta = {
                "structure_type": "防火区画貫通部",
                "structure_type_code": "PS",
                "duration_min": dur,
                "part": part_label,
                "part_code": pt_code,
                "part_group": "壁" if pt_code == "WL" else "床",
                "load_bearing": None,
                "category": "防火区画貫通部",
                "law_article": "令第129条の2の4第1項第7号ハ（旧令第129条の2の5）",
                "group": "防火関係",
                "sub_category": f"{part_label}（{dur}分間）",
                "description": f"防火区画の{part_label}における{dur}分間遮炎措置",
                "_is_structured_fire": True,
            }
            if exact_meta:
                for k in ("category", "law_article", "group", "sub_category", "description"):
                    if exact_meta.get(k):
                        meta[k] = exact_meta[k]
            return meta, warnings

        # 3. Exact prefix lookup (NM, NE, QM, EA, EB, EC, SOI, FRM, TBFC, MFN, HSB, TFB*, SPRG, LAE, ...)
        if exact_meta:
            part_val = exact_meta.get("part")
            structure_type_val = (
                exact_meta.get("structure_type")
                or exact_meta.get("sub_category")
                or exact_meta.get("category")
            )
            meta = {
                "structure_type": structure_type_val,
                "structure_type_code": prefix,
                "duration_min": exact_meta.get("duration_min"),
                "part": part_val,
                "part_code": None,
                "part_group": self._infer_part_group(part_val, None),
                "load_bearing": exact_meta.get("load_bearing"),
                "category": exact_meta.get("category"),
                "law_article": exact_meta.get("law_article"),
                "group": exact_meta.get("group"),
                "sub_category": exact_meta.get("sub_category"),
                "description": exact_meta.get("description"),
                "_is_structured_fire": False,
            }
            return meta, warnings

        # 4. Building / structure positional bitmask (HFEB, MNNNNNN, SNNN, ...)
        bldg_meta = self._decode_building_bitmask(prefix)
        if bldg_meta is not None:
            return bldg_meta, warnings

        # 5. Elevator / escalator / amusement positional bitmask (ENNNUN, ESNNF, ANSE, ...)
        elev_meta = self._decode_elevator_or_amusement(prefix)
        if elev_meta is not None:
            return elev_meta, warnings

        return None, warnings

    def decode(self, raw_cert_no: str) -> DecodeResult:
        """Decode a Minister Certification number into structured metadata."""
        normalized = normalize_cert_no(raw_cert_no)
        warnings: list[str] = []

        if not normalized:
            return DecodeResult(
                raw_input=raw_cert_no,
                cert_no="",
                prefix="",
                serial=None,
                structure_type=None,
                duration_min=None,
                part=None,
                load_bearing=None,
                category=None,
                law_article=None,
                is_valid_format=False,
                warnings=["認定番号が空です。"],
                seinokyo_search_url=self.seinokyo_search_url,
            )

        if "-" in normalized:
            prefix, serial = normalized.split("-", 1)
        else:
            prefix, serial = normalized, None

        meta, prefix_warnings = self._decode_prefix_metadata(prefix)
        warnings.extend(prefix_warnings)

        # Handle missing hyphen
        if serial is None:
            if meta is not None:
                warnings.append(
                    "ハイフンおよび通算番号が不足しています（記号部のみ入力されています）。"
                )
            else:
                m_omitted = re.match(
                    r"^((?:FP|QF|PC|QP|NH|FI)\d{3}[A-Z]{2}|PS\d{3}(?:WL|FL))(\d{3,5})$",
                    normalized,
                )
                if m_omitted:
                    cand_prefix, cand_serial = m_omitted.groups()
                    cand_meta, _ = self._decode_prefix_metadata(cand_prefix)
                    if cand_meta is not None:
                        warnings.append(
                            f"ハイフンが欠落している可能性があります（想定形式: {cand_prefix}-{cand_serial}）。"
                        )
                        return DecodeResult(
                            raw_input=raw_cert_no,
                            cert_no=normalized,
                            prefix=cand_prefix,
                            serial=cand_serial,
                            structure_type=cand_meta["structure_type"],
                            duration_min=cand_meta["duration_min"],
                            part=cand_meta["part"],
                            load_bearing=cand_meta["load_bearing"],
                            category=cand_meta["category"],
                            law_article=cand_meta["law_article"],
                            is_valid_format=False,
                            warnings=warnings,
                            structure_type_code=cand_meta["structure_type_code"],
                            part_code=cand_meta["part_code"],
                            part_group=cand_meta["part_group"],
                            group=cand_meta["group"],
                            sub_category=cand_meta["sub_category"],
                            description=cand_meta["description"],
                            seinokyo_search_url=self.seinokyo_search_url,
                        )
                warnings.append("ハイフン区切りの認定番号形式（記号-通算番号）ではありません。")

            return DecodeResult(
                raw_input=raw_cert_no,
                cert_no=normalized,
                prefix=prefix,
                serial=None,
                structure_type=meta["structure_type"] if meta else None,
                duration_min=meta["duration_min"] if meta else None,
                part=meta["part"] if meta else None,
                load_bearing=meta["load_bearing"] if meta else None,
                category=meta["category"] if meta else None,
                law_article=meta["law_article"] if meta else None,
                is_valid_format=False,
                warnings=warnings,
                structure_type_code=meta["structure_type_code"] if meta else None,
                part_code=meta["part_code"] if meta else None,
                part_group=meta["part_group"] if meta else None,
                group=meta["group"] if meta else None,
                sub_category=meta["sub_category"] if meta else None,
                description=meta["description"] if meta else None,
                seinokyo_search_url=self.seinokyo_search_url,
            )

        # Validate serial based on prefix family
        if meta and meta.get("_is_structured_fire"):
            serial_valid = bool(_SERIAL_FIRE_RE.match(serial))
        elif prefix in _SHORT_SERIAL_PREFIXES:
            serial_valid = bool(_SERIAL_SHORT_OK_RE.match(serial))
        else:
            serial_valid = bool(_SERIAL_STANDARD_RE.match(serial))

        if not serial_valid:
            if _SERIAL_SHORT_OK_RE.match(serial):
                warnings.append(
                    f"通算番号の桁数が一般的な形式（4桁等）と異なります: {serial}"
                )
            else:
                warnings.append(f"通算番号の形式が不正です: {serial}")

        if meta is None:
            if re.match(r"^(?:FP|QF|PC|QP|NH|FI)\d{3}[A-Z]{2}$", prefix):
                part_part = prefix[-2:]
                warnings.append(f"未定義の部位記号です: {part_part}（プレフィックス: {prefix}）")
            elif re.match(r"^(?:FP|QF|PC|QP|NH|FI)\d+[A-Z]{2}$", prefix):
                warnings.append(
                    f"性能時間は3桁の数字で表記する必要があります（例: 045, 060）: {prefix}"
                )
            else:
                warnings.append(f"国交省の認定番号記号ルールに一致しないプレフィックスです: {prefix}")

            return DecodeResult(
                raw_input=raw_cert_no,
                cert_no=normalized,
                prefix=prefix,
                serial=serial,
                structure_type=None,
                duration_min=None,
                part=None,
                load_bearing=None,
                category=None,
                law_article=None,
                is_valid_format=False,
                warnings=warnings,
                seinokyo_search_url=self.seinokyo_search_url,
            )

        return DecodeResult(
            raw_input=raw_cert_no,
            cert_no=normalized,
            prefix=prefix,
            serial=serial,
            structure_type=meta["structure_type"],
            duration_min=meta["duration_min"],
            part=meta["part"],
            load_bearing=meta["load_bearing"],
            category=meta["category"],
            law_article=meta["law_article"],
            is_valid_format=serial_valid,
            warnings=warnings,
            structure_type_code=meta["structure_type_code"],
            part_code=meta["part_code"],
            part_group=meta["part_group"],
            group=meta["group"],
            sub_category=meta["sub_category"],
            description=meta["description"],
            seinokyo_search_url=self.seinokyo_search_url,
        )


_DEFAULT_DECODER: CertDecoder | None = None


def get_default_decoder(reload: bool = False) -> CertDecoder:
    """Return a cached default ``CertDecoder`` instance."""
    global _DEFAULT_DECODER
    if _DEFAULT_DECODER is None or reload:
        _DEFAULT_DECODER = CertDecoder()
    return _DEFAULT_DECODER


def decode_cert_no(
    cert_no: str,
    rules_path: Path | str | None = None,
) -> DecodeResult:
    """Convenience function to decode a single Minister Certification number."""
    if rules_path is not None:
        return CertDecoder(rules_path=rules_path).decode(cert_no)
    return get_default_decoder().decode(cert_no)
