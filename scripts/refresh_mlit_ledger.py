"""Check the MLIT PDF snapshot and rebuild the ledger when a source changes."""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
from urllib.parse import quote

from parse_pdfs import extract_ledger_date_period, parse_jp_date_to_iso

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "maintenance" / "mlit-pdf-sources.json"
LOG = ROOT / "maintenance" / "ledger-checks.md"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_script(name: str, *arguments: str | Path) -> str:
    command = [sys.executable, "-X", "utf8", str(ROOT / "scripts" / name), *map(str, arguments)]
    result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = result.stdout.decode("utf-8", errors="replace")
    print(output, end="")
    result.check_returncode()
    return output


def source_snapshot(sources: dict, category_ids: set[str]) -> dict:
    if {cat["id"] for cat in sources["categories"]} != category_ids:
        raise ValueError("The official PDF list has missing or unexpected categories.")
    files = {}
    for cat in sources["categories"]:
        pdfs = cat["pdfs"]
        if sorted(pdf["period"] for pdf in pdfs) != ["H25_after", "H25_before"]:
            raise ValueError(f"Expected two ledger PDFs for {cat['id']}.")
        for pdf in pdfs:
            files[f"{cat['id']}/{pdf['period']}"] = pdf
    for pdf in sources["reference_pdfs"]:
        files[f"reference/{pdf['id']}"] = pdf
    return {
        key: {"url": pdf["url"], "sha256": pdf["sha256"]}
        for key, pdf in sorted(files.items())
    }


def short_date(japanese: str) -> str:
    return re.sub(r"0(\d)([月日])", r"\1\2", japanese)


def update_public_text(old: dict, new: dict, rules: dict, joint_rows: int) -> dict[Path, str]:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    old_end = short_date(old["data_period"].split("～")[-1])
    new_end = short_date(new["data_period"].split("～")[-1])
    html = html.replace(old_end, new_end)

    old_total, new_total = (f"{m['total_records']:,}" for m in (old, new))
    readme = readme.replace(old_total, new_total).replace(quote(old_total), quote(new_total))
    backtick = chr(96)
    for previous, current in (
        (old["data_period"], new["data_period"]),
        (old_end, new_end),
        (old["page_last_updated"], new["page_last_updated"]),
        (
            f"{old['date_range']['min']}{backtick} 〜 {backtick}{old['date_range']['max']}",
            f"{new['date_range']['min']}{backtick} 〜 {backtick}{new['date_range']['max']}",
        ),
    ):
        if previous:
            readme = readme.replace(previous, current)

    counts = {cat["id"]: cat["record_count"] for cat in new["categories"]}
    lines = readme.splitlines(keepends=True)
    for index, line in enumerate(lines):
        cells = line.split("|")
        if len(cells) != 8:
            continue
        category_id = cells[2].strip().strip(backtick)
        if category_id in counts:
            cells[5] = f" {counts[category_id]:,} "
            lines[index] = "|".join(cells)
    readme = "".join(lines)
    readme = re.sub(r"共同申請継続行（[0-9,]+件）", f"共同申請継続行（{joint_rows:,}件）", readme)
    readme = re.sub(
        r"v[0-9.]+：[0-9]+記号・[0-9]+構造種別・[0-9]+部位コード・[0-9]+プレフィックス規則",
        f"v{rules['version']}：{len(rules['exact_codes'])}記号・"
        f"{len(rules['structure_types'])}構造種別・{len(rules['part_codes'])}部位コード・"
        f"{len(rules['prefix_patterns'])}プレフィックス規則",
        readme,
    )
    return {Path("README.md"): readme, Path("docs/index.html"): html}


def preserve_unchanged_metadata(generated: Path, current: Path) -> None:
    payload = read_json(generated)
    previous = read_json(current)
    payload.pop("generated_at", None)
    previous.pop("generated_at", None)
    if payload == previous:
        copyfile(current, generated)


def rebuild(pdf_dir: Path, work: Path, old: dict, sources: dict) -> dict:
    data, web = work / "data", work / "docs" / "data"
    args = ("--cache-dir", pdf_dir, "--data-dir", data, "--docs-data-dir", web)
    run_script("build_code_rules.py", *args)
    report = run_script("parse_pdfs.py", *args)
    for field in ("Empty cert_no rows", "Unparsed date rows", "Anomalies"):
        if not re.search(rf"^{field}\s*:\s*0(?:\D|$)", report, flags=re.MULTILINE):
            raise ValueError(f"PDF extraction needs review: {field}.")
    manifest = read_json(data / "manifest.json")
    if manifest != read_json(web / "manifest.json"):
        raise ValueError("CLI and Web manifests differ.")

    current_ends = {
        parse_jp_date_to_iso(extract_ledger_date_period(pdf_dir / pdf["filename"])[1])
        for cat in sources["categories"]
        for pdf in cat["pdfs"]
        if pdf["period"] == "H25_after"
    }
    old_end = parse_jp_date_to_iso(old["data_period"].split("～")[-1])
    if len(current_ends) != 1 or min(current_ends) < old_end or not min(current_ends):
        raise ValueError("The 21 current ledger PDFs have mixed, invalid, or older cutoff dates.")
    total = 0
    for cat in manifest["categories"]:
        records = read_json(data / cat["json_file"])
        if not records or len(records) != cat["record_count"]:
            raise ValueError(f"Unexpected number of records for {cat['id']}.")
        with (data / cat["csv_file"]).open(encoding="utf-8-sig", newline="") as handle:
            if list(csv.DictReader(handle)) != records:
                raise ValueError(f"JSON and CSV differ for {cat['id']}.")
        if read_json(web / cat["json_file"]) != records:
            raise ValueError(f"CLI and Web records differ for {cat['id']}.")
        total += len(records)
    if total != manifest["total_records"]:
        raise ValueError("The manifest total differs from the validated record count.")
    rules = read_json(data / "code_rules.json")
    if rules != read_json(web / "code_rules.json"):
        raise ValueError("CLI and Web decoding rules differ.")

    joint = re.search(r"^Cross-page merged\s*:\s*(\d+)", report, flags=re.MULTILINE)
    if not joint:
        raise ValueError("The PDF extraction report is incomplete.")
    for path, content in update_public_text(old, manifest, rules, int(joint[1])).items():
        (work / path).write_text(content, encoding="utf-8", newline="\n")
    records_changed = any(
        (web / cat["json_file"]).read_bytes()
        != (ROOT / "docs" / "data" / cat["json_file"]).read_bytes()
        for cat in manifest["categories"]
    )
    for directory in (Path("data"), Path("docs/data")):
        if not records_changed:
            preserve_unchanged_metadata(work / directory / "manifest.json", ROOT / directory / "manifest.json")
        preserve_unchanged_metadata(work / directory / "code_rules.json", ROOT / directory / "code_rules.json")
    bundled = work / "daijin_nintei" / "bundled_code_rules.json"
    bundled.parent.mkdir()
    copyfile(data / "code_rules.json", bundled)
    for path in work.rglob("*"):
        if path.is_file():
            copyfile(path, ROOT / path.relative_to(work))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", type=Path, help="Use already downloaded PDFs for local verification.")
    args = parser.parse_args()
    current = read_json(ROOT / "data" / "manifest.json")
    previous = read_json(STATE)
    with TemporaryDirectory(prefix="mlit-ledger-") as temporary:
        scratch = Path(temporary)
        pdf_dir = args.pdf_dir or scratch / "pdfs"
        if args.pdf_dir is None:
            run_script("fetch_mlit_pdfs.py", "--out-dir", pdf_dir)
        sources = read_json(pdf_dir / "sources.json")
        snapshot = source_snapshot(sources, {cat["id"] for cat in current["categories"]})
        changes = sorted(key for key in snapshot.keys() | previous.keys() if snapshot.get(key) != previous.get(key))
        if changes:
            current = rebuild(pdf_dir, scratch / "rebuild", current, sources)
            STATE.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8", newline="\n")
    checked = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M JST")
    summary = f"{len(changes)}件差し替え（{', '.join(changes)}）" if changes else "差し替えなし"
    end = short_date(current["data_period"].split("～")[-1])
    note = f"- {checked}｜{len(current['categories'])}区分確認｜{summary}｜収録末日 {end}｜{current['total_records']:,}件"
    with LOG.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(note + "\n")
    print(note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
