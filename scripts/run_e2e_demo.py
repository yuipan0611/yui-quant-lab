#!/usr/bin/env python3
"""執行端到端 demo（stdout 摘要）。專案根目錄：python scripts/run_e2e_demo.py"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from e2e_full_flow import main_cli  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main_cli(sys.argv[1:]))
