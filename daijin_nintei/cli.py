"""Command-line interface for decoding, searching, and batch-screening
Japanese Minister Certification numbers (大臣認定番号).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .db import CertDatabase
from .decoder import SEINOKYO_SEARCH_URL, CertDecoder, format_load_bearing_label
from .screener import (
    STATUS_FOUND,
    STATUS_INVALID_FORMAT,
    STATUS_LABELS,
    STATUS_UNLISTED_VALID_FORMAT,
    CertScreener,
)


def _configure_stdio_utf8() -> None:
    """Ensure UTF-8 output on Windows consoles without failing in captured streams."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                enc = (getattr(stream, "encoding", "") or "").lower()
                if enc and enc != "utf-8":
                    stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _try_open_db(data_dir: str | None, *, required: bool) -> CertDatabase | None:
    try:
        return CertDatabase(data_dir=data_dir)
    except FileNotFoundError as exc:
        if required:
            print(f"エラー: {exc}", file=sys.stderr)
            print(
                "ローカル台帳データ（data/manifest.json）が見つかりません。"
                " --data-dir で指定するか、データ生成スクリプトを実行してください。",
                file=sys.stderr,
            )
            raise SystemExit(2) from exc
        return None


def _cmd_decode(args: argparse.Namespace) -> int:
    rules_path = (
        Path(args.data_dir) / "code_rules.json" if args.data_dir else None
    )
    decoder = CertDecoder(rules_path=rules_path)
    db = None if args.no_db else _try_open_db(args.data_dir, required=False)

    results: list[dict[str, Any]] = []
    for raw_no in args.cert_nos:
        dec = decoder.decode(raw_no)
        ledger = db.lookup(dec.cert_no) if (db is not None and dec.cert_no) else None
        if ledger is not None:
            status = STATUS_FOUND
        elif dec.is_valid_format:
            status = STATUS_UNLISTED_VALID_FORMAT
        else:
            status = STATUS_INVALID_FORMAT

        results.append(
            {
                "decode": dec.to_dict(),
                "status": status,
                "status_label": STATUS_LABELS[status],
                "ledger_record": ledger,
                "seinokyo_url": SEINOKYO_SEARCH_URL,
            }
        )

    if args.json:
        payload: Any = results[0] if len(results) == 1 else results
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    for idx, item in enumerate(results):
        if idx > 0:
            print()
        dec_d = item["decode"]
        rec = item["ledger_record"]
        status = item["status"]
        lb_str = format_load_bearing_label(
            dec_d["load_bearing"],
            part_code=dec_d.get("part_code"),
            part_group=dec_d.get("part_group"),
            empty="-",
        )
        dur_str = (
            f"{dec_d['duration_min']}分"
            if dec_d["duration_min"] is not None
            else "-"
        )

        print(f"=== 認定番号デコード: {dec_d['cert_no'] or dec_d['raw_input']} ===")
        print(f"  正規化番号   : {dec_d['cert_no'] or '(空)'}")
        print(f"  記号 / 通算  : {dec_d['prefix'] or '-'} / {dec_d['serial'] or '-'}")
        print(f"  形式判定     : {'有効形式' if dec_d['is_valid_format'] else '形式不正・要確認'}")
        print(f"  構造・種別   : {dec_d['structure_type'] or '-'}")
        print(f"  性能時間     : {dur_str}")
        print(f"  部位         : {dec_d['part'] or '-'}")
        print(f"  耐力区分     : {lb_str}")
        print(f"  カテゴリ     : {dec_d['category'] or '-'}")
        print(f"  根拠条文     : {dec_d['law_article'] or '-'}")
        if dec_d["warnings"]:
            for w in dec_d["warnings"]:
                print(f"  [注意]       : {w}")

        if db is not None:
            print(f"  台帳照合     : {item['status_label']} ({status})")
            if rec:
                print(f"  構造方法名   : {rec.get('title') or '-'}")
                print(f"  申請者       : {rec.get('applicant') or '-'}")
                print(f"  認定年月日   : {rec.get('date_jp') or '-'} ({rec.get('date') or '-'})")
                print(f"  性能評価機関 : {rec.get('evaluator') or '-'}")
                print(f"  台帳カテゴリ : {rec.get('category_name') or '-'}")
            elif status == STATUS_UNLISTED_VALID_FORMAT:
                print(f"  最新確認URL  : {SEINOKYO_SEARCH_URL}")

    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    db = _try_open_db(args.data_dir, required=True)
    assert db is not None

    if args.list_categories:
        cats = db.list_categories()
        if args.json:
            print(json.dumps(cats, ensure_ascii=False, indent=2))
            return 0
        print(f"収録カテゴリ一覧（総件数: {db.total_records:,} 件 / 基準: {db.data_period}）")
        for c in cats:
            pfxs = ", ".join((c.get("prefixes") or [])[:6])
            if len(c.get("prefixes") or []) > 6:
                pfxs += ", ..."
            print(
                f"  - {c['id']:<28} {c['name']} ({c.get('record_count', 0):,}件) [{pfxs}]"
            )
        return 0

    results, total = db.search_with_total(
        query=args.q,
        category_id=args.category,
        applicant=args.applicant,
        prefix=args.prefix,
        period=args.period,
        evaluator=args.evaluator,
        limit=args.limit,
        offset=args.offset,
    )

    if args.json:
        print(
            json.dumps(
                {"total": total, "count": len(results), "results": results},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"検索結果: {total:,} 件中 {len(results):,} 件表示（基準: {db.data_period}）")
    if not results:
        print("一致するレコードはありませんでした。")
        return 0

    for rec in results:
        print(
            f"- {rec.get('cert_no', ''):<16} | {rec.get('date_jp', ''):<13} | "
            f"{rec.get('applicant', '')} | {rec.get('title', '')} "
            f"[{rec.get('category_name', '')}]"
        )
    return 0


def _cmd_screen(args: argparse.Namespace) -> int:
    db = None if args.no_db else _try_open_db(args.data_dir, required=False)
    rules_path = (
        Path(args.data_dir) / "code_rules.json" if args.data_dir else None
    )
    decoder = CertDecoder(rules_path=rules_path)
    screener = CertScreener(db=db, decoder=decoder, auto_load_db=False)

    target = args.target
    if target == "-":
        text = sys.stdin.read()
        report = screener.screen_text(text, source="<stdin>")
    else:
        path = Path(target)
        if not path.exists():
            print(f"エラー: 指定されたパスが見つかりません: {path}", file=sys.stderr)
            return 2
        report = screener.screen_path(path)

    fmt = args.format
    if fmt == "json":
        output_text = report.to_json()
    elif fmt == "csv":
        output_text = report.to_csv()
    else:
        output_text = report.to_markdown()

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Use UTF-8 with BOM for CSV files so Excel opens Japanese characters cleanly
        enc = "utf-8-sig" if fmt == "csv" else "utf-8"
        out_path.write_text(output_text, encoding=enc)
        print(
            f"スクリーニング結果を出力しました: {out_path} "
            f"(検出 {report.summary['total_occurrences']}件 / "
            f"ユニーク {report.summary['unique_cert_count']}件 / "
            f"アラート {report.summary['warning_occurrence_count']}件)"
        )
    else:
        print(output_text)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="screen_cert_ids.py",
        description=(
            "国土交通省 大臣認定番号（構造方法等の認定）デコーダ・検索・一括スクリーニングCLI"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # decode
    p_decode = subparsers.add_parser(
        "decode",
        help="大臣認定番号の記号ルールをデコードし、R5.4台帳と照合する",
    )
    p_decode.add_argument(
        "cert_nos",
        nargs="+",
        help="デコードする大臣認定番号（複数指定可。全角・異体ハイフン対応）",
    )
    p_decode.add_argument(
        "--json",
        action="store_true",
        help="結果をJSON形式で出力する",
    )
    p_decode.add_argument(
        "--no-db",
        action="store_true",
        help="ローカル台帳との照合をスキップし、記号デコードのみ実行する",
    )
    p_decode.add_argument(
        "--data-dir",
        default=None,
        help="data/ ディレクトリのパス（省略時はリポジトリ内の data/）",
    )
    p_decode.set_defaults(func=_cmd_decode)

    # search
    p_search = subparsers.add_parser(
        "search",
        help="ローカルのR5.4国交省公開帳簿データを検索する",
    )
    p_search.add_argument(
        "--q",
        default=None,
        help="フリーワード検索（スペース区切りでAND検索）",
    )
    p_search.add_argument(
        "--category",
        default=None,
        help="カテゴリIDで絞り込む（--list-categories で確認可）",
    )
    p_search.add_argument(
        "--applicant",
        default=None,
        help="申請者（メーカー名等）で部分一致絞り込み",
    )
    p_search.add_argument(
        "--prefix",
        default=None,
        help="認定番号プレフィックスで前方一致絞り込み（例: FP060BE, NM, SOI）",
    )
    p_search.add_argument(
        "--period",
        choices=["H25_after", "H25_before"],
        default=None,
        help="帳簿区分（H25_after / H25_before）で絞り込む",
    )
    p_search.add_argument(
        "--evaluator",
        default=None,
        help="性能評価機関名で部分一致絞り込み",
    )
    p_search.add_argument(
        "--limit",
        type=int,
        default=50,
        help="最大表示件数（デフォルト: 50）",
    )
    p_search.add_argument(
        "--offset",
        type=int,
        default=0,
        help="取得開始オフセット（デフォルト: 0）",
    )
    p_search.add_argument(
        "--list-categories",
        action="store_true",
        help="収録カテゴリ一覧を表示する",
    )
    p_search.add_argument(
        "--json",
        action="store_true",
        help="結果をJSON形式で出力する",
    )
    p_search.add_argument(
        "--data-dir",
        default=None,
        help="data/ ディレクトリのパス（省略時はリポジトリ内の data/）",
    )
    p_search.set_defaults(func=_cmd_search)

    # screen
    p_screen = subparsers.add_parser(
        "screen",
        help="ファイルまたはディレクトリから大臣認定番号を一括抽出・照合・矛盾チェックする",
    )
    p_screen.add_argument(
        "target",
        help="対象ファイルまたはディレクトリ（'-' で標準入力から読込）",
    )
    p_screen.add_argument(
        "--format",
        choices=["md", "json", "csv"],
        default="md",
        help="レポート出力形式（md / json / csv、デフォルト: md）",
    )
    p_screen.add_argument(
        "--out",
        default=None,
        help="出力ファイルパス（省略時は標準出力）",
    )
    p_screen.add_argument(
        "--no-db",
        action="store_true",
        help="ローカル台帳との照合をスキップし、記号デコードと文脈チェックのみ実行する",
    )
    p_screen.add_argument(
        "--data-dir",
        default=None,
        help="data/ ディレクトリのパス（省略時はリポジトリ内の data/）",
    )
    p_screen.set_defaults(func=_cmd_screen)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        _configure_stdio_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
