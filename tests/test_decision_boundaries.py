"""decision_engine 與 app 邊界情境（閾值、high_vol、bias、state gate、dedupe）。"""

from __future__ import annotations

import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import app as app_module
import command_writer
import e2e_full_flow
import execution_tracker
import state_manager
import telegram_bot as tg_module
from decision_engine import (
    MAX_EXTENSION_POINTS,
    MIN_DELTA_STRENGTH,
    MIN_ROOM_POINTS,
    REASON_BIAS_CONFLICT,
    REASON_CHASE_OK,
    REASON_EXTENSION_TOO_LARGE,
    REASON_HIGH_VOL_DOWNGRADE,
    REASON_LOW_DELTA,
    REASON_NO_ROOM,
    REASON_STATE_GATE,
    REASON_UNSUPPORTED_SIGNAL,
    decide_trade,
)
from helpers import (
    nq_eod_long_room,
    nq_eod_short_room,
    qqq_regime,
    trade_input_long,
    trade_input_short,
)
from state_manager import REGIME_HIGH_VOL, _default_state
from time_utils import now_taipei


class TestDeltaStrengthBoundaries(unittest.TestCase):
    def test_delta_strength_exactly_min_passes_low_delta_gate_and_chases(self) -> None:
        """規則：delta_strength < MIN 才 LOW_DELTA；等於 MIN 時不觸發該分支。"""
        price = 20150.0
        r_above = price + float(MIN_ROOM_POINTS)
        ti = trade_input_long(delta_strength=float(MIN_DELTA_STRENGTH), breakout_level=20140.0)
        r = decide_trade(ti, nq_eod_long_room(resistance_above_price=r_above), qqq_regime("normal"))
        self.assertEqual(r["trace"]["reason_code"], REASON_CHASE_OK)
        self.assertEqual(r["decision"], "CHASE")
        self.assertEqual(r["trace"]["inputs"]["delta_strength"], MIN_DELTA_STRENGTH)

    def test_delta_strength_slightly_below_min_skips_with_low_delta(self) -> None:
        """規則：嚴格小於 MIN 即 SKIP + LOW_DELTA。"""
        ti = trade_input_long(delta_strength=MIN_DELTA_STRENGTH - 1e-3)
        r = decide_trade(ti, nq_eod_long_room(resistance_above_price=20300.0), qqq_regime("normal"))
        self.assertEqual(r["decision"], "SKIP")
        self.assertEqual(r["trace"]["reason_code"], REASON_LOW_DELTA)

    def test_delta_strength_slightly_above_min_chases_when_other_gates_pass(self) -> None:
        """規則：高於 MIN 後續依 room / extension / bias 評估。"""
        price = 20150.0
        r_above = price + float(MIN_ROOM_POINTS)
        ti = trade_input_long(delta_strength=MIN_DELTA_STRENGTH + 1e-3, breakout_level=20140.0)
        r = decide_trade(ti, nq_eod_long_room(resistance_above_price=r_above), qqq_regime("normal"))
        self.assertEqual(r["decision"], "CHASE")
        self.assertEqual(r["trace"]["reason_code"], REASON_CHASE_OK)


class TestRoomPointsBoundaries(unittest.TestCase):
    def test_long_room_exactly_min_room_points_does_not_trigger_no_room(self) -> None:
        """規則：long 側 (nearest_r - price) < MIN_ROOM 才 NO_ROOM；等於 MIN 不觸發。"""
        price = 20150.0
        resistance = price + float(MIN_ROOM_POINTS)
        ti = trade_input_long(price=price, breakout_level=20140.0, delta_strength=0.9)
        r = decide_trade(ti, nq_eod_long_room(resistance_above_price=resistance), qqq_regime("normal"))
        self.assertEqual(r["decision"], "CHASE")
        self.assertEqual(r["trace"]["reason_code"], REASON_CHASE_OK)
        self.assertAlmostEqual(r["trace"]["inputs"]["room_points"], float(MIN_ROOM_POINTS))

    def test_long_room_slightly_below_min_retests_with_no_room(self) -> None:
        """規則：上方 room 嚴格小於 MIN_ROOM → RETEST + NO_ROOM。"""
        price = 20150.0
        resistance = price + float(MIN_ROOM_POINTS) - 0.5
        ti = trade_input_long(price=price, breakout_level=20140.0, delta_strength=0.9)
        r = decide_trade(ti, nq_eod_long_room(resistance_above_price=resistance), qqq_regime("normal"))
        self.assertEqual(r["decision"], "RETEST")
        self.assertEqual(r["trace"]["reason_code"], REASON_NO_ROOM)

    def test_long_no_resistance_above_price_room_points_null_chase_ok(self) -> None:
        """規則：無上方壓力時不檢查 room；room_points 為 null，空間視為充足 → CHASE_OK。"""
        ti = trade_input_long(breakout_level=20140.0, delta_strength=0.9)
        r = decide_trade(ti, nq_eod_long_room(resistance_above_price=None), qqq_regime("normal"))
        self.assertIsNone(r["trace"]["inputs"]["room_points"])
        self.assertEqual(r["decision"], "CHASE")
        self.assertEqual(r["trace"]["reason_code"], REASON_CHASE_OK)

    def test_short_no_support_below_price_room_points_null_chase_ok(self) -> None:
        """規則：無下方支撐時不檢查 room；room_points 為 null → CHASE_OK。"""
        ti = trade_input_short(price=20150.0, breakout_level=20160.0, delta_strength=0.9)
        r = decide_trade(ti, nq_eod_short_room(support_below_price=None), qqq_regime("normal"))
        self.assertIsNone(r["trace"]["inputs"]["room_points"])
        self.assertEqual(r["decision"], "CHASE")
        self.assertEqual(r["trace"]["reason_code"], REASON_CHASE_OK)

    def test_short_breakout_room_at_min_and_slightly_below_min(self) -> None:
        """
        規則（空）：(price - nearest_s) < MIN_ROOM 才 NO_ROOM；等於 MIN 不觸發。
        a) room 剛好 MIN → CHASE + CHASE_OK；b) 嚴格小於 → RETEST + NO_ROOM。
        """
        price = 20150.0
        breakout = 20160.0  # extension 10 <= MAX
        delta = 0.95
        support_ok = price - float(MIN_ROOM_POINTS)
        ti_ok = trade_input_short(price=price, breakout_level=breakout, delta_strength=delta)
        r_ok = decide_trade(ti_ok, nq_eod_short_room(support_below_price=support_ok), qqq_regime("normal"))
        self.assertEqual(r_ok["trace"]["branch"], "SHORT")
        self.assertAlmostEqual(r_ok["trace"]["inputs"]["room_points"], float(MIN_ROOM_POINTS))
        self.assertEqual(r_ok["decision"], "CHASE")
        self.assertEqual(r_ok["trace"]["reason_code"], REASON_CHASE_OK)

        support_tight = price - float(MIN_ROOM_POINTS) + 0.5
        ti_no = trade_input_short(price=price, breakout_level=breakout, delta_strength=delta)
        r_no = decide_trade(ti_no, nq_eod_short_room(support_below_price=support_tight), qqq_regime("normal"))
        self.assertEqual(r_no["trace"]["branch"], "SHORT")
        self.assertAlmostEqual(r_no["trace"]["inputs"]["room_points"], float(MIN_ROOM_POINTS) - 0.5)
        self.assertEqual(r_no["decision"], "RETEST")
        self.assertEqual(r_no["trace"]["reason_code"], REASON_NO_ROOM)


class TestExtensionPointsBoundaries(unittest.TestCase):
    def test_long_extension_exactly_max_chases(self) -> None:
        """規則：long extension = price - breakout；extension > MAX 才 EXTENSION_TOO_LARGE。"""
        price = 20150.0
        breakout = price - float(MAX_EXTENSION_POINTS)
        ti = trade_input_long(price=price, breakout_level=breakout, delta_strength=0.9)
        r = decide_trade(ti, nq_eod_long_room(resistance_above_price=20300.0), qqq_regime("normal"))
        self.assertEqual(r["decision"], "CHASE")
        self.assertEqual(r["trace"]["reason_code"], REASON_CHASE_OK)
        self.assertAlmostEqual(r["trace"]["inputs"]["extension_points"], float(MAX_EXTENSION_POINTS))

    def test_long_extension_slightly_above_max_retests_extension_too_large(self) -> None:
        """規則：延伸嚴格大於 MAX → RETEST + EXTENSION_TOO_LARGE。"""
        price = 20150.0
        ext = float(MAX_EXTENSION_POINTS) + 0.25
        breakout = price - ext
        ti = trade_input_long(price=price, breakout_level=breakout, delta_strength=0.9)
        r = decide_trade(ti, nq_eod_long_room(resistance_above_price=20300.0), qqq_regime("normal"))
        self.assertEqual(r["decision"], "RETEST")
        self.assertEqual(r["trace"]["reason_code"], REASON_EXTENSION_TOO_LARGE)

    def test_short_breakout_extension_at_max_and_slightly_above_max(self) -> None:
        """
        規則（空）：extension = breakout_level - price；> MAX 才 EXTENSION_TOO_LARGE。
        a) 剛好 MAX → CHASE + CHASE_OK；b) 略大於 MAX → RETEST + EXTENSION_TOO_LARGE。
        """
        price = 20150.0
        delta = 0.95
        nq = nq_eod_short_room(support_below_price=20000.0)

        ext_ok = float(MAX_EXTENSION_POINTS)
        br_ok = price + ext_ok
        ti_ok = trade_input_short(price=price, breakout_level=br_ok, delta_strength=delta)
        r_ok = decide_trade(ti_ok, nq, qqq_regime("normal"))
        self.assertEqual(r_ok["trace"]["branch"], "SHORT")
        self.assertAlmostEqual(r_ok["trace"]["inputs"]["extension_points"], ext_ok)
        self.assertEqual(r_ok["decision"], "CHASE")
        self.assertEqual(r_ok["trace"]["reason_code"], REASON_CHASE_OK)

        ext_big = float(MAX_EXTENSION_POINTS) + 0.2
        br_big = price + ext_big
        ti_big = trade_input_short(price=price, breakout_level=br_big, delta_strength=delta)
        r_big = decide_trade(ti_big, nq, qqq_regime("normal"))
        self.assertEqual(r_big["trace"]["branch"], "SHORT")
        self.assertAlmostEqual(r_big["trace"]["inputs"]["extension_points"], ext_big)
        self.assertEqual(r_big["decision"], "RETEST")
        self.assertEqual(r_big["trace"]["reason_code"], REASON_EXTENSION_TOO_LARGE)


class TestHighVolDowngrade(unittest.TestCase):
    def test_high_vol_downgrades_chase_to_retest_when_delta_below_high_vol_floor(self) -> None:
        """
        規則：regime=high_vol 且原決策 CHASE 時，若 delta < MIN+0.2 或 extension > cap
        則降級 RETEST + HIGH_VOL_DOWNGRADE，downgraded_from=CHASE。
        """
        price = 20150.0
        resistance = price + float(MIN_ROOM_POINTS)
        # 0.85 >= MIN_DELTA_STRENGTH 且 < MIN+0.2 → 先 CHASE 再被 high_vol 降級
        ti = trade_input_long(price=price, breakout_level=20140.0, delta_strength=0.85)
        r = decide_trade(
            ti,
            nq_eod_long_room(resistance_above_price=resistance),
            qqq_regime(REGIME_HIGH_VOL),
        )
        self.assertEqual(r["decision"], "RETEST")
        self.assertEqual(r["trace"]["reason_code"], REASON_HIGH_VOL_DOWNGRADE)
        self.assertEqual(r["trace"]["downgraded_from"], "CHASE")

    def test_high_vol_downgrade_when_extension_exceeds_high_vol_cap_with_strong_delta(self) -> None:
        """
        規則：high_vol 下 extension > max(5, MAX-10) 且原 CHASE 時降級；
        delta >= MIN+0.2 時僅能由 extension guard 觸發。
        """
        high_vol_ext_cap = max(5.0, float(MAX_EXTENSION_POINTS) - 10.0)
        ext_trigger = high_vol_ext_cap + 3.0  # 仍 <= MAX，故非 EXTENSION_TOO_LARGE
        self.assertLessEqual(ext_trigger, float(MAX_EXTENSION_POINTS))

        price = 20150.0
        breakout = price + ext_trigger
        delta_floor = float(MIN_DELTA_STRENGTH) + 0.2
        ti = trade_input_short(
            price=price,
            breakout_level=breakout,
            delta_strength=delta_floor + 0.05,
        )
        r = decide_trade(
            ti,
            nq_eod_short_room(support_below_price=20000.0),
            qqq_regime(REGIME_HIGH_VOL),
        )
        self.assertEqual(r["decision"], "RETEST")
        self.assertEqual(r["trace"]["reason_code"], REASON_HIGH_VOL_DOWNGRADE)
        self.assertEqual(r["trace"]["downgraded_from"], "CHASE")
        self.assertEqual(r["trace"]["inputs"]["regime"], REGIME_HIGH_VOL)
        self.assertAlmostEqual(r["trace"]["inputs"]["extension_points"], ext_trigger)


class TestBiasConflict(unittest.TestCase):
    def test_long_breakout_with_bearish_bias_skips_bias_conflict(self) -> None:
        """規則：long 需 bullish/neutral；bearish → SKIP + BIAS_CONFLICT。"""
        ti = trade_input_long(delta_strength=0.9, breakout_level=20140.0)
        r = decide_trade(ti, nq_eod_long_room(resistance_above_price=20300.0, bias="bearish"))
        self.assertEqual(r["decision"], "SKIP")
        self.assertEqual(r["trace"]["reason_code"], REASON_BIAS_CONFLICT)

    def test_short_breakout_with_bullish_bias_skips_bias_conflict(self) -> None:
        """規則：short 需 bearish/neutral；bullish → SKIP + BIAS_CONFLICT。"""
        ti = trade_input_short(price=20150.0, breakout_level=20160.0, delta_strength=0.9)
        r = decide_trade(ti, nq_eod_short_room(support_below_price=20000.0, bias="bullish"))
        self.assertEqual(r["decision"], "SKIP")
        self.assertEqual(r["trace"]["reason_code"], REASON_BIAS_CONFLICT)


class TestUnsupportedSignal(unittest.TestCase):
    def test_unsupported_signal_skips_with_unsupported_reason(self) -> None:
        """規則：signal 非 long_breakout/short_breakout → SKIP + UNSUPPORTED_SIGNAL。"""
        ti = trade_input_long(signal="range_reject", delta_strength=0.9)
        r = decide_trade(ti, nq_eod_long_room(resistance_above_price=20300.0))
        self.assertEqual(r["decision"], "SKIP")
        self.assertEqual(r["trace"]["reason_code"], REASON_UNSUPPORTED_SIGNAL)
        self.assertEqual(r["trace"]["branch"], "NONE")


class TestAppWebhookStateGate(unittest.TestCase):
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
        self.fill_notify_mock = Mock()
        app_module.notify_decision = self.notify_mock
        app_module.notify_fill_result = self.fill_notify_mock

        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        app_module.notify_decision = tg_module.notify_decision
        app_module.notify_fill_result = tg_module.notify_fill_result
        self.tmp_dir.cleanup()

    def test_webhook_state_gate_cooldown_skip_trace_shape(self) -> None:
        """規則：app 層 gate 拒絕時合成 trace：STATE_GATE、branch NONE、decision SKIP。"""
        s = _default_state()
        s["cooldown_until"] = (now_taipei() + timedelta(minutes=30)).isoformat(timespec="seconds")
        state_manager.save_state(s)

        payload = {
            "symbol": "MNQ",
            "signal": "long_breakout",
            "price": 20150.0,
            "breakout_level": 20145.0,
            "delta_strength": 0.92,
            "bias": "bullish",
            "levels": {"r1": 20300.0, "s1": 20000.0},
        }
        resp = self.client.post("/webhook", json=payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        assert isinstance(body, dict)
        tr = body.get("trace")
        self.assertIsInstance(tr, dict)
        assert isinstance(tr, dict)
        self.assertEqual(tr.get("reason_code"), REASON_STATE_GATE)
        self.assertEqual(tr.get("branch"), "NONE")
        self.assertEqual(tr.get("decision"), "SKIP")
        self.assertEqual(body.get("decision"), "SKIP")


class TestDuplicateFillViaE2EHelper(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.output_dir = Path(self.tmp.name) / "output"
        e2e_full_flow.configure_output_dir(self.output_dir)
        state_manager.save_state(state_manager._default_state())
        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_duplicate_fill_first_applied_second_not_applied(self) -> None:
        """規則：同 fill_id/request_id 第二次 /fill-result → applied false（dedupe）。"""
        res = e2e_full_flow.run_e2e_flow(
            self.client,
            include_duplicate_fill=True,
            fill_pnl=2.0,
            fill_cooldown_minutes=None,
        )
        self.assertTrue(res.fill_first_body.get("applied"))
        self.assertIsNotNone(res.fill_dup_body)
        assert res.fill_dup_body is not None
        self.assertFalse(res.fill_dup_body.get("applied"))


if __name__ == "__main__":
    unittest.main()
