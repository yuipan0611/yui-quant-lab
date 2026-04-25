from __future__ import annotations

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
import webhook_dedupe as webhook_dedupe_module
from engine_types import DecisionSignal, GateContext
from risk_engine import build_trade_intent
from state_gate import evaluate_gate


class EngineV2FlowTests(unittest.TestCase):
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

        self.secret = "tv-v2-secret"
        self.dedupe_path = self.output_dir / "webhook_dedupe.json"
        self.env_patch = patch.dict(
            os.environ,
            {
                "TV_WEBHOOK_SECRET": self.secret,
                "WEBHOOK_DEDUPE_PATH": str(self.dedupe_path),
                "ENGINE_V2_ENABLED": "true",
            },
            clear=False,
        )
        self.env_patch.start()
        webhook_dedupe_module.get_dedupe_path.cache_clear()
        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        self.env_patch.stop()
        webhook_dedupe_module.get_dedupe_path.cache_clear()
        app_module.notify_decision = tg_module.notify_decision
        app_module.notify_fill_result = tg_module.notify_fill_result
        self.tmp_dir.cleanup()

    @staticmethod
    def _webhook_payload() -> dict:
        return {
            "symbol": "MNQ",
            "signal": "long_breakout",
            "price": 20150.0,
            "breakout_level": 20145.0,
            "delta_strength": 0.92,
            "bias": "bullish",
            "levels": {"r1": 20300.0, "s1": 20000.0},
        }

    def test_webhook_v2_happy_path_writes_command(self) -> None:
        resp = self.client.post("/webhook", json=self._webhook_payload())
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        assert isinstance(body, dict)
        self.assertEqual(body.get("status"), "success")
        self.assertIn(body.get("decision"), ("CHASE", "RETEST", "SKIP"))
        self.assertIn("gate_result", body)
        self.assertTrue(command_writer.SIGNAL_LOG_PATH.is_file())
        if body.get("decision") in ("CHASE", "RETEST"):
            self.assertTrue(command_writer.ORDER_COMMAND_PATH.is_file())
            cmd = command_writer.read_order_command()
            assert isinstance(cmd, dict)
            self.assertIn("risk", cmd)

    def test_tv_webhook_v2_returns_compat_response(self) -> None:
        payload = {**self._webhook_payload(), "secret": self.secret}
        resp = self.client.post("/tv-webhook", json=payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        assert isinstance(body, dict)
        self.assertTrue(body.get("ok"))
        self.assertIn(body.get("decision"), ("CHASE", "RETEST", "SKIP"))
        self.assertIn("reason_code", body)

    def test_state_gate_loss_streak_block(self) -> None:
        with patch.dict(os.environ, {"STATE_GATE_MAX_LOSS_STREAK": "2"}, clear=False):
            gate = evaluate_gate(
                GateContext(
                    trace_id="t1",
                    event_id="e1",
                    payload=self._webhook_payload(),
                    decision_signal=DecisionSignal(
                        trace_id="t1",
                        event_id="e1",
                        decision="CHASE",
                        reason="ok",
                        trace={},
                        plan={"reference_levels": {"price": 20150.0}},
                        market_payload={},
                    ),
                    state={"consecutive_loss": 2, "regime": "normal"},
                    endpoint="/webhook",
                )
            )
        self.assertFalse(gate.allow)
        self.assertEqual(gate.reason_code, "LOSS_STREAK_BLOCKED")

    def test_risk_engine_daily_loss_guard_sets_zero_risk(self) -> None:
        intent = build_trade_intent(
            request_id="rid1",
            trace_id="rid1",
            payload=self._webhook_payload(),
            decision_signal=DecisionSignal(
                trace_id="rid1",
                event_id="rid1",
                decision="CHASE",
                reason="chase_ok",
                trace={},
                plan={"reference_levels": {"price": 20150.0}},
                market_payload={},
            ),
            state={"today_realized_pnl": -500.0, "regime": "normal"},
        )
        self.assertEqual(intent.max_risk, 0.0)
        self.assertEqual(intent.position_size, 0.0)
        self.assertTrue(intent.daily_loss_protection["blocked"])

    def test_v1_compat_when_flag_off(self) -> None:
        with patch.dict(os.environ, {"ENGINE_V2_ENABLED": "false"}, clear=False):
            resp = self.client.post("/webhook", json=self._webhook_payload())
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        assert isinstance(body, dict)
        self.assertEqual(body.get("status"), "success")
        self.assertIn("command_write", body)


if __name__ == "__main__":
    unittest.main()

