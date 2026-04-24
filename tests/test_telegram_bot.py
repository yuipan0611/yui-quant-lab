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
import telegram_bot as tg


class TailJsonlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / "log.jsonl"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(
            tg.tail_jsonl_find_last(self.path, lambda o: o.get("t") == 1, max_lines=100)
        )

    def test_finds_last_matching(self) -> None:
        self.path.write_text(
            '{"t":0}\n'
            '{"t":1,"id":1}\n'
            'not json\n'
            '{"t":1,"id":2}\n'
            '{"t":2}\n',
            encoding="utf-8",
        )
        found = tg.tail_jsonl_find_last(self.path, lambda o: o.get("t") == 1, max_lines=10)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.get("id"), 2)

    def test_respects_max_lines(self) -> None:
        lines = "\n".join(json.dumps({"event_type": "x", "i": i}) for i in range(25))
        self.path.write_text(lines + "\n", encoding="utf-8")
        found = tg.tail_jsonl_find_last(
            self.path,
            lambda o: o.get("event_type") == "x" and int(o.get("i", -1)) < 10,
            max_lines=5,
        )
        self.assertIsNone(found)


class ExtractUpdateFieldsTests(unittest.TestCase):
    def test_message_text(self) -> None:
        u = {"message": {"chat": {"id": 42}, "text": "/state"}}
        f = tg.extract_update_fields(u)
        self.assertEqual(f["update_type"], "message")
        self.assertEqual(f["chat_id"], 42)
        self.assertEqual(f["text"], "/state")

    def test_callback_no_plain_text(self) -> None:
        u = {"callback_query": {"id": "cbq_1", "message": {"chat": {"id": 7}}, "data": "noop"}}
        f = tg.extract_update_fields(u)
        self.assertEqual(f["update_type"], "callback_query")
        self.assertEqual(f["chat_id"], 7)
        self.assertEqual(f["text"], "noop")
        self.assertEqual(f["callback_query_id"], "cbq_1")

    def test_empty_update(self) -> None:
        f = tg.extract_update_fields({})
        self.assertIsNone(f["update_type"])
        self.assertIsNone(f["chat_id"])


class SendMessageResultTests(unittest.TestCase):
    def test_missing_token_shape(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ["TELEGRAM_CHAT_ID"] = "1"
            r = tg._send_message("hi")
        self.assertFalse(r["ok"])
        self.assertIsNone(r["status_code"])
        self.assertEqual(r["error"], "missing_token")


class TelegramWebhookIntegrationTests(unittest.TestCase):
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
        tg.MANUAL_TRADE_LOCK_PATH = self.output_dir / "manual_trade_lock.json"

        self.client = app_module.app.test_client()
        self._env_patch = patch.dict(
            os.environ,
            {
                "TELEGRAM_CHAT_ID": "4242",
                "TELEGRAM_WEBHOOK_SECRET": "",
            },
            clear=False,
        )
        self._env_patch.start()
        os.environ.pop("TELEGRAM_WEBHOOK_SECRET", None)

    def tearDown(self) -> None:
        self._env_patch.stop()
        self.tmp_dir.cleanup()

    def test_webhook_secret_rejects(self) -> None:
        os.environ["TELEGRAM_WEBHOOK_SECRET"] = "sec"
        resp = self.client.post(
            "/telegram/webhook",
            json={"message": {"chat": {"id": 4242}, "text": "/help"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_state_command_sends_reply(self) -> None:
        state_manager.save_state(state_manager._default_state())
        os.environ.pop("TELEGRAM_WEBHOOK_SECRET", None)
        with patch.object(tg, "_send_message", return_value={"ok": True, "status_code": 200, "error": None}) as sm:
            resp = self.client.post(
                "/telegram/webhook",
                json={"message": {"chat": {"id": 4242}, "text": "/state"}},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["ok"])
        sm.assert_called_once()
        args, kwargs = sm.call_args
        self.assertIn("[State]", args[0])

    def test_start_command_sends_menu(self) -> None:
        with patch.object(
            tg,
            "_send_message",
            return_value={"ok": True, "status_code": 200, "error": None},
        ) as sm:
            resp = self.client.post(
                "/telegram/webhook",
                json={"message": {"chat": {"id": 4242}, "text": "/start"}},
            )
        self.assertEqual(resp.status_code, 200)
        sm.assert_called_once()
        _, kwargs = sm.call_args
        self.assertIn("reply_markup", kwargs)
        self.assertIn("inline_keyboard", kwargs["reply_markup"])

    def test_help_command_keeps_help_and_menu(self) -> None:
        with patch.object(
            tg,
            "_send_message",
            return_value={"ok": True, "status_code": 200, "error": None},
        ) as sm:
            resp = self.client.post(
                "/telegram/webhook",
                json={"message": {"chat": {"id": 4242}, "text": "/help"}},
            )
        self.assertEqual(resp.status_code, 200)
        args, kwargs = sm.call_args
        self.assertIn("[Help]", args[0])
        self.assertIn("reply_markup", kwargs)

    def test_callback_status_replies_state_and_answers_callback(self) -> None:
        state_manager.save_state(state_manager._default_state())
        with patch.object(
            tg,
            "_send_message",
            return_value={"ok": True, "status_code": 200, "error": None},
        ) as sm, patch.object(
            tg,
            "_answer_callback_query",
            return_value={"ok": True, "status_code": 200, "error": None},
        ) as ack:
            resp = self.client.post(
                "/telegram/webhook",
                json={
                    "callback_query": {
                        "id": "cbq_status_1",
                        "message": {"chat": {"id": 4242}},
                        "data": "status",
                    }
                },
            )
        self.assertEqual(resp.status_code, 200)
        args, _ = sm.call_args
        self.assertIn("[State]", args[0])
        ack.assert_called_once_with("cbq_status_1")

    def test_callback_answer_failure_does_not_break_webhook_flow(self) -> None:
        state_manager.save_state(state_manager._default_state())
        with patch.object(
            tg,
            "_send_message",
            return_value={"ok": True, "status_code": 200, "error": None},
        ) as sm, patch.object(
            tg,
            "_answer_callback_query",
            return_value={"ok": False, "status_code": 500, "error": "mock error"},
        ) as ack:
            resp = self.client.post(
                "/telegram/webhook",
                json={
                    "callback_query": {
                        "id": "cbq_status_fail_1",
                        "message": {"chat": {"id": 4242}},
                        "data": "status",
                    }
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["ok"])
        sm.assert_called_once()
        args, _ = sm.call_args
        self.assertIn("[State]", args[0])
        ack.assert_called_once_with("cbq_status_fail_1")

    def test_callback_lock_trading_writes_manual_lock_file(self) -> None:
        with patch.object(
            tg,
            "_send_message",
            return_value={"ok": True, "status_code": 200, "error": None},
        ), patch.object(
            tg,
            "_answer_callback_query",
            return_value={"ok": True, "status_code": 200, "error": None},
        ):
            resp = self.client.post(
                "/telegram/webhook",
                json={
                    "callback_query": {
                        "id": "cbq_lock_1",
                        "message": {"chat": {"id": 4242}},
                        "data": "lock_trading",
                    }
                },
            )
        self.assertEqual(resp.status_code, 200)
        payload = json.loads(tg.MANUAL_TRADE_LOCK_PATH.read_text(encoding="utf-8"))
        self.assertTrue(payload["locked"])
        self.assertEqual(payload["source"], "telegram")
        self.assertEqual(payload["reason"], "manual_lock")
        self.assertIn("updated_at", payload)

    def test_callback_unlock_trading_writes_manual_lock_file(self) -> None:
        tg.save_manual_trade_lock(True, "manual_lock")
        with patch.object(
            tg,
            "_send_message",
            return_value={"ok": True, "status_code": 200, "error": None},
        ), patch.object(
            tg,
            "_answer_callback_query",
            return_value={"ok": True, "status_code": 200, "error": None},
        ):
            resp = self.client.post(
                "/telegram/webhook",
                json={
                    "callback_query": {
                        "id": "cbq_unlock_1",
                        "message": {"chat": {"id": 4242}},
                        "data": "unlock_trading",
                    }
                },
            )
        self.assertEqual(resp.status_code, 200)
        payload = json.loads(tg.MANUAL_TRADE_LOCK_PATH.read_text(encoding="utf-8"))
        self.assertFalse(payload["locked"])
        self.assertEqual(payload["source"], "telegram")
        self.assertEqual(payload["reason"], "manual_unlock")

    def test_callback_unknown_does_not_crash(self) -> None:
        with patch.object(
            tg,
            "_send_message",
            return_value={"ok": True, "status_code": 200, "error": None},
        ) as sm, patch.object(
            tg,
            "_answer_callback_query",
            return_value={"ok": True, "status_code": 200, "error": None},
        ):
            resp = self.client.post(
                "/telegram/webhook",
                json={
                    "callback_query": {
                        "id": "cbq_unknown_1",
                        "message": {"chat": {"id": 4242}},
                        "data": "unknown_action",
                    }
                },
            )
        self.assertEqual(resp.status_code, 200)
        args, _ = sm.call_args
        self.assertIn("未知操作", args[0])

    def test_unauthorized_chat_is_silent_ok(self) -> None:
        with patch.object(tg, "_send_message") as sm:
            resp = self.client.post(
                "/telegram/webhook",
                json={"message": {"chat": {"id": 1}, "text": "/state"}},
            )
        self.assertEqual(resp.status_code, 200)
        sm.assert_not_called()


class NotifyDecisionModeTests(unittest.TestCase):
    def _summary(self) -> dict:
        return {
            "request_id": "20260419T120000_abc123",
            "symbol": "MNQ",
            "signal": "long_breakout",
            "decision": "CHASE",
            "reason_code": "OK_CHASE",
            "trace": {
                "branch": "LONG",
                "inputs": {
                    "delta_strength": 0.88,
                    "extension_points": 10.0,
                    "regime": "range",
                },
            },
            "regime": "range",
        }

    def test_format_decision_message_multiline_plain_text(self) -> None:
        msg = tg.format_decision_message(self._summary())
        self.assertIn("\n\n", msg)
        self.assertIn("symbol: MNQ", msg)
        self.assertIn("reason_code: OK_CHASE", msg)
        self.assertIn("branch: LONG", msg)
        self.assertIn("delta_strength: 0.88", msg)
        self.assertIn("extension_points: 10.0", msg)
        self.assertIn("regime: range", msg)

    def test_notify_mode_print_when_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_TELEGRAM_NOTIFY", None)
            with patch("builtins.print"):
                r = tg.notify_decision(self._summary())
        self.assertEqual(r.get("mode"), "print")
        self.assertTrue(r.get("ok"))

    def test_notify_mode_disabled_when_false(self) -> None:
        with patch.dict(os.environ, {"ENABLE_TELEGRAM_NOTIFY": "false"}, clear=False):
            with patch("builtins.print"):
                r = tg.notify_decision(self._summary())
        self.assertEqual(r.get("mode"), "disabled")
        self.assertTrue(r.get("ok"))

    def test_notify_mode_missing_credentials(self) -> None:
        with patch.dict(os.environ, {"ENABLE_TELEGRAM_NOTIFY": "true"}, clear=False):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ.pop("TELEGRAM_CHAT_ID", None)
            with patch("builtins.print"):
                r = tg.notify_decision(self._summary())
        self.assertEqual(r.get("mode"), "missing_credentials")
        self.assertTrue(r.get("ok"))

    def test_notify_mode_telegram_success(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ENABLE_TELEGRAM_NOTIFY": "true",
                "TELEGRAM_BOT_TOKEN": "t",
                "TELEGRAM_CHAT_ID": "1",
            },
            clear=False,
        ):
            with patch.object(
                tg,
                "_send_message",
                return_value={"ok": True, "status_code": 200, "error": None},
            ):
                r = tg.notify_decision(self._summary())
        self.assertEqual(r.get("mode"), "telegram")
        self.assertTrue(r.get("ok"))

    def test_notify_mode_telegram_send_failed_still_telegram_mode(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ENABLE_TELEGRAM_NOTIFY": "true",
                "TELEGRAM_BOT_TOKEN": "t",
                "TELEGRAM_CHAT_ID": "1",
            },
            clear=False,
        ):
            with patch.object(
                tg,
                "_send_message",
                return_value={"ok": False, "status_code": 400, "error": "x"},
            ):
                with patch("builtins.print"):
                    r = tg.notify_decision(self._summary())
        self.assertEqual(r.get("mode"), "telegram")
        self.assertFalse(r.get("ok"))


class FillNotifyHookTests(unittest.TestCase):
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

        self.fill_mock = Mock(return_value={"ok": True, "status_code": None, "error": None})
        app_module.notify_fill_result = self.fill_mock

        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        app_module.notify_fill_result = tg.notify_fill_result
        self.tmp_dir.cleanup()

    def test_fill_notifies_applied_true_then_duplicate_false(self) -> None:
        state_manager.save_state(state_manager._default_state())
        req = "dup_fill_notify_01"
        r1 = self.client.post("/fill-result", json={"request_id": req, "pnl": -1.0})
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.get_json()["applied"])
        r2 = self.client.post("/fill-result", json={"request_id": req, "pnl": -1.0})
        self.assertEqual(r2.status_code, 200)
        self.assertFalse(r2.get_json()["applied"])
        self.assertEqual(self.fill_mock.call_count, 2)
        first = self.fill_mock.call_args_list[0][0][0]
        second = self.fill_mock.call_args_list[1][0][0]
        self.assertTrue(first["applied"])
        self.assertFalse(first.get("dedupe"))
        self.assertFalse(second["applied"])
        self.assertTrue(second.get("dedupe"))


class LastFormatterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.log_path = Path(self.tmp.name) / "signal_log.jsonl"
        self._orig_signal_log_path = tg.SIGNAL_LOG_PATH
        tg.SIGNAL_LOG_PATH = self.log_path

    def tearDown(self) -> None:
        tg.SIGNAL_LOG_PATH = self._orig_signal_log_path
        self.tmp.cleanup()

    def _write_events(self, events: list[dict]) -> None:
        lines = "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n"
        self.log_path.write_text(lines, encoding="utf-8")

    def test_last_decision_message_is_short_and_without_payload_dump(self) -> None:
        self._write_events(
            [
                {
                    "event_type": "decision_result",
                    "request_id": "req_1",
                    "timestamp": "2026-04-25T01:00:00+08:00",
                    "decision": "CHASE",
                    "reason": "chase_ok",
                    "raw_payload": {"signal": "long_breakout", "symbol": "MNQ", "price": 20150},
                    "trace": {"inputs": {"regime": "range"}},
                }
            ]
        )
        msg = tg._format_last_event_by_type("decision_result", "LastDecision")
        self.assertIn("[最新決策]", msg)
        self.assertIn("Decision：CHASE", msg)
        self.assertNotIn("payload=", msg)
        self.assertNotIn('"event_type": "decision_result"', msg)

    def test_last_signal_message_is_short_and_without_payload_dump(self) -> None:
        self._write_events(
            [
                {
                    "event_type": "signal_received",
                    "request_id": "req_sig_1",
                    "timestamp": "2026-04-25T01:00:01+08:00",
                    "payload": {
                        "symbol": "MNQ",
                        "signal": "long_breakout",
                        "price": 20155.5,
                        "breakout_level": 20145,
                        "delta_strength": 0.92,
                    },
                }
            ]
        )
        msg = tg._format_last_signal_message()
        self.assertIn("[最新訊號]", msg)
        self.assertIn("Symbol：MNQ", msg)
        self.assertNotIn("payload=", msg)
        self.assertNotIn('"symbol": "MNQ"', msg)

    def test_missing_fields_do_not_crash_and_use_dash(self) -> None:
        self._write_events(
            [
                {
                    "event_type": "decision_result",
                    "request_id": "req_missing",
                }
            ]
        )
        msg = tg._format_last_event_by_type("decision_result", "LastDecision")
        self.assertIn("Decision：-", msg)
        self.assertIn("Reason：-", msg)
        self.assertIn("Price：-", msg)

    def test_last_fill_message_short_format(self) -> None:
        self._write_events(
            [
                {
                    "event_type": "fill_result",
                    "request_id": "req_fill_1",
                    "timestamp": "2026-04-25T01:00:02+08:00",
                    "status": "applied",
                    "payload": {"symbol": "MNQ", "side": "BUY", "qty": 1, "price": 20160},
                    "message": "fill synced",
                }
            ]
        )
        msg = tg._format_last_event_by_type("fill_result", "LastFill")
        self.assertIn("[最新成交]", msg)
        self.assertIn("Status：applied", msg)
        self.assertIn("Symbol：MNQ", msg)
        self.assertNotIn("payload=", msg)


if __name__ == "__main__":
    unittest.main()
