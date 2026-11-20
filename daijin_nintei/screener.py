"""Batch extractor, ledger screener, contradiction checker, and report exporter
for Japanese Minister Certification numbers (大臣認定番号).

Zero-hallucination design:
- Distinguishes clearly between ``FOUND`` (present in R5.4.30 MLIT ledger),
  ``UNLISTED_VALID_FORMAT`` (valid official MLIT symbol format, not in R5.4.30
  ledger — e.g. post-May 2023 certification or typo requiring verification via
  seinokyo), and ``INVALID_FORMAT`` (malformed or unrecognized symbol).
- Never falsely condemns valid-format unlisted numbers as invalid/fraudulent.
- Core functionality uses only Python stdlib; ``.pdf`` (``pymupdf``), ``.dxf``
  (``ezdxf``), and ``.ifc`` (``ifcopenshell``) are optional integrations.
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .db import CertDatabase
from .decoder import (
    SEINOKYO_SEARCH_URL,
    CertDecoder,
    DecodeResult,
    _FULLWIDTH_TRANSLATION,
    _HYPHEN_TRANSLATION,
    format_load_bearing_label,
    get_default_decoder,
    normalize_cert_no,
)

# Status constants
STATUS_FOUND = "FOUND"
STATUS_UNLISTED_VALID_FORMAT = "UNLISTED_VALID_FORMAT"
STATUS_INVALID_FORMAT = "INVALID_FORMAT"

STATUS_LABELS: dict[str, str] = {
    STATUS_FOUND: "R5.4台帳一致",
    STATUS_UNLISTED_VALID_FORMAT: (
        "R5.4帳簿未掲載（令和5年5月以降の新規認定、または誤記の要確認）"
    ),
    STATUS_INVALID_FORMAT: "形式不正・要確認",
}

LEGAL_DISCLAIMER = (
    "【免責事項・利用上の注意】\n"
    "本レポートは国土交通省公開「建築基準法に基づく構造方法等の認定に係る帳簿等」"
    "（令和5年4月30日現在・公共データ利用規約（第1.0版）（PDL1.0）に基づき加工）"
    "を用いた機械的スクリーニング補助結果です。"
    "令和5年5月以降の新規認定・変更・取消・承継や別添仕様の詳細条件は含まれていません。\n"
    "未掲載の認定番号や最終的な建築基準法上の適法性判断については、必ず有資格者（建築士等）が"
    f"建築性能基準推進協会 大臣認定検索システム（{SEINOKYO_SEARCH_URL}）および認定書・別添原本にてご確認ください。"
)

# Supported file extensions when scanning directories
TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {".txt", ".md", ".markdown", ".log", ".tsv", ".yaml", ".yml", ".xml", ".html", ".htm"}
)
SUPPORTED_EXTENSIONS: frozenset[str] = (
    TEXT_EXTENSIONS | {".csv", ".json", ".pdf", ".dxf", ".ifc"}
)
SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".cache"}
)

# URL masking pattern (1-to-1 length replacement preserves character offsets)
_URL_RE = re.compile(r"https?://[^\s<>\"'\]）)）」』]+")

# Context label announcing a certification number
_CERT_LABEL_RE = re.compile(
    r"(?:国土交通大臣認定|国交大臣認定|大臣認定|認定)(?:番号|No\.?|NO\.?|記号)?\s*[:：＝=・\s「『(（]*"
)

# Known Japanese building material / housing manufacturer aliases for mismatch checks
_MANUFACTURER_ALIASES: list[tuple[str, tuple[str, ...]]] = [
    ("吉野石膏", ("吉野石膏",)),
    ("ニチハ", ("ニチハ", "nichiha")),
    ("ケイミュー", ("ケイミュー", "kmew")),
    ("旭化成建材", ("旭化成建材", "旭化成ホームズ")),
    ("旭トステム外装", ("旭トステム",)),
    ("アイジー工業", ("アイジー工業",)),
    ("YKK AP", ("ykkap", "ykk ap", "ワイケイケイエイピー")),
    ("LIXIL", ("lixil", "リクシル", "トステム", "新日軽")),
    ("三協立山", ("三協立山", "三協アルミ", "立山アルミ")),
    ("三和シヤッター工業", ("三和シヤッター", "三和シャッター")),
    ("文化シヤッター", ("文化シヤッター", "文化シャッター")),
    ("東洋シヤッター", ("東洋シヤッター", "東洋シャッター")),
    ("大建工業", ("大建工業", "daiken")),
    ("パナソニック", ("パナソニック", "panasonic")),
    ("ニチアス", ("ニチアス", "nichias")),
    ("エーアンドエーマテリアル", ("エーアンドエーマテリアル", "a&aマテリアル")),
    ("太平洋マテリアル", ("太平洋マテリアル",)),
    ("日本インシュレーション", ("日本インシュレーション",)),
    ("ノダ", ("株式会社ノダ", "ノダ")),
    ("永大産業", ("永大産業",)),
    ("アイカ工業", ("アイカ工業", "aica")),
    ("旭ファイバーグラス", ("旭ファイバーグラス",)),
    ("マグ・イゾベール", ("マグイゾベール", "マグ・イゾベール")),
    ("パラマウント硝子工業", ("パラマウント硝子",)),
    ("因幡電機産業", ("因幡電機",)),
    ("古河電気工業", ("古河電工", "古河電気工業")),
    ("住友電気工業", ("住友電工", "住友電気工業")),
    ("積水化学工業", ("積水化学",)),
    ("積水ハウス", ("積水ハウス",)),
    ("大和ハウス工業", ("大和ハウス",)),
    ("住友林業", ("住友林業",)),
    ("ミサワホーム", ("ミサワホーム",)),
    ("トヨタホーム", ("トヨタホーム",)),
    ("田島ルーフィング", ("田島ルーフィング",)),
    ("サンゲツ", ("サンゲツ",)),
    ("東リ", ("東リ",)),
    ("リリカラ", ("リリカラ",)),
    ("シンコール", ("シンコール",)),
    ("JFE", ("jfeスチール", "jfe建材")),
    ("日本製鉄", ("日本製鉄", "日鉄建材")),
    ("タイガー", ("タイガーボード",)),
]

# Plausible fire-rating durations in minutes
_PLAUSIBLE_DURATIONS: frozenset[int] = frozenset(
    {5, 10, 15, 20, 30, 45, 60, 75, 90, 120, 150, 180, 240}
)

# Compatible part groups for PART_MISMATCH checks
_COMPATIBLE_PARTS: dict[str, frozenset[str]] = {
    "外壁": frozenset({"外壁", "壁"}),
    "間仕切壁": frozenset({"間仕切壁", "界壁", "壁"}),
    "界壁": frozenset({"界壁", "間仕切壁", "壁"}),
    "壁": frozenset({"壁", "外壁", "間仕切壁", "界壁"}),
    "床": frozenset({"床"}),
    "柱": frozenset({"柱"}),
    "はり": frozenset({"はり"}),
    "屋根": frozenset({"屋根"}),
    "軒裏": frozenset({"軒裏"}),
    "階段": frozenset({"階段"}),
    "ひさし": frozenset({"ひさし"}),
    "天井": frozenset({"天井"}),
}


def _translate_line_1to1(line: str) -> str:
    """1-to-1 width & hyphen normalization preserving exact string indices."""
    return line.translate(_FULLWIDTH_TRANSLATION).translate(_HYPHEN_TRANSLATION)


def _mask_urls_1to1(text: str) -> str:
    """Replace URL spans with spaces of identical length."""
    return _URL_RE.sub(lambda m: " " * (m.end() - m.start()), text)


def _normalize_company_name(name: str) -> str:
    """Normalize a Japanese corporate/manufacturer name for comparison."""
    s = unicodedata.normalize("NFKC", name).lower()
    for token in (
        "株式会社",
        "(株)",
        "有限会社",
        "(有)",
        "合同会社",
        "一般社団法人",
        "一般財団法人",
        "公益社団法人",
        "公益財団法人",
        "協同組合",
        "工業",
        " ",
        "\t",
        "・",
        "‐",
        "-",
    ):
        s = s.replace(token, "")
    return s.strip()


@dataclass
class ContradictionWarning:
    """A heuristic contradiction warning between context text and cert code/ledger."""

    code: str
    severity: str
    message: str
    basis: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractedCandidate:
    """Raw extracted candidate certification number with location and context."""

    raw_text: str
    cert_no: str
    source: str
    location: str
    context: str
    local_context: str
    start: int
    end: int


@dataclass
class ScreeningOccurrence:
    """Screening result for a single extracted certification occurrence."""

    source: str
    location: str
    raw_text: str
    cert_no: str
    status: str
    status_label: str
    decode: dict[str, Any]
    ledger_record: dict[str, Any] | None
    context: str
    warnings: list[dict[str, Any]] = field(default_factory=list)
    seinokyo_url: str = SEINOKYO_SEARCH_URL

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InventoryItem:
    """Deduplicated cross-file certification inventory entry."""

    cert_no: str
    count: int
    files: list[str]
    status: str
    status_label: str
    decode: dict[str, Any]
    ledger_record: dict[str, Any] | None
    warning_count: int
    warnings: list[dict[str, Any]]
    seinokyo_url: str = SEINOKYO_SEARCH_URL

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScreeningReport:
    """Complete batch screening report with Markdown, JSON, and CSV export."""

    generated_at: str
    data_period: str
    source_inputs: list[str]
    scanned_file_count: int
    skipped_files: list[dict[str, str]]
    occurrences: list[ScreeningOccurrence]
    inventory: list[InventoryItem]
    summary: dict[str, int]
    disclaimer: str = LEGAL_DISCLAIMER

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "data_period": self.data_period,
            "source_inputs": self.source_inputs,
            "scanned_file_count": self.scanned_file_count,
            "skipped_files": self.skipped_files,
            "summary": self.summary,
            "inventory": [item.to_dict() for item in self.inventory],
            "occurrences": [occ.to_dict() for occ in self.occurrences],
            "disclaimer": self.disclaimer,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_csv(self) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(
            [
                "source",
                "location",
                "cert_no",
                "status",
                "status_label",
                "prefix",
                "structure_type",
                "duration_min",
                "part",
                "load_bearing",
                "category",
                "applicant",
                "title",
                "date_jp",
                "warnings",
                "context",
                "seinokyo_url",
            ]
        )
        for occ in self.occurrences:
            dec = occ.decode
            rec = occ.ledger_record or {}
            warn_str = " / ".join(
                f"[{w['code']}] {w['message']}" for w in occ.warnings
            )
            lb_str = format_load_bearing_label(
                dec.get("load_bearing"),
                part_code=dec.get("part_code"),
                part_group=dec.get("part_group"),
                empty="",
            )
            writer.writerow(
                [
                    occ.source,
                    occ.location,
                    occ.cert_no,
                    occ.status,
                    occ.status_label,
                    dec.get("prefix") or "",
                    dec.get("structure_type") or "",
                    "" if dec.get("duration_min") is None else dec.get("duration_min"),
                    dec.get("part") or "",
                    lb_str,
                    dec.get("category") or "",
                    rec.get("applicant") or "",
                    rec.get("title") or "",
                    rec.get("date_jp") or "",
                    warn_str,
                    occ.context,
                    occ.seinokyo_url,
                ]
            )
        return buf.getvalue()

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# 大臣認定番号 スクリーニングレポート")
        lines.append("")
        lines.append(f"- **生成日時**: {self.generated_at}")
        lines.append(f"- **参照台帳基準**: {self.data_period}")
        lines.append(f"- **スキャン対象数**: {self.scanned_file_count} 件")
        if self.source_inputs:
            shown = ", ".join(f"`{s}`" for s in self.source_inputs[:10])
            if len(self.source_inputs) > 10:
                shown += f" ほか {len(self.source_inputs) - 10} 件"
            lines.append(f"- **対象ソース**: {shown}")
        lines.append("")

        # Summary KPI table
        s = self.summary
        lines.append("## 1. サマリー")
        lines.append("")
        lines.append("| 指標 | 件数 |")
        lines.append("| --- | ---: |")
        lines.append(f"| 検出総数（延べ） | {s['total_occurrences']} |")
        lines.append(f"| ユニーク認定番号数 | {s['unique_cert_count']} |")
        lines.append(f"| R5.4台帳一致 (`FOUND`) | {s['found_count']}（ユニーク {s['unique_found_count']}） |")
        lines.append(
            f"| R5.4帳簿未掲載・有効形式 (`UNLISTED_VALID_FORMAT`) | "
            f"{s['unlisted_valid_count']}（ユニーク {s['unique_unlisted_valid_count']}） |"
        )
        lines.append(
            f"| 形式不正・要確認 (`INVALID_FORMAT`) | "
            f"{s['invalid_format_count']}（ユニーク {s['unique_invalid_format_count']}） |"
        )
        lines.append(f"| 文脈矛盾・注意アラート検出箇所 | {s['warning_occurrence_count']} |")
        lines.append("")

        # Cross-file deduplicated inventory
        lines.append("## 2. クロスファイル認定番号一覧（重複集約）")
        lines.append("")
        if not self.inventory:
            lines.append("大臣認定番号の候補は検出されませんでした。")
            lines.append("")
        else:
            lines.append(
                "| 認定番号 | 判定ステータス | 出現回数 | 構造・種別 | 時間 | 部位 | 台帳申請者 / 構造方法名 | 検出ソース | 注意 |"
            )
            lines.append("| --- | --- | ---: | --- | ---: | --- | --- | --- | --- |")
            for item in self.inventory:
                dec = item.decode
                rec = item.ledger_record
                st = _md_escape(str(dec.get("structure_type") or "-"))
                dur = (
                    f"{dec['duration_min']}分"
                    if dec.get("duration_min") is not None
                    else "-"
                )
                part = _md_escape(str(dec.get("part") or "-"))
                if rec:
                    ledger_info = _md_escape(
                        f"{rec.get('applicant') or ''} / {rec.get('title') or ''}"
                    )
                elif item.status == STATUS_UNLISTED_VALID_FORMAT:
                    ledger_info = (
                        f"[建築性能基準推進協会（性能協）DBで要確認]({item.seinokyo_url})"
                    )
                else:
                    ledger_info = "-"
                files_str = _md_escape(", ".join(item.files[:3]))
                if len(item.files) > 3:
                    files_str += f" ほか{len(item.files) - 3}件"
                warn_badge = f"⚠ {item.warning_count}件" if item.warning_count else "-"
                status_short = (
                    "R5.4台帳一致"
                    if item.status == STATUS_FOUND
                    else (
                        "R5.4帳簿未掲載（有効形式）"
                        if item.status == STATUS_UNLISTED_VALID_FORMAT
                        else "形式不正・要確認"
                    )
                )
                lines.append(
                    f"| `{_md_escape(item.cert_no)}` | {status_short} | {item.count} | "
                    f"{st} | {dur} | {part} | {ledger_info} | {files_str} | {warn_badge} |"
                )
            lines.append("")

        # Alerts & Contradictions section
        alert_occs = [
            o
            for o in self.occurrences
            if o.warnings or o.status == STATUS_INVALID_FORMAT
        ]
        if alert_occs:
            lines.append("## 3. 注意アラート・形式不正の詳細")
            lines.append("")
            for occ in alert_occs:
                lines.append(
                    f"- **`{occ.cert_no}`** (`{occ.source}` / {occ.location}) — {occ.status_label}"
                )
                lines.append(f"  - 原文抜粋: `{_md_escape(occ.context[:200])}`")
                if occ.status == STATUS_INVALID_FORMAT:
                    for dw in occ.decode.get("warnings") or []:
                        lines.append(f"  - 形式チェック: {dw}")
                for w in occ.warnings:
                    lines.append(f"  - **[{w['code']}]** {w['message']}（根拠: {w['basis']}）")
            lines.append("")

        # Unlisted valid-format section
        unlisted_items = [
            i for i in self.inventory if i.status == STATUS_UNLISTED_VALID_FORMAT
        ]
        if unlisted_items:
            lines.append("## 4. R5.4帳簿未掲載（有効形式）の確認ガイド")
            lines.append("")
            lines.append(
                "以下の認定番号は国土交通省の記号ルールに適合していますが、令和5年4月30日時点の公開帳簿には掲載されていません。"
                "令和5年5月以降の新規認定または転記ミスの可能性があるため、"
                f"[一般社団法人 建築性能基準推進協会 大臣認定検索システム]({SEINOKYO_SEARCH_URL}) 等で最新状況を確認してください。"
            )
            lines.append("")
            for item in unlisted_items:
                dec = item.decode
                summary_parts = [
                    str(x)
                    for x in (
                        dec.get("structure_type"),
                        f"{dec['duration_min']}分" if dec.get("duration_min") else None,
                        dec.get("part"),
                        dec.get("law_article"),
                    )
                    if x
                ]
                lines.append(
                    f"- `{item.cert_no}`: {' / '.join(summary_parts) or '記号デコード済'} "
                    f"（検出: {', '.join(item.files)}）"
                )
            lines.append("")

        if self.skipped_files:
            lines.append("## 5. スキップ・未読込ファイル")
            lines.append("")
            for sk in self.skipped_files:
                lines.append(f"- `{sk['source']}`: {sk['reason']}")
            lines.append("")

        lines.append("---")
        lines.append("")
        lines.append(self.disclaimer)
        lines.append("")
        return "\n".join(lines)


def _md_escape(text: str) -> str:
    return (
        text.replace("|", "\\|")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", " ")
        .replace("\r", "")
    )


class CertScreener:
    """Extractor, ledger screener, and context-contradiction checker."""

    def __init__(
        self,
        db: CertDatabase | None = None,
        decoder: CertDecoder | None = None,
        *,
        data_dir: Path | str | None = None,
        auto_load_db: bool = True,
    ) -> None:
        self.decoder = decoder or (
            CertDecoder(rules_path=Path(data_dir) / "code_rules.json")
            if data_dir is not None
            else get_default_decoder()
        )
        if db is not None:
            self.db: CertDatabase | None = db
        elif auto_load_db:
            try:
                self.db = CertDatabase(data_dir=data_dir)
            except FileNotFoundError:
                self.db = None
        else:
            self.db = None

        self._compile_patterns()

    def _compile_patterns(self) -> None:
        exact_prefixes = self.decoder.known_exact_prefixes
        # Separate 2-char exact prefixes (require strict 3-5 digit serial) vs 3+ char prefixes
        short_exact = [re.escape(p) for p in exact_prefixes if len(p) <= 2]
        long_exact = [re.escape(p) for p in exact_prefixes if len(p) >= 3]

        st_codes = sorted(
            (k for k in self.decoder.structure_types if k != "PS"),
            key=len,
            reverse=True,
        )
        pt_codes = sorted(self.decoder.part_codes.keys(), key=len, reverse=True)
        st_pat = "|".join(re.escape(k) for k in st_codes)
        pt_pat = "|".join(re.escape(k) for k in pt_codes)

        serial_strict = r"\d{3,6}(?:-\d{1,3}){0,2}(?:\([0-9A-Z]{1,4}\))?"
        serial_short_ok = r"\d{1,6}(?:-\d{1,3}){0,2}(?:\([0-9A-Z]{1,4}\))?"
        serial_any = r"[0-9A-Z]{1,8}(?:-\d{1,3})?(?:\([0-9A-Z]{1,4}\))?"

        patterns: list[str] = []
        # 1. Structured fire codes (valid & near-miss malformed, e.g. FP060XX-0001, FP60BE-0001, FP060BE-12)
        patterns.append(rf"(?:{st_pat})\d{{1,4}}[A-Z]{{2}}\s*-\s*{serial_any}")
        # 2. Fire-compartment penetration codes (PS060WL-0123, PS60WL-0001, ...)
        patterns.append(rf"PS\d{{2,4}}[A-Z]{{2}}\s*-\s*{serial_any}")
        # 3. Unknown 2-letter prefix + 3 digits + known fire part code (catches XX060BE-0001)
        patterns.append(rf"[A-Z]{{2}}\d{{3}}(?:{pt_pat})\s*-\s*{serial_strict}")
        # 4. Short-serial building equipment prefixes (LAE-69, DW3N-99, etc.)
        patterns.append(rf"(?:LAE|DW3N)\s*-\s*{serial_short_ok}")
        # 5. Longer known exact prefixes (SOI, SOC, EBN, ECN, CAT, CAS, FRM, TBFC, MFN, HSB, TFBJ, SPRG, ...)
        if long_exact:
            patterns.append(rf"(?:{'|'.join(long_exact)})\s*-\s*{serial_strict}")
        # 6. Building / structure positional bitmasks (HFEB-0001, MNNNNNN-12013-1, SNNNNNN-12379)
        patterns.append(
            r"[HMSN][FSN][EN][BFSN](?:[LSPN](?:[SEWN][EN])?)?\s*-\s*\d{4,5}(?:-\d{1,2})?"
        )
        # 7. Elevator / escalator / amusement positional bitmasks (ENNNUN-2796, ESNNF-0001, ANSE-0076)
        patterns.append(
            r"(?:E[PN][CN][SN](?:[UN]{2})?|ES[PN][SN][FN]|A[PN][CSN][SEN])\s*-\s*\d{3,5}(?:-\d{1,2})?"
        )
        # 8. Short 2-letter known exact prefixes (NM, NE, QM, QE, RM, RE, DR, DW, UR, UW, EA, EB, EC, JM, JC, CC)
        if short_exact:
            patterns.append(rf"(?:{'|'.join(short_exact)})\s*-\s*{serial_strict}")

        self._candidate_re = re.compile(
            rf"(?<![A-Za-z0-9/_])({'|'.join(patterns)})(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
        # Context-anchored fallback for suspicious/malformed codes explicitly labeled as 認定番号
        self._labeled_re = re.compile(
            r"(?:国土交通大臣認定|国交大臣認定|大臣認定|認定)(?:番号|No\.?|NO\.?|記号)?\s*[:：＝=・\s「『(（]{0,4}"
            r"([A-Z][A-Z0-9]{1,11}\s*-\s*[A-Z0-9]{1,8}(?:-\d{1,3})?(?:\([0-9A-Z]{1,4}\))?|"
            rf"(?:{st_pat})\d{{3}}[A-Z]{{2}}\d{{3,5}})"
            r"(?![A-Za-z0-9])",
            re.IGNORECASE,
        )

    def extract_candidates_from_line(
        self,
        line: str,
        *,
        source: str,
        location: str,
        extra_context: str | None = None,
    ) -> list[ExtractedCandidate]:
        """Extract all candidate certification numbers from a single text line."""
        if not line or not line.strip():
            return []

        translated = _mask_urls_1to1(_translate_line_1to1(line))
        spans: list[tuple[int, int]] = []

        for m in self._candidate_re.finditer(translated):
            spans.append((m.start(1), m.end(1)))

        for m in self._labeled_re.finditer(translated):
            spans.append((m.start(1), m.end(1)))

        if not spans:
            return []

        # Deduplicate overlapping spans, preferring longer matches
        spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
        merged: list[tuple[int, int]] = []
        for start, end in spans:
            if merged and start < merged[-1][1]:
                continue
            merged.append((start, end))

        full_context = extra_context if extra_context is not None else line.strip()
        results: list[ExtractedCandidate] = []
        for idx, (start, end) in enumerate(merged):
            raw_text = line[start:end]
            cert_no = normalize_cert_no(raw_text)
            if not cert_no:
                continue

            if len(merged) == 1:
                local_ctx = full_context
            else:
                prev_end = merged[idx - 1][1] if idx > 0 else 0
                next_start = merged[idx + 1][0] if idx + 1 < len(merged) else len(line)
                left_seg = line[prev_end:start]
                right_seg = line[end:next_start]
                # Trim right segment at first clause separator if another cert follows
                if idx + 1 < len(merged):
                    m_sep = re.search(r"[、。/|｜;\t]", right_seg)
                    if m_sep:
                        right_seg = right_seg[: m_sep.start()]
                # Trim left segment after last clause separator belonging to previous cert
                if idx > 0:
                    sep_matches = list(re.finditer(r"[、。/|｜;\t]", left_seg))
                    if sep_matches:
                        left_seg = left_seg[sep_matches[-1].end() :]
                local_ctx = (left_seg + raw_text + right_seg).strip()

            results.append(
                ExtractedCandidate(
                    raw_text=raw_text,
                    cert_no=cert_no,
                    source=source,
                    location=location,
                    context=full_context,
                    local_context=local_ctx,
                    start=start,
                    end=end,
                )
            )
        return results

    def check_contradictions(
        self,
        local_context: str,
        decode_result: DecodeResult,
        ledger_record: dict[str, Any] | None,
    ) -> list[ContradictionWarning]:
        """Run fact-based heuristic contradiction checks against surrounding text."""
        if not local_context:
            return []

        warnings: list[ContradictionWarning] = []
        ctx_norm = _translate_line_1to1(local_context)
        # Mask the cert_no itself in ctx_norm so digits/codes inside the cert_no don't trigger checks
        if decode_result.cert_no:
            ctx_no_code = re.sub(
                re.escape(decode_result.cert_no), " ", ctx_norm, flags=re.IGNORECASE
            )
        else:
            ctx_no_code = ctx_norm

        # 1. Duration mismatch (e.g., context says "60分" or "1時間", code has 045)
        if decode_result.duration_min is not None:
            dur_text = re.sub(r"徒歩\s*\d+\s*分", " ", ctx_no_code)
            dur_text = re.sub(r"\d+\s*分の\s*\d+", " ", dur_text)
            detected_durations: list[tuple[int, str]] = []

            for m in re.finditer(
                r"(?<![0-9.])(\d+(?:\.\d+)?)\s*(分間|分(?![の割率類解離析担配岐譲])|min\b)",
                dur_text,
                re.IGNORECASE,
            ):
                val = float(m.group(1))
                if val.is_integer() and int(val) in _PLAUSIBLE_DURATIONS:
                    detected_durations.append((int(val), m.group(0)))

            for m in re.finditer(
                r"(?<![0-9.])(\d+(?:\.\d+)?)\s*(時間|hr\b)",
                dur_text,
                re.IGNORECASE,
            ):
                mins = round(float(m.group(1)) * 60)
                if mins in _PLAUSIBLE_DURATIONS:
                    detected_durations.append((mins, m.group(0)))

            if detected_durations:
                dur_values = {v for v, _ in detected_durations}
                if decode_result.duration_min not in dur_values:
                    labels = "・".join(dict.fromkeys(lbl for _, lbl in detected_durations))
                    warnings.append(
                        ContradictionWarning(
                            code="DURATION_MISMATCH",
                            severity="warning",
                            message=(
                                f"文脈には「{labels}」と記載がありますが、認定番号記号は"
                                f"{decode_result.duration_min}分間を示しています。"
                            ),
                            basis=f"文脈={labels} / 認定番号={decode_result.prefix}({decode_result.duration_min}分)",
                        )
                    )

        # 2. Structure type mismatch (e.g., context says "耐火外壁", code is QF 準耐火)
        if decode_result.structure_type_code in {"FP", "QF", "PC", "QP"}:
            st_work = ctx_no_code
            for noise in (
                "準耐火建築物",
                "耐火建築物",
                "耐火被覆",
                "耐火パテ",
                "耐火シート",
                "耐火ボード",
                "耐火レンガ",
                "耐火煉瓦",
                "耐火二層管",
                "耐火塗料",
                "耐火目地",
                "準防火地域",
                "防火地域",
                "防火区画",
                "特定防火設備",
                "防火設備",
                "防火戸",
                "防火ダンパー",
                "防火シャッター",
                "防火シヤッター",
                "防火材料",
                "防火管理",
                "防火対象物",
                "防火水槽",
            ):
                st_work = st_work.replace(noise, " ")

            found_st: list[tuple[str, str]] = []
            for st_code, pat in (
                ("QF", r"準耐火(?:構造|性能|外壁|間仕切壁?|壁|床|柱|梁|はり|屋根|軒裏|階段|仕様)"),
                ("QP", r"準防火(?:構造|性能|外壁|壁|仕様)"),
                ("FP", r"耐火(?:構造|性能|外壁|間仕切壁?|壁|床|柱|梁|はり|屋根|階段|仕様)"),
                ("PC", r"防火(?:構造|性能|外壁|壁|軒裏|仕様)"),
            ):
                for m in re.finditer(pat, st_work):
                    found_st.append((st_code, m.group(0)))
                st_work = re.sub(pat, " ", st_work)

            if found_st:
                st_codes_found = {c for c, _ in found_st}
                if decode_result.structure_type_code not in st_codes_found:
                    labels = "・".join(dict.fromkeys(lbl for _, lbl in found_st))
                    warnings.append(
                        ContradictionWarning(
                            code="STRUCTURE_TYPE_MISMATCH",
                            severity="warning",
                            message=(
                                f"文脈には「{labels}」と記載がありますが、認定番号記号（"
                                f"{decode_result.structure_type_code}）は「{decode_result.structure_type}」を示しています。"
                            ),
                            basis=(
                                f"文脈={labels} / 認定番号={decode_result.structure_type_code}"
                                f"({decode_result.structure_type})"
                            ),
                        )
                    )

        # 3. Load-bearing mismatch (only for wall parts: BE/BP/NE/NP or 壁 groups)
        is_wall_part = (
            decode_result.part_code in {"BE", "BP", "NE", "NP"}
            or decode_result.part_group in {"外壁", "間仕切壁", "壁"}
        )
        if is_wall_part and decode_result.load_bearing is not None:
            lb_work = ctx_no_code
            non_bearing_hits = re.findall(
                r"非耐力(?:外壁|間仕切壁?|壁|構造)?", lb_work
            )
            lb_masked = re.sub(r"非耐力(?:外壁|間仕切壁?|壁|構造)?", " ", lb_work)
            bearing_hits = re.findall(r"耐力(?:外壁|間仕切壁?|壁)", lb_masked)

            if bearing_hits and not non_bearing_hits and decode_result.load_bearing is False:
                hit_lbl = "・".join(dict.fromkeys(bearing_hits))
                warnings.append(
                    ContradictionWarning(
                        code="LOAD_BEARING_MISMATCH",
                        severity="warning",
                        message=(
                            f"文脈には「{hit_lbl}」と記載がありますが、認定番号記号（"
                            f"{decode_result.part_code or decode_result.prefix}）は非耐力（{decode_result.part}）を示しています。"
                        ),
                        basis=f"文脈={hit_lbl} / 認定番号={decode_result.part_code}({decode_result.part})",
                    )
                )
            elif non_bearing_hits and not bearing_hits and decode_result.load_bearing is True:
                hit_lbl = "・".join(dict.fromkeys(non_bearing_hits))
                warnings.append(
                    ContradictionWarning(
                        code="LOAD_BEARING_MISMATCH",
                        severity="warning",
                        message=(
                            f"文脈には「{hit_lbl}」と記載がありますが、認定番号記号（"
                            f"{decode_result.part_code or decode_result.prefix}）は耐力壁（{decode_result.part}）を示しています。"
                        ),
                        basis=f"文脈={hit_lbl} / 認定番号={decode_result.part_code}({decode_result.part})",
                    )
                )

        # 4. Part mismatch (e.g., context says "床", code is BE/BP/NE/NP wall)
        if decode_result.part_group and decode_result.part_group in _COMPATIBLE_PARTS:
            pt_work = ctx_no_code
            for noise in (
                "床面積",
                "延べ床",
                "延床",
                "床上",
                "床下",
                "床高",
                "床レベル",
                "床仕上",
                "床暖房",
                "床衝撃音",
                "壁倍率",
                "壁厚",
                "壁面",
                "壁紙",
                "壁装",
                "柱脚",
                "柱頭",
                "柱芯",
                "天井高",
                "天井裏",
                "天井懐",
                "天井仕上",
                "階段室",
                "屋根勾配",
                "屋根裏",
            ):
                pt_work = pt_work.replace(noise, " ")

            detected_parts: list[tuple[str, str]] = []
            for group_name, pat in (
                ("外壁", r"外壁"),
                ("間仕切壁", r"間仕切壁?"),
                ("界壁", r"界壁"),
                ("軒裏", r"軒裏"),
                ("ひさし", r"ひさし|小庇|庇"),
                ("天井", r"天井"),
                ("階段", r"階段"),
                ("屋根", r"屋根"),
                ("はり", r"はり|梁"),
                ("柱", r"柱"),
                ("床", r"床"),
            ):
                for m in re.finditer(pat, pt_work):
                    detected_parts.append((group_name, m.group(0)))
                pt_work = re.sub(pat, " ", pt_work)

            # Check generic "壁" only if no specific wall subtype was already found
            if not any(g in {"外壁", "間仕切壁", "界壁"} for g, _ in detected_parts):
                for m in re.finditer(r"壁", pt_work):
                    detected_parts.append(("壁", m.group(0)))

            if detected_parts:
                compatible = _COMPATIBLE_PARTS[decode_result.part_group]
                found_groups = {g for g, _ in detected_parts}
                if not (found_groups & compatible):
                    labels = "・".join(dict.fromkeys(lbl for _, lbl in detected_parts))
                    warnings.append(
                        ContradictionWarning(
                            code="PART_MISMATCH",
                            severity="warning",
                            message=(
                                f"文脈には部位「{labels}」の記載がありますが、認定番号記号は"
                                f"「{decode_result.part}」を示しています。"
                            ),
                            basis=f"文脈={labels} / 認定番号={decode_result.part_code or decode_result.prefix}({decode_result.part})",
                        )
                    )

        # 5. Applicant / Manufacturer mismatch (only when FOUND in ledger)
        if ledger_record and ledger_record.get("applicant"):
            ledger_applicant = str(ledger_record["applicant"])
            co_applicants = [
                _normalize_company_name(part)
                for part in ledger_applicant.split(" / ")
                if part.strip()
            ]
            norm_ledger_all = _normalize_company_name(ledger_applicant)
            detected_companies = self._extract_company_mentions(ctx_no_code)
            if detected_companies and norm_ledger_all:
                matched_any = False
                for raw_comp, alias_norms in detected_companies:
                    for cand_norm in alias_norms:
                        if not cand_norm:
                            continue
                        if cand_norm in norm_ledger_all or any(
                            ca and (cand_norm in ca or ca in cand_norm)
                            for ca in co_applicants
                        ):
                            matched_any = True
                            break
                    if matched_any:
                        break
                if not matched_any:
                    comp_labels = "・".join(
                        dict.fromkeys(raw_c for raw_c, _ in detected_companies)
                    )
                    warnings.append(
                        ContradictionWarning(
                            code="APPLICANT_MISMATCH",
                            severity="warning",
                            message=(
                                f"文脈にはメーカー／社名「{comp_labels}」の記載がありますが、"
                                f"R5.4台帳上の申請者は「{ledger_applicant}」です"
                                "（協会認定・OEM・承継等の可能性も含め原本で確認してください）。"
                            ),
                            basis=f"文脈={comp_labels} / R5.4台帳申請者={ledger_applicant}",
                        )
                    )

        # 6. Revoked or expired certification note in MLIT ledger date_jp
        if ledger_record and ledger_record.get("date_jp"):
            date_jp = str(ledger_record["date_jp"])
            date_jp_compact = re.sub(r"\s+", "", date_jp)
            if "取消" in date_jp_compact or "失効" in date_jp_compact:
                warnings.append(
                    ContradictionWarning(
                        code="CERT_REVOKED_OR_EXPIRED",
                        severity="warning",
                        message=(
                            f"R5.4台帳の認定年月日欄に取消・失効の記載があります（{date_jp}）。"
                            "当該認定の有効性・使用可否を必ず確認してください。"
                        ),
                        basis=f"R5.4台帳認定年月日={date_jp}",
                    )
                )

        return warnings

    def _extract_company_mentions(self, text: str) -> list[tuple[str, list[str]]]:
        """Extract explicit manufacturer / corporate mentions from local context,
        deduplicating shorter aliases already subsumed by a longer corporate name.
        """
        raw_hits: list[tuple[str, list[str]]] = []
        seen_raw: set[str] = set()
        text_lower = unicodedata.normalize("NFKC", text).lower()

        # 1. Explicit manufacturer label (e.g. メーカー：ニチハ)
        for m in re.finditer(
            r"(?:メーカー|製造者|製造元|申請者|社名)\s*[:：＝=]\s*([^\s、。,，/／|｜;；)）(（]{2,24})",
            text,
        ):
            val = m.group(1).strip()
            if val and val not in seen_raw:
                seen_raw.add(val)
                raw_hits.append((val, [_normalize_company_name(val)]))

        # 2. Corporate legal entity markers (株式会社〇〇 / 〇〇株式会社)
        for pat in (
            r"(?:株式会社|有限会社|合同会社|一般社団法人|一般財団法人)[^\s、。,，/／|｜;；(（)）「」『』:：]{2,20}",
            r"[^\s、。,，/／|｜;；(（)）「」『』:：]{2,20}(?:株式会社|有限会社|合同会社)",
        ):
            for m in re.finditer(pat, text):
                val = m.group(0).strip()
                if val and val not in seen_raw:
                    seen_raw.add(val)
                    raw_hits.append((val, [_normalize_company_name(val)]))

        # 3. Curated well-known Japanese building-material manufacturers
        for canonical, aliases in _MANUFACTURER_ALIASES:
            for alias in aliases:
                if alias.lower() in text_lower:
                    if canonical not in seen_raw:
                        seen_raw.add(canonical)
                        norms = [_normalize_company_name(canonical)] + [
                            _normalize_company_name(a) for a in aliases
                        ]
                        raw_hits.append((canonical, list(dict.fromkeys(norms))))
                    break

        # Deduplicate subsumed entries (e.g. "ニチハ株式会社" subsumes "ニチハ")
        # Keep the longer / more specific display name while merging alias lists.
        deduped: list[tuple[str, list[str]]] = []
        for label, norms in sorted(raw_hits, key=lambda x: -len(x[0])):
            label_norm = _normalize_company_name(label)
            merged_into_existing = False
            for idx, (ex_label, ex_norms) in enumerate(deduped):
                ex_label_norm = _normalize_company_name(ex_label)
                subsumed = (
                    (label in ex_label)
                    or (label_norm and ex_label_norm and (label_norm == ex_label_norm or label_norm in ex_label_norm))
                    or any(n and ex_label_norm and n == ex_label_norm for n in norms)
                )
                if subsumed:
                    combined = list(dict.fromkeys(ex_norms + norms))
                    deduped[idx] = (ex_label, combined)
                    merged_into_existing = True
                    break
            if not merged_into_existing:
                deduped.append((label, norms))

        return deduped

    def _classify_candidate(self, cand: ExtractedCandidate) -> ScreeningOccurrence:
        decode_res = self.decoder.decode(cand.cert_no)
        ledger_rec: dict[str, Any] | None = None
        if self.db is not None:
            ledger_rec = self.db.lookup(decode_res.cert_no)

        if ledger_rec is not None:
            status = STATUS_FOUND
        elif decode_res.is_valid_format:
            status = STATUS_UNLISTED_VALID_FORMAT
        else:
            status = STATUS_INVALID_FORMAT

        contradictions = self.check_contradictions(
            cand.local_context, decode_res, ledger_rec
        )

        return ScreeningOccurrence(
            source=cand.source,
            location=cand.location,
            raw_text=cand.raw_text,
            cert_no=decode_res.cert_no or cand.cert_no,
            status=status,
            status_label=STATUS_LABELS[status],
            decode=decode_res.to_dict(),
            ledger_record=ledger_rec,
            context=cand.context,
            warnings=[w.to_dict() for w in contradictions],
            seinokyo_url=decode_res.seinokyo_search_url,
        )

    def _build_report(
        self,
        occurrences: list[ScreeningOccurrence],
        source_inputs: list[str],
        scanned_file_count: int,
        skipped_files: list[dict[str, str]],
    ) -> ScreeningReport:
        # Build deduplicated cross-file inventory
        grouped: dict[str, list[ScreeningOccurrence]] = {}
        for occ in occurrences:
            grouped.setdefault(occ.cert_no, []).append(occ)

        inventory: list[InventoryItem] = []
        for cert_no, occs in sorted(
            grouped.items(),
            key=lambda kv: (
                0
                if kv[1][0].status == STATUS_INVALID_FORMAT
                else (1 if kv[1][0].status == STATUS_UNLISTED_VALID_FORMAT else 2),
                kv[0],
            ),
        ):
            first = occs[0]
            files = list(dict.fromkeys(o.source for o in occs))
            all_warnings: list[dict[str, Any]] = []
            seen_w: set[tuple[str, str]] = set()
            for o in occs:
                for w in o.warnings:
                    key = (w["code"], w["basis"])
                    if key not in seen_w:
                        seen_w.add(key)
                        all_warnings.append(w)

            inventory.append(
                InventoryItem(
                    cert_no=cert_no,
                    count=len(occs),
                    files=files,
                    status=first.status,
                    status_label=first.status_label,
                    decode=first.decode,
                    ledger_record=first.ledger_record,
                    warning_count=sum(len(o.warnings) for o in occs),
                    warnings=all_warnings,
                    seinokyo_url=first.seinokyo_url,
                )
            )

        summary = {
            "total_occurrences": len(occurrences),
            "unique_cert_count": len(inventory),
            "found_count": sum(1 for o in occurrences if o.status == STATUS_FOUND),
            "unlisted_valid_count": sum(
                1 for o in occurrences if o.status == STATUS_UNLISTED_VALID_FORMAT
            ),
            "invalid_format_count": sum(
                1 for o in occurrences if o.status == STATUS_INVALID_FORMAT
            ),
            "warning_occurrence_count": sum(1 for o in occurrences if o.warnings),
            "unique_found_count": sum(1 for i in inventory if i.status == STATUS_FOUND),
            "unique_unlisted_valid_count": sum(
                1 for i in inventory if i.status == STATUS_UNLISTED_VALID_FORMAT
            ),
            "unique_invalid_format_count": sum(
                1 for i in inventory if i.status == STATUS_INVALID_FORMAT
            ),
        }

        data_period = (
            self.db.data_period
            if self.db is not None
            else "台帳未読込（記号デコードのみモード）"
        )

        return ScreeningReport(
            generated_at=datetime.now(timezone.utc)
            .astimezone()
            .isoformat(timespec="seconds"),
            data_period=data_period,
            source_inputs=source_inputs,
            scanned_file_count=scanned_file_count,
            skipped_files=skipped_files,
            occurrences=occurrences,
            inventory=inventory,
            summary=summary,
        )

    def screen_text(self, text: str, *, source: str = "<text>") -> ScreeningReport:
        """Screen raw plain text and return a structured ``ScreeningReport``."""
        occurrences: list[ScreeningOccurrence] = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            cands = self.extract_candidates_from_line(
                line, source=source, location=f"L{lineno}"
            )
            for cand in cands:
                occurrences.append(self._classify_candidate(cand))
        return self._build_report(
            occurrences=occurrences,
            source_inputs=[source],
            scanned_file_count=1,
            skipped_files=[],
        )

    def _read_text_with_fallback(self, path: Path) -> str:
        raw = path.read_bytes()
        for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis", "euc_jp"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def _extract_from_csv(
        self, path: Path, source_label: str
    ) -> list[ExtractedCandidate]:
        text = self._read_text_with_fallback(path)
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        if not rows:
            return []

        header = [h.strip() for h in rows[0]]
        # Detect if row 0 looks like a header (no cert_no in row 0)
        header_has_cert = any(
            self.extract_candidates_from_line(cell, source=source_label, location="row 1")
            for cell in header
        )
        start_idx = 0 if header_has_cert else 1
        use_header = not header_has_cert

        candidates: list[ExtractedCandidate] = []
        for r_idx in range(start_idx, len(rows)):
            row = rows[r_idx]
            row_num = r_idx + 1
            if use_header:
                pairs = [
                    f"{header[c_idx]}:{cell.strip()}"
                    if c_idx < len(header) and header[c_idx]
                    else cell.strip()
                    for c_idx, cell in enumerate(row)
                    if cell.strip()
                ]
                row_context = " / ".join(pairs)
            else:
                row_context = " / ".join(cell.strip() for cell in row if cell.strip())

            # Extract from each cell so row_context carries full row metadata while avoiding cross-cell bleed if multiple certs
            row_cands: list[ExtractedCandidate] = []
            for c_idx, cell in enumerate(row):
                col_name = (
                    header[c_idx]
                    if use_header and c_idx < len(header) and header[c_idx]
                    else f"col{c_idx + 1}"
                )
                cell_cands = self.extract_candidates_from_line(
                    cell,
                    source=source_label,
                    location=f"row {row_num} ({col_name})",
                    extra_context=row_context,
                )
                row_cands.extend(cell_cands)

            # If only one cert exists in this row, give it the whole row context for contradiction checks!
            if len(row_cands) == 1:
                row_cands[0].local_context = row_context
            candidates.extend(row_cands)

        return candidates

    def _extract_from_json(
        self, path: Path, source_label: str
    ) -> list[ExtractedCandidate]:
        text = self._read_text_with_fallback(path)
        data = json.loads(text)
        candidates: list[ExtractedCandidate] = []

        def _walk(node: Any, jpath: str, sibling_context: str | None = None) -> None:
            if isinstance(node, dict):
                # Build sibling scalar summary for context-aware contradiction checks
                scalar_parts = [
                    f"{k}:{v}"
                    for k, v in node.items()
                    if isinstance(v, (str, int, float, bool)) and str(v).strip()
                ]
                obj_ctx = " / ".join(scalar_parts)
                for k, v in node.items():
                    _walk(v, f"{jpath}.{k}", sibling_context=obj_ctx)
            elif isinstance(node, list):
                for i, item in enumerate(node):
                    _walk(item, f"{jpath}[{i}]", sibling_context=None)
            elif isinstance(node, str):
                cands = self.extract_candidates_from_line(
                    node,
                    source=source_label,
                    location=jpath,
                    extra_context=sibling_context or node,
                )
                if len(cands) == 1 and sibling_context:
                    cands[0].local_context = sibling_context
                candidates.extend(cands)

        _walk(data, "$")
        return candidates

    def _extract_from_pdf(
        self, path: Path, source_label: str
    ) -> tuple[list[ExtractedCandidate], str | None]:
        try:
            import pymupdf  # type: ignore[import-not-found]
        except ImportError:
            try:
                import fitz as pymupdf  # type: ignore[import-not-found]
            except ImportError:
                return (
                    [],
                    "PDF解析にはオプション依存パッケージ `pymupdf` が必要です（pip install pymupdf）",
                )

        candidates: list[ExtractedCandidate] = []
        with pymupdf.open(str(path)) as doc:
            for page_idx, page in enumerate(doc, start=1):
                page_text = page.get_text("text") or ""
                for lineno, line in enumerate(page_text.splitlines(), start=1):
                    candidates.extend(
                        self.extract_candidates_from_line(
                            line,
                            source=source_label,
                            location=f"p.{page_idx}:L{lineno}",
                        )
                    )
        return candidates, None

    def _extract_from_dxf(
        self, path: Path, source_label: str
    ) -> tuple[list[ExtractedCandidate], str | None]:
        try:
            import ezdxf  # type: ignore[import-not-found]
        except ImportError:
            return (
                [],
                "DXF解析にはオプション依存パッケージ `ezdxf` が必要です（pip install ezdxf）",
            )

        doc = ezdxf.readfile(str(path))
        candidates: list[ExtractedCandidate] = []

        def _extract_entity_text(entity: Any) -> str:
            etype = entity.dxftype()
            if etype == "MTEXT":
                try:
                    return str(entity.plain_text())
                except Exception:
                    return str(getattr(entity, "text", "") or "")
            if etype in {"TEXT", "ATTRIB", "ATTDEF"}:
                return str(getattr(entity.dxf, "text", "") or "")
            if etype == "DIMENSION":
                return str(getattr(entity.dxf, "text", "") or "")
            return ""

        def _scan_space(entities: Iterable[Any], space_name: str) -> None:
            for idx, entity in enumerate(entities, start=1):
                etype = entity.dxftype()
                if etype == "INSERT":
                    # Scan attached ATTRIB entities on block inserts
                    for attrib in getattr(entity, "attribs", []):
                        txt = _extract_entity_text(attrib)
                        layer = getattr(attrib.dxf, "layer", "0")
                        for lno, line in enumerate(txt.splitlines(), start=1):
                            candidates.extend(
                                self.extract_candidates_from_line(
                                    line,
                                    source=source_label,
                                    location=f"{space_name}:{layer}:ATTRIB#{idx}:L{lno}",
                                )
                            )
                    continue
                txt = _extract_entity_text(entity)
                if not txt:
                    continue
                layer = getattr(entity.dxf, "layer", "0")
                for lno, line in enumerate(txt.splitlines(), start=1):
                    candidates.extend(
                        self.extract_candidates_from_line(
                            line,
                            source=source_label,
                            location=f"{space_name}:{layer}:{etype}#{idx}:L{lno}",
                        )
                    )

        _scan_space(doc.modelspace(), "ModelSpace")
        for layout in doc.layouts:
            if layout.name != "Model":
                _scan_space(layout, f"Layout[{layout.name}]")
        for block in doc.blocks:
            if not block.name.startswith("*"):
                _scan_space(block, f"Block[{block.name}]")

        return candidates, None

    def _extract_from_ifc(
        self, path: Path, source_label: str
    ) -> tuple[list[ExtractedCandidate], str | None]:
        try:
            import ifcopenshell  # type: ignore[import-not-found]
        except ImportError:
            return (
                [],
                "IFC解析にはオプション依存パッケージ `ifcopenshell` が必要です（pip install ifcopenshell）",
            )

        model = ifcopenshell.open(str(path))
        candidates: list[ExtractedCandidate] = []

        # Scan properties, classification references, and building elements
        for prop in model.by_type("IfcPropertySingleValue"):
            val = getattr(prop, "NominalValue", None)
            wrapped = getattr(val, "wrappedValue", None) if val is not None else None
            if isinstance(wrapped, str) and wrapped.strip():
                pname = getattr(prop, "Name", "") or ""
                ctx = f"{pname}: {wrapped}" if pname else wrapped
                candidates.extend(
                    self.extract_candidates_from_line(
                        ctx,
                        source=source_label,
                        location=f"IFC#{prop.id()}:{pname}",
                    )
                )

        for cref in model.by_type("IfcClassificationReference"):
            parts = [
                str(getattr(cref, attr, "") or "")
                for attr in ("Identification", "ItemReference", "Name", "Description")
                if getattr(cref, attr, None)
            ]
            line = " / ".join(parts)
            if line:
                candidates.extend(
                    self.extract_candidates_from_line(
                        line,
                        source=source_label,
                        location=f"IFC#{cref.id()}:IfcClassificationReference",
                    )
                )

        for elem in model.by_type("IfcObjectDefinition"):
            parts = [
                str(getattr(elem, attr, "") or "")
                for attr in ("Name", "Description", "ObjectType", "Tag")
                if getattr(elem, attr, None)
            ]
            line = " / ".join(parts)
            if line:
                candidates.extend(
                    self.extract_candidates_from_line(
                        line,
                        source=source_label,
                        location=f"IFC#{elem.id()}:{elem.is_a()}",
                    )
                )

        return candidates, None

    def _scan_single_file(
        self, path: Path, display_name: str
    ) -> tuple[list[ScreeningOccurrence], dict[str, str] | None]:
        suffix = path.suffix.lower()
        try:
            if suffix == ".csv":
                cands = self._extract_from_csv(path, display_name)
            elif suffix == ".json":
                cands = self._extract_from_json(path, display_name)
            elif suffix == ".pdf":
                cands, skip_reason = self._extract_from_pdf(path, display_name)
                if skip_reason:
                    return [], {"source": display_name, "reason": skip_reason}
            elif suffix == ".dxf":
                cands, skip_reason = self._extract_from_dxf(path, display_name)
                if skip_reason:
                    return [], {"source": display_name, "reason": skip_reason}
            elif suffix == ".ifc":
                cands, skip_reason = self._extract_from_ifc(path, display_name)
                if skip_reason:
                    return [], {"source": display_name, "reason": skip_reason}
            else:
                text = self._read_text_with_fallback(path)
                cands = []
                for lineno, line in enumerate(text.splitlines(), start=1):
                    cands.extend(
                        self.extract_candidates_from_line(
                            line, source=display_name, location=f"L{lineno}"
                        )
                    )
        except Exception as exc:
            return [], {"source": display_name, "reason": f"読込エラー: {exc}"}

        return [self._classify_candidate(c) for c in cands], None

    def screen_file(self, path: Path | str) -> ScreeningReport:
        """Screen a single file and return a ``ScreeningReport``."""
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"ファイルが見つかりません: {file_path}")
        display = file_path.name
        occs, skipped = self._scan_single_file(file_path, display)
        return self._build_report(
            occurrences=occs,
            source_inputs=[str(file_path)],
            scanned_file_count=0 if skipped else 1,
            skipped_files=[skipped] if skipped else [],
        )

    def screen_path(self, path: Path | str) -> ScreeningReport:
        """Screen a file or recursively screen all supported files in a directory."""
        target = Path(path)
        if not target.exists():
            raise FileNotFoundError(f"パスが見つかりません: {target}")

        if target.is_file():
            return self.screen_file(target)

        files: list[Path] = []
        for item in sorted(target.rglob("*")):
            if not item.is_file():
                continue
            if any(part in SKIP_DIR_NAMES for part in item.relative_to(target).parts[:-1]):
                continue
            if item.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(item)

        occurrences: list[ScreeningOccurrence] = []
        skipped_files: list[dict[str, str]] = []
        scanned_count = 0

        for f in files:
            rel_name = f.relative_to(target).as_posix()
            occs, skipped = self._scan_single_file(f, rel_name)
            if skipped:
                skipped_files.append(skipped)
            else:
                scanned_count += 1
                occurrences.extend(occs)

        return self._build_report(
            occurrences=occurrences,
            source_inputs=[str(target)],
            scanned_file_count=scanned_count,
            skipped_files=skipped_files,
        )
