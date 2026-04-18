"""replay.py 與 fixtures 煙霧測試（不重跑真 Telegram）。"""

from __future__ import annotations

import subprocess
import sys
import unittest
from collections import Counter
from pathlib import Path

import replay
from decision_engine import REASON_HIGH_VOL_DOWNGRADE, REASON_STATE_GATE


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "fixtures"


class ReplaySmokeTests(unittest.TestCase):
    def test_replay_single_fixture_chase_clean(self) -> None:
        path = FIXTURES_DIR / "chase_clean.json"
        self.assertTrue(path.is_file(), f"missing {path}")
        r = replay.replay_one_fixture(path, verbose=False, fill_cooldown_minutes=None)
        self.assertEqual(r["http_status"], 200)
        self.assertEqual(r["decision"], "CHASE")
        self.assertEqual(r["reason_code"], "CHASE_OK")
        self.assertTrue(r["order_command_written"])
        self.assertEqual(r["notify_decision_count"], 1)
        self.assertEqual(r["notify_fill_count"], 2)
        self.assertFalse(r["fill_duplicate_applied"])

    def test_replay_folder_batch_all_json(self) -> None:
        files = sorted(p for p in FIXTURES_DIR.glob("*.json") if p.is_file())
        self.assertGreaterEqual(len(files), 5)
        results = [replay.replay_one_fixture(p, verbose=False, fill_cooldown_minutes=None) for p in files]
        self.assertEqual(len(results), len(files))
        for r in results:
            self.assertEqual(r["http_status"], 200)
        rc = Counter(str(x.get("reason_code") or "UNKNOWN") for x in results)
        self.assertIn("CHASE_OK", rc)
        self.assertIn("EXTENSION_TOO_LARGE", rc)

    def test_fixture_high_vol_downgrade_reason_code(self) -> None:
        path = FIXTURES_DIR / "high_vol_downgrade.json"
        r = replay.replay_one_fixture(path, verbose=False, fill_cooldown_minutes=None)
        self.assertEqual(r["reason_code"], REASON_HIGH_VOL_DOWNGRADE)
        self.assertEqual(r["decision"], "RETEST")
        self.assertEqual(r["downgraded_from"], "CHASE")
        self.assertEqual(r["inputs_regime"], "high_vol")

    def test_fixture_state_gate_skip_reason_code(self) -> None:
        path = FIXTURES_DIR / "state_gate_skip.json"
        r = replay.replay_one_fixture(path, verbose=False, fill_cooldown_minutes=None)
        self.assertEqual(r["reason_code"], REASON_STATE_GATE)
        self.assertEqual(r["decision"], "SKIP")
        self.assertEqual(r["branch"], "NONE")

    def test_replay_cli_single_file_exit_zero(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "replay.py"),
                str(FIXTURES_DIR / "chase_clean.json"),
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("CHASE", proc.stdout)
        self.assertIn("chase_clean.json", proc.stdout)

    def test_replay_cli_fixtures_dir_exit_zero_with_summary(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "replay.py"), str(FIXTURES_DIR)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Summary", proc.stdout)
        self.assertIn("total cases:", proc.stdout)
        self.assertIn("reason_code counts:", proc.stdout)

    def test_replay_verbose_includes_trace_and_state_keys(self) -> None:
        path = FIXTURES_DIR / "skip_low_delta.json"
        r = replay.replay_one_fixture(path, verbose=True, fill_cooldown_minutes=None)
        vb = r.get("_verbose")
        self.assertIsInstance(vb, dict)
        assert isinstance(vb, dict)
        self.assertIn("trace_full", vb)
        self.assertIn("last_decision_result", vb)
        self.assertIn("state_final", vb)


if __name__ == "__main__":
    unittest.main()
