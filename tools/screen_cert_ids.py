#!/usr/bin/env python3
"""CLI entry point for decoding, searching, and screening MLIT Minister Certification numbers.

Usage examples:
    python tools/screen_cert_ids.py decode QF045BE-1232
    python tools/screen_cert_ids.py search --applicant 吉野石膏 --prefix FP060NE
    python tools/screen_cert_ids.py screen ./specs/ --format md --out report.md
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repository root is importable when invoked directly as a script
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from daijin_nintei.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
