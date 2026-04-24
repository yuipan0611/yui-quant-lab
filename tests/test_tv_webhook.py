from __future__ import annotations

import json
import multiprocessing as mp
import os
import threading
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
import webhook_dedupe as webhook_dedupe_module


def _mp_check_and_remember_worker(
    dedupe_path: str,
    endpoint: str,
    payload: dict,
    start_event,
    result_queue,
) -> None:
    # 每個 process 都使用同一個 dedupe store 路徑，模擬 Gunicorn 多 worker。
    os.environ["WEBHOOK_DEDUPE_PATH"] = dedupe_path
    webhook_dedupe_module.get_dedupe_path.cache_clear()
    start_event.wait()
    is_dup = webhook_dedupe_module.check_and_remember(endpoint, payload)
    result_queue.put(bool(is_dup))


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
        self.dedupe_path = self.output_dir / "webhook_dedupe.json"
        self.env_patch = patch.dict(
            os.environ,
            {
                "TV_WEBHOOK_SECRET": self.secret,
                "WEBHOOK_DEDUPE_PATH": str(self.dedupe_path),
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

    def test_duplicate_response_keeps_tv_compat_and_new_schema(self) -> None:
        payload = self._tv_body(signal="short_breakout")
        first = self.client.post("/tv-webhook", json=payload)
        self.assertEqual(first.status_code, 200)
        first_body = first.get_json()
        assert isinstance(first_body, dict)
        self.assertTrue(first_body.get("ok"))

        second = self.client.post("/tv-webhook", json=payload)
        self.assertEqual(second.status_code, 200)
        body = second.get_json()
        assert isinstance(body, dict)

        self.assertTrue(body.get("ok"))
        self.assertTrue(body.get("duplicate"))
        for key in ("status", "decision", "reason_code", "branch", "request_id", "trace"):
            self.assertIn(key, body)
        self.assertEqual(body.get("status"), "duplicate_ignored")
        self.assertEqual(body.get("decision"), "SKIP")
        self.assertEqual(body.get("reason_code"), "DUPLICATE_IGNORED")
        self.assertEqual(body.get("branch"), "SHORT")
        self.assertRegex(str(body.get("request_id")), r"^dup_[0-9a-f]{12}$")

        tr = body.get("trace")
        assert isinstance(tr, dict)
        self.assertTrue(tr.get("duplicate"))
        self.assertEqual(tr.get("decision"), "SKIP")
        self.assertEqual(tr.get("reason_code"), "DUPLICATE_IGNORED")
        self.assertEqual(tr.get("branch"), "SHORT")
        self.assertIn("timestamp", tr)
        inputs = tr.get("inputs")
        assert isinstance(inputs, dict)
        self.assertEqual(set(inputs.keys()), {"signal", "regime"})
        self.assertEqual(inputs.get("signal"), "short_breakout")
        self.assertIsNone(inputs.get("regime"))

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


def test_get_dedupe_path_default_and_override_with_tmp_path(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("WEBHOOK_DEDUPE_PATH", raising=False)
    webhook_dedupe_module.get_dedupe_path.cache_clear()
    default_path = webhook_dedupe_module.get_dedupe_path()
    expected_default = Path(webhook_dedupe_module.__file__).resolve().parent / "output" / "webhook_dedupe.json"
    assert default_path == expected_default

    override_path = tmp_path / "isolated" / "dedupe.json"
    monkeypatch.setenv("WEBHOOK_DEDUPE_PATH", str(override_path))
    webhook_dedupe_module.get_dedupe_path.cache_clear()
    assert webhook_dedupe_module.get_dedupe_path() == override_path


def test_get_dedupe_path_stays_consistent_in_process(tmp_path, monkeypatch) -> None:
    first_path = tmp_path / "first" / "dedupe.json"
    second_path = tmp_path / "second" / "dedupe.json"
    monkeypatch.setenv("WEBHOOK_DEDUPE_PATH", str(first_path))
    webhook_dedupe_module.get_dedupe_path.cache_clear()
    got_first = webhook_dedupe_module.get_dedupe_path()

    # 同一 process 內路徑固定，避免多次讀 env 造成不一致。
    monkeypatch.setenv("WEBHOOK_DEDUPE_PATH", str(second_path))
    got_second = webhook_dedupe_module.get_dedupe_path()
    assert got_first == got_second == first_path


def test_remember_writes_to_webhook_dedupe_path(tmp_path, monkeypatch) -> None:
    target_path = tmp_path / "nested" / "webhook_dedupe.json"
    monkeypatch.setenv("WEBHOOK_DEDUPE_PATH", str(target_path))
    webhook_dedupe_module.get_dedupe_path.cache_clear()

    webhook_dedupe_module.remember(
        "/webhook",
        {
            "symbol": "MNQ",
            "signal": "long_breakout",
            "price": 20150.0,
            "breakout_level": 20145.0,
            "delta_strength": 0.88,
        },
    )

    assert target_path.exists()
    data = json.loads(target_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert len(data) == 1


def test_check_and_remember_same_endpoint_same_payload(tmp_path, monkeypatch) -> None:
    target_path = tmp_path / "output" / "webhook_dedupe.json"
    monkeypatch.setenv("WEBHOOK_DEDUPE_PATH", str(target_path))
    webhook_dedupe_module.get_dedupe_path.cache_clear()
    payload = {
        "symbol": "MNQ",
        "signal": "long_breakout",
        "price": 20150.0,
        "breakout_level": 20145.0,
        "delta_strength": 0.9,
    }
    assert webhook_dedupe_module.check_and_remember("/webhook", payload) is False
    assert webhook_dedupe_module.check_and_remember("/webhook", payload) is True


def test_check_and_remember_same_payload_different_endpoint_not_duplicate(tmp_path, monkeypatch) -> None:
    target_path = tmp_path / "output" / "webhook_dedupe.json"
    monkeypatch.setenv("WEBHOOK_DEDUPE_PATH", str(target_path))
    webhook_dedupe_module.get_dedupe_path.cache_clear()
    payload = {
        "symbol": "MNQ",
        "signal": "long_breakout",
        "price": 20150.0,
        "breakout_level": 20145.0,
        "delta_strength": 0.9,
    }
    assert webhook_dedupe_module.check_and_remember("/webhook", payload) is False
    assert webhook_dedupe_module.check_and_remember("/tv-webhook", payload) is False


def test_check_and_remember_ttl_window_then_expire(tmp_path, monkeypatch) -> None:
    target_path = tmp_path / "output" / "webhook_dedupe.json"
    monkeypatch.setenv("WEBHOOK_DEDUPE_PATH", str(target_path))
    monkeypatch.setenv("WEBHOOK_DEDUPE_TTL_SEC", "30")
    webhook_dedupe_module.get_dedupe_path.cache_clear()
    payload = {
        "symbol": "MNQ",
        "signal": "long_breakout",
        "price": 20150.0,
        "breakout_level": 20145.0,
        "delta_strength": 0.9,
    }
    with patch("webhook_dedupe.time.time", return_value=1000.0):
        assert webhook_dedupe_module.check_and_remember("/webhook", payload) is False
    with patch("webhook_dedupe.time.time", return_value=1020.0):
        assert webhook_dedupe_module.check_and_remember("/webhook", payload) is True
    with patch("webhook_dedupe.time.time", return_value=1031.0):
        assert webhook_dedupe_module.check_and_remember("/webhook", payload) is False


def test_check_and_remember_concurrent_same_payload_only_one_first(tmp_path, monkeypatch) -> None:
    target_path = tmp_path / "output" / "webhook_dedupe.json"
    monkeypatch.setenv("WEBHOOK_DEDUPE_PATH", str(target_path))
    webhook_dedupe_module.get_dedupe_path.cache_clear()
    payload = {
        "symbol": "MNQ",
        "signal": "long_breakout",
        "price": 20150.0,
        "breakout_level": 20145.0,
        "delta_strength": 0.9,
    }

    thread_count = 8
    barrier = threading.Barrier(thread_count)
    results: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        got = webhook_dedupe_module.check_and_remember("/webhook", payload)
        with lock:
            results.append(got)

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(False) == 1
    assert results.count(True) == thread_count - 1


def test_check_and_remember_concurrent_two_payloads_no_lost_update(tmp_path, monkeypatch) -> None:
    target_path = tmp_path / "output" / "webhook_dedupe.json"
    monkeypatch.setenv("WEBHOOK_DEDUPE_PATH", str(target_path))
    webhook_dedupe_module.get_dedupe_path.cache_clear()
    payload_a = {
        "symbol": "MNQ",
        "signal": "long_breakout",
        "price": 20150.0,
        "breakout_level": 20145.0,
        "delta_strength": 0.9,
    }
    payload_b = {
        "symbol": "MNQ",
        "signal": "short_breakout",
        "price": 20140.0,
        "breakout_level": 20142.0,
        "delta_strength": 0.8,
    }
    barrier = threading.Barrier(2)
    results: list[bool] = []
    lock = threading.Lock()

    def worker(payload: dict) -> None:
        barrier.wait()
        got = webhook_dedupe_module.check_and_remember("/webhook", payload)
        with lock:
            results.append(got)

    t1 = threading.Thread(target=worker, args=(payload_a,))
    t2 = threading.Thread(target=worker, args=(payload_b,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results.count(False) == 2
    store = json.loads(target_path.read_text(encoding="utf-8"))
    assert isinstance(store, dict)
    fp_a = webhook_dedupe_module.fingerprint_for("/webhook", payload_a)
    fp_b = webhook_dedupe_module.fingerprint_for("/webhook", payload_b)
    assert fp_a in store
    assert fp_b in store


def test_check_and_remember_multiprocess_same_payload_only_one_first(tmp_path, monkeypatch) -> None:
    target_path = tmp_path / "output" / "webhook_dedupe.json"
    monkeypatch.setenv("WEBHOOK_DEDUPE_PATH", str(target_path))
    webhook_dedupe_module.get_dedupe_path.cache_clear()
    payload = {
        "symbol": "MNQ",
        "signal": "long_breakout",
        "price": 20150.0,
        "breakout_level": 20145.0,
        "delta_strength": 0.9,
    }

    ctx = mp.get_context("spawn")
    start_event = ctx.Event()
    result_queue = ctx.Queue()
    proc_count = 6
    procs = [
        ctx.Process(
            target=_mp_check_and_remember_worker,
            args=(str(target_path), "/webhook", payload, start_event, result_queue),
        )
        for _ in range(proc_count)
    ]
    for p in procs:
        p.start()
    # 所有 process 啟動後同時放行，增加同時競爭臨界區的機率。
    start_event.set()
    for p in procs:
        p.join()

    results = [result_queue.get() for _ in range(proc_count)]
    assert results.count(False) == 1
    assert results.count(True) == proc_count - 1


def test_check_and_remember_multiprocess_different_payloads_no_lost_update(tmp_path, monkeypatch) -> None:
    target_path = tmp_path / "output" / "webhook_dedupe.json"
    monkeypatch.setenv("WEBHOOK_DEDUPE_PATH", str(target_path))
    webhook_dedupe_module.get_dedupe_path.cache_clear()
    payloads = [
        {
            "symbol": "MNQ",
            "signal": "long_breakout",
            "price": 20150.0,
            "breakout_level": 20145.0,
            "delta_strength": 0.9,
        },
        {
            "symbol": "MNQ",
            "signal": "short_breakout",
            "price": 20140.0,
            "breakout_level": 20142.0,
            "delta_strength": 0.8,
        },
        {
            "symbol": "NQ",
            "signal": "long_breakout",
            "price": 20200.0,
            "breakout_level": 20190.0,
            "delta_strength": 0.7,
        },
    ]

    ctx = mp.get_context("spawn")
    start_event = ctx.Event()
    result_queue = ctx.Queue()
    procs = [
        ctx.Process(
            target=_mp_check_and_remember_worker,
            args=(str(target_path), "/webhook", payload, start_event, result_queue),
        )
        for payload in payloads
    ]
    for p in procs:
        p.start()
    start_event.set()
    for p in procs:
        p.join()

    results = [result_queue.get() for _ in range(len(payloads))]
    assert results.count(False) == len(payloads)

    store = json.loads(target_path.read_text(encoding="utf-8"))
    assert isinstance(store, dict)
    for payload in payloads:
        fp = webhook_dedupe_module.fingerprint_for("/webhook", payload)
        assert fp in store
