from __future__ import annotations

import json
import os
import re
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import app as app_module
import command_writer
import execution_tracker
import state_manager
import telegram_bot as tg_module
from state_manager import _default_state
from time_utils import now_taipei


class WebhookStateMVPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = TemporaryDirectory()
        self.output_dir = Path(self.tmp_dir.name) / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Redirect runtime file outputs.
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
        self.fill_notify_mock = Mock()
        app_module.notify_decision = self.notify_mock
        app_module.notify_fill_result = self.fill_notify_mock

        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        app_module.notify_decision = tg_module.notify_decision
        app_module.notify_fill_result = tg_module.notify_fill_result
        self.tmp_dir.cleanup()

    def _payload(self) -> dict:
        return {
            "symbol": "MNQ",
            "signal": "long_breakout",
            "price": 20150.0,
            "breakout_level": 20145.0,
            "delta_strength": 0.92,
            "bias": "bullish",
            "levels": {"r1": 20300.0, "s1": 20000.0},
        }

    def test_a_normal_webhook_pipeline(self) -> None:
        resp = self.client.post("/webhook", json=self._payload())
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["status"], "success")
        self.assertIn(body["decision"], ("CHASE", "RETEST", "SKIP"))
        self.assertRegex(body["request_id"], r"^\d{8}T\d{6}_[0-9a-f]{6}$")

        self.assertTrue(command_writer.SIGNAL_LOG_PATH.exists())
        lines = command_writer.SIGNAL_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
        self.assertGreaterEqual(len(lines), 2)
        last = json.loads(lines[-1])
        self.assertIn("state_snapshot_before", last)
        self.assertIn("state_snapshot_after", last)

        if body["decision"] in ("CHASE", "RETEST"):
            self.assertTrue(command_writer.ORDER_COMMAND_PATH.exists())
            cmd = json.loads(command_writer.ORDER_COMMAND_PATH.read_text(encoding="utf-8"))
            self.assertIn("request_id", cmd)
            self.assertIn("reason", cmd)
            self.assertIn("+08:00", cmd["timestamp"])
            order_path = execution_tracker.ORDERS_DIR / f"{cmd['request_id']}.json"
            self.assertTrue(order_path.is_file())

        self.assertTrue(state_manager.STATE_PATH.exists())
        self.notify_mock.assert_called_once()
        self.assertIn("trace", body)
        tr = body.get("trace")
        self.assertIsInstance(tr, dict)
        assert isinstance(tr, dict)
        self.assertEqual(tr.get("decision"), body.get("decision"))
        rc = tr.get("reason_code")
        self.assertIsInstance(rc, str)
        self.assertGreater(len(rc), 0)
        last = json.loads(lines[-1])
        self.assertEqual(last.get("event_type"), "decision_result")
        self.assertIn("trace", last)
        self.assertIsInstance(last.get("trace"), dict)

    def test_b_cooldown_gate_skip(self) -> None:
        s = _default_state()
        s["cooldown_until"] = (now_taipei() + timedelta(minutes=10)).isoformat(timespec="seconds")
        state_manager.save_state(s)

        resp = self.client.post("/webhook", json=self._payload())
        body = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body["decision"], "SKIP")
        self.assertEqual(body["reason"], "cooldown_active")
        tr = body.get("trace")
        self.assertIsInstance(tr, dict)
        assert isinstance(tr, dict)
        self.assertEqual(tr.get("reason_code"), "STATE_GATE")

    def test_notify_decision_send_failure_does_not_break_webhook(self) -> None:
        state_manager.save_state(_default_state())
        app_module.notify_decision = tg_module.notify_decision
        with patch.dict(
            os.environ,
            {
                "ENABLE_TELEGRAM_NOTIFY": "true",
                "TELEGRAM_BOT_TOKEN": "fake-token",
                "TELEGRAM_CHAT_ID": "4242",
            },
            clear=False,
        ):
            with patch.object(
                tg_module,
                "_send_message",
                return_value={"ok": False, "status_code": 400, "error": "Bad Request"},
            ):
                resp = self.client.post("/webhook", json=self._payload())
        app_module.notify_decision = self.notify_mock
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        assert body is not None
        self.assertEqual(body["status"], "success")
        self.assertIn(body["decision"], ("CHASE", "RETEST", "SKIP"))

    def test_c_cross_day_reset(self) -> None:
        s = _default_state()
        s["trading_day"] = "1999-01-01"
        s["today_realized_pnl"] = -350.0
        s["today_loss"] = -350.0
        s["consecutive_loss"] = 4
        s["cooldown_until"] = (now_taipei() + timedelta(minutes=30)).isoformat(timespec="seconds")
        s["daily_trade_count"] = 9
        state_manager.save_state(s)

        resp = self.client.post("/webhook", json=self._payload())
        self.assertEqual(resp.status_code, 200)

        new_state = state_manager.load_state()
        self.assertEqual(new_state["trading_day"], now_taipei().date().isoformat())
        self.assertEqual(new_state["today_realized_pnl"], 0.0)
        self.assertEqual(new_state["today_loss"], 0.0)
        self.assertEqual(new_state["consecutive_loss"], 0)
        self.assertIsNone(new_state["cooldown_until"])
        self.assertLessEqual(new_state["daily_trade_count"], 1)

    def test_d_corrupt_state_recovery(self) -> None:
        state_manager.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        state_manager.STATE_PATH.write_text("{bad_json", encoding="utf-8")

        resp = self.client.post("/webhook", json=self._payload())
        self.assertEqual(resp.status_code, 200)

        self.assertTrue(state_manager.STATE_PATH.exists())
        corrupt_files = list(self.output_dir.glob("state.json.corrupt.*"))
        self.assertGreaterEqual(len(corrupt_files), 1)

    def test_e_fill_result_updates_state(self) -> None:
        s = _default_state()
        state_manager.save_state(s)

        req = "20260419T103012_ab12cd"
        resp = self.client.post(
            "/fill-result",
            json={"request_id": req, "pnl": -120.5, "cooldown_minutes": 15, "regime": "high_vol"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["request_id"], req)

        new_state = state_manager.load_state()
        self.assertAlmostEqual(new_state["today_realized_pnl"], -120.5)
        self.assertAlmostEqual(new_state["today_loss"], -120.5)
        self.assertEqual(new_state["consecutive_loss"], 1)
        self.assertEqual(new_state["regime"], "high_vol")
        self.assertIsNotNone(new_state["cooldown_until"])

    def test_f_fill_duplicate_ignored(self) -> None:
        s = _default_state()
        s["consecutive_loss"] = 2
        state_manager.save_state(s)
        req = "dup_fill_01"
        body = {"request_id": req, "pnl": -50.0}
        r1 = self.client.post("/fill-result", json=body)
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.get_json()["applied"])
        st1 = state_manager.load_state()
        self.assertAlmostEqual(st1["today_realized_pnl"], -50.0)
        self.assertEqual(st1["consecutive_loss"], 3)

        r2 = self.client.post("/fill-result", json=body)
        self.assertEqual(r2.status_code, 200)
        self.assertFalse(r2.get_json()["applied"])
        self.assertEqual(r2.get_json().get("reason"), "duplicate_fill")
        st2 = state_manager.load_state()
        self.assertAlmostEqual(st2["today_realized_pnl"], -50.0)
        self.assertEqual(st2["consecutive_loss"], 3)

    def test_g_pnl_zero_keeps_consecutive_loss(self) -> None:
        s = _default_state()
        s["consecutive_loss"] = 2
        state_manager.save_state(s)
        req = "pnl_zero_01"
        resp = self.client.post("/fill-result", json={"request_id": req, "pnl": 0.0})
        self.assertEqual(resp.status_code, 200)
        st = state_manager.load_state()
        self.assertAlmostEqual(st["today_realized_pnl"], 0.0)
        self.assertEqual(st["consecutive_loss"], 2)

    def test_h_order_event_and_fill_link_by_broker_id(self) -> None:
        execution_tracker.create_order_record(
            request_id="ord_1",
            symbol="MNQ",
            decision="CHASE",
            client_order_id="cli_9",
        )
        r_ack = self.client.post(
            "/order-event",
            json={
                "request_id": "ord_1",
                "broker": "single_broker",
                "event_type": "order_acknowledged",
                "broker_order_id": "BRK-1",
            },
        )
        self.assertEqual(r_ack.status_code, 200)
        rec = execution_tracker.load_order_record("ord_1")
        self.assertEqual(rec["command_status"], "acknowledged")
        self.assertEqual(rec["broker_order_id"], "BRK-1")

        r_fill = self.client.post(
            "/fill-result",
            json={
                "pnl": -10.0,
                "broker": "single_broker",
                "broker_order_id": "BRK-1",
                "fill_id": "f1",
                "filled_qty": 1,
            },
        )
        self.assertEqual(r_fill.status_code, 200)
        rec2 = execution_tracker.load_order_record("ord_1")
        self.assertEqual(rec2["fill_status"], "filled")
        self.assertEqual(rec2["command_status"], "filled")

    def test_i_multi_broker_same_request_id(self) -> None:
        rid = "ord_multi"
        execution_tracker.create_order_record(
            request_id=rid,
            symbol="MNQ",
            decision="CHASE",
            broker="broker_A",
            client_order_id="cA",
        )
        execution_tracker.create_order_record(
            request_id=rid,
            symbol="MNQ",
            decision="CHASE",
            broker="broker_B",
            client_order_id="cB",
        )
        self.client.post(
            "/order-event",
            json={
                "request_id": rid,
                "broker": "broker_A",
                "event_type": "order_acknowledged",
                "broker_order_id": "A-1",
            },
        )
        self.client.post(
            "/order-event",
            json={
                "request_id": rid,
                "broker": "broker_B",
                "event_type": "order_acknowledged",
                "broker_order_id": "B-9",
            },
        )
        self.client.post(
            "/fill-result",
            json={"pnl": -1.0, "broker": "broker_A", "broker_order_id": "A-1", "fill_id": "fa1"},
        )
        self.client.post(
            "/fill-result",
            json={"pnl": 2.0, "broker": "broker_B", "broker_order_id": "B-9", "fill_id": "fb1"},
        )
        rec = execution_tracker.load_order_record(rid)
        self.assertIn("broker_A", rec["brokers"])
        self.assertIn("broker_B", rec["brokers"])
        self.assertEqual(rec["brokers"]["broker_A"]["broker_order_id"], "A-1")
        self.assertEqual(rec["brokers"]["broker_B"]["broker_order_id"], "B-9")
        self.assertEqual(rec["brokers"]["broker_A"]["command_status"], "filled")
        self.assertEqual(rec["brokers"]["broker_B"]["command_status"], "filled")


if __name__ == "__main__":
    unittest.main()
