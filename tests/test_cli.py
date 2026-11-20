"""CLI tests for daijin_nintei.cli and tools/screen_cert_ids.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from daijin_nintei.cli import main

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLI_SCRIPT = _REPO_ROOT / "tools" / "screen_cert_ids.py"


class TestCliInProcess:
    def test_decode_subcommand_json(self, mock_data_dir: Path, capsys) -> None:
        rc = main(
            ["decode", "ＦＰ０６０ＢＥ－０１２３", "--json", "--data-dir", str(mock_data_dir)]
        )
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["decode"]["cert_no"] == "FP060BE-0123"
        assert data["status"] == "FOUND"
        assert data["ledger_record"]["applicant"] == "吉野石膏株式会社"

    def test_search_subcommand(self, mock_data_dir: Path, capsys) -> None:
        rc = main(
            [
                "search",
                "--applicant",
                "吉野石膏",
                "--json",
                "--data-dir",
                str(mock_data_dir),
            ]
        )
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["total"] == 2
        assert {r["cert_no"] for r in data["results"]} == {"FP060BE-0123", "SOI-0160"}

    def test_search_list_categories(self, mock_data_dir: Path, capsys) -> None:
        rc = main(["search", "--list-categories", "--data-dir", str(mock_data_dir)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "fireproof" in out
        assert "fire_material" in out

    def test_screen_subcommand_formats_and_out_file(
        self, mock_data_dir: Path, tmp_path: Path, capsys
    ) -> None:
        spec_file = tmp_path / "spec.txt"
        spec_file.write_text(
            "60分耐火外壁 QF045NE-0001\n新規認定 FP060BE-9999\n",
            encoding="utf-8",
        )
        out_md = tmp_path / "out" / "report.md"
        rc = main(
            [
                "screen",
                str(spec_file),
                "--format",
                "md",
                "--out",
                str(out_md),
                "--data-dir",
                str(mock_data_dir),
            ]
        )
        assert rc == 0
        assert "スクリーニング結果を出力しました" in capsys.readouterr().out
        assert out_md.is_file()
        md_text = out_md.read_text(encoding="utf-8")
        assert "QF045NE-0001" in md_text
        assert "FP060BE-9999" in md_text
        assert "DURATION_MISMATCH" in md_text

        # JSON output to stdout
        rc_json = main(
            [
                "screen",
                str(spec_file),
                "--format",
                "json",
                "--data-dir",
                str(mock_data_dir),
            ]
        )
        assert rc_json == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["summary"]["total_occurrences"] == 2

        # CSV output to file (UTF-8 with BOM)
        out_csv = tmp_path / "report.csv"
        rc_csv = main(
            [
                "screen",
                str(spec_file),
                "--format",
                "csv",
                "--out",
                str(out_csv),
                "--data-dir",
                str(mock_data_dir),
            ]
        )
        assert rc_csv == 0
        assert out_csv.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_decode_multiple_and_no_db(self, capsys) -> None:
        rc = main(["decode", "FP060BE-9999", "NM-8585", "--no-db", "--json"])
        assert rc == 0
        items = json.loads(capsys.readouterr().out)
        assert isinstance(items, list) and len(items) == 2
        assert items[0]["decode"]["cert_no"] == "FP060BE-9999"
        assert items[1]["decode"]["cert_no"] == "NM-8585"

    def test_screen_missing_path_returns_nonzero(self, mock_data_dir: Path, capsys) -> None:
        rc = main(["screen", "/nonexistent/path/abc.txt", "--data-dir", str(mock_data_dir)])
        assert rc == 2
        assert "見つかりません" in capsys.readouterr().err

    def test_screen_stdin(self, mock_data_dir: Path, monkeypatch, capsys) -> None:
        import io

        monkeypatch.setattr(sys, "stdin", io.StringIO("不燃材料 NM-8585\n"))
        rc = main(["screen", "-", "--format", "json", "--data-dir", str(mock_data_dir)])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["summary"]["found_count"] == 1
        assert payload["occurrences"][0]["cert_no"] == "NM-8585"


class TestCliSubprocess:
    def test_tools_script_runs_directly(self, mock_data_dir: Path) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                str(_CLI_SCRIPT),
                "decode",
                "QF045BE-9999",
                "--json",
                "--data-dir",
                str(mock_data_dir),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["decode"]["cert_no"] == "QF045BE-9999"
        assert payload["status"] == "UNLISTED_VALID_FORMAT"
