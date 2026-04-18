from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import app as app_module
import command_writer
import execution_tracker
import state_manager
import telegram_bot as tg_module
from decision_engine import REASON_UNSUPPORTED_SIGNAL


class TvWebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = TemporaryDirectory()
        self.output_dir = Path(self.tmp_dir.name) / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        command_writer.OUTPUT_DIR = self.output_dir
        command_writer.ORDER_COMMAND_PATH = self.output_dir / "order_command.json"
        command_writer.ORDER_COMMAND_TMP_PATH = self.output_dir / "order_command.json.tmp"
        command_writer.SIGNAL_LOG_PATH = self.output_dir / "signal_log.jsonl"

        state_manager.OUTPUT_DIR = self.output_dir
        state_manager.STATE_PATH = self.output_dir / "state.json"
        state_manager.FILL_DEDUPE_PATH = self.output_dir / "fill_request_ids.json"

        execution_tracker.OUTPUT_DIR = self.output_dir
        execution_tracker.EXECUTION_EVENTS_PATH = self.output_dir / "execution_events.jsonl"
        execution_tracker.ORDERS_DIR = self.output_dir / "orders"

        self.notify_mock = Mock()
        app_module.notify_decision = self.notify_mock
        app_module.notify_fill_result = self.notify_mock

        self.secret = "tv-test-secret-xyz"
        self.env_patch = patch.dict(os.environ, {"TV_WEBHOOK_SECRET": self.secret}, clear=False)
        self.env_patch.start()

        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        self.env_patch.stop()
        app_module.notify_decision = tg_module.notify_decision
        app_module.notify_fill_result = tg_module.notify_fill_result
        self.tmp_dir.cleanup()

    def _tv_body(self, **overrides: object) -> dict:
        base: dict = {
            "secret": self.secret,
            "symbol": "MNQ",
            "signal": "long_breakout",
            "price": 20150.0,
            "breakout_level": 20145.0,
            "delta_strength": 0.88,
            "bias": "bullish",
            "levels": {"r1": 20300.0, "s1": 20000.0},
        }
        base.update(overrides)
        return base

    def test_valid_secret_and_payload_returns_trace(self) -> None:
        resp = self.client.post("/tv-webhook", json=self._tv_body())
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        assert body is not None
        self.assertTrue(body.get("ok"))
        self.assertIn(body.get("decision"), ("CHASE", "RETEST", "SKIP"))
        self.assertIsInstance(body.get("reason_code"), str)
        self.assertGreater(len(body.get("reason_code", "")), 0)
        self.assertRegex(str(body.get("request_id")), r"^\d{8}T\d{6}_[0-9a-f]{6}$")
        tr = body.get("trace")
        self.assertIsInstance(tr, dict)

        lines = command_writer.SIGNAL_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
        tv_ev = None
        for line in lines:
            rec = json.loads(line)
            if rec.get("event_type") == "tv_webhook_received":
                tv_ev = rec
                break
        self.assertIsNotNone(tv_ev)
        assert tv_ev is not None
        self.assertNotIn("secret", tv_ev.get("tv_payload_sanitized", {}))
        self.assertEqual(tv_ev.get("tv_payload_sanitized", {}).get("signal"), "long_breakout")
        self.assertIn("adapted_internal_payload", tv_ev)

    def test_external_request_id_not_used(self) -> None:
        resp = self.client.post(
            "/tv-webhook",
            json=self._tv_body(request_id="client-should-not-win-99999"),
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        assert body is not None
        rid = str(body.get("request_id"))
        self.assertRegex(rid, r"^\d{8}T\d{6}_[0-9a-f]{6}$")
        self.assertNotEqual(rid, "client-should-not-win-99999")

    def test_invalid_secret_403(self) -> None:
        resp = self.client.post("/tv-webhook", json={**self._tv_body(), "secret": "wrong"})
        self.assertEqual(resp.status_code, 403)
        body = resp.get_json()
        assert body is not None
        self.assertFalse(body.get("ok"))
        self.assertEqual(body.get("error"), "invalid_secret")

    def test_missing_field_400(self) -> None:
        b = self._tv_body()
        del b["price"]
        resp = self.client.post("/tv-webhook", json=b)
        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        assert body is not None
        self.assertFalse(body.get("ok"))
        self.assertEqual(body.get("error"), "bad_payload")

    def test_missing_or_empty_symbol_400(self) -> None:
        b = self._tv_body()
        del b["symbol"]
        r1 = self.client.post("/tv-webhook", json=b)
        self.assertEqual(r1.status_code, 400)

        r2 = self.client.post("/tv-webhook", json={**self._tv_body(), "symbol": ""})
        self.assertEqual(r2.status_code, 400)

        r3 = self.client.post("/tv-webhook", json={**self._tv_body(), "symbol": "   "})
        self.assertEqual(r3.status_code, 400)

    def test_symbol_unknown_string_allowed(self) -> None:
        resp = self.client.post("/tv-webhook", json={**self._tv_body(), "symbol": "UNKNOWN"})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        assert body is not None
        self.assertTrue(body.get("ok"))

    def test_adapter_trace_inputs_mapping(self) -> None:
        resp = self.client.post(
            "/tv-webhook",
            json=self._tv_body(delta_strength=0.88, price=20150.0, breakout_level=20140.0),
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        assert body is not None
        tr = body.get("trace")
        assert isinstance(tr, dict)
        ins = tr.get("inputs")
        assert isinstance(ins, dict)
        self.assertAlmostEqual(float(ins.get("delta_strength")), 0.88)
        self.assertAlmostEqual(float(ins.get("extension_points")), 10.0)

    def test_unknown_signal_reaches_engine_unsupported_signal(self) -> None:
        resp = self.client.post(
            "/tv-webhook",
            json=self._tv_body(signal="weird_unknown_signal_xyz", delta_strength=0.95),
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        assert body is not None
        self.assertEqual(body.get("decision"), "SKIP")
        self.assertEqual(body.get("reason_code"), REASON_UNSUPPORTED_SIGNAL)
        tr = body.get("trace")
        assert isinstance(tr, dict)
        self.assertEqual(tr.get("reason_code"), REASON_UNSUPPORTED_SIGNAL)

    def test_process_webhook_payload_error_returns_500(self) -> None:
        def boom(*_a, **_k):
            raise RuntimeError("simulated failure")

        with patch.object(app_module, "process_webhook_payload", side_effect=boom):
            resp = self.client.post("/tv-webhook", json=self._tv_body())
        self.assertEqual(resp.status_code, 500)
        body = resp.get_json()
        assert body is not None
        self.assertFalse(body.get("ok"))
        self.assertEqual(body.get("error"), "internal_error")
        self.assertNotIn("traceback", json.dumps(body).lower())


if __name__ == "__main__":
    unittest.main()
