from __future__ import annotations

import unittest

from reason_translator import translate_reason_code


class ReasonTranslatorTests(unittest.TestCase):
    def test_known_reason_code_translation(self) -> None:
        result = translate_reason_code("COOLDOWN_ACTIVE", {"cooldown_until": "2026-04-25T23:00:00+08:00"})
        self.assertEqual(result["raw_reason_code"], "COOLDOWN_ACTIVE")
        self.assertEqual(result["reason_code"], "COOLDOWN_ACTIVE")
        self.assertIn("title", result)
        self.assertIn("message", result)
        self.assertIn("suggested_action", result)
        self.assertEqual(result["severity"], "warning")

    def test_unknown_reason_code_fallback(self) -> None:
        result = translate_reason_code("totally_new_code_xxx")
        self.assertEqual(result["raw_reason_code"], "TOTALLY_NEW_CODE_XXX")
        self.assertEqual(result["reason_code"], "UNKNOWN")
        self.assertEqual(result["severity"], "warning")

    def test_details_none_does_not_crash(self) -> None:
        result = translate_reason_code("STATE_GATE", None)
        self.assertEqual(result["raw_reason_code"], "STATE_GATE")
        self.assertEqual(result["reason_code"], "STATE_GATE")
        self.assertIsInstance(result["message"], str)
        self.assertGreater(len(result["message"]), 0)

    def test_severity_is_constrained(self) -> None:
        allowed = {"info", "warning", "danger"}
        for code in (
            "STATE_GATE",
            "COOLDOWN_ACTIVE",
            "LOSS_STREAK_LIMIT",
            "DAILY_LOSS_LIMIT",
            "DUPLICATE_SIGNAL",
            "SESSION_CLOSED",
            "RISK_REDUCED",
            "RISK_BLOCKED",
            "HIGH_VOL_GUARDRAIL",
            "DECISION_SKIP",
            "UNKNOWN",
        ):
            result = translate_reason_code(code, {})
            self.assertIn(result["severity"], allowed)

    def test_alias_risk_daily_loss_guard_maps_to_risk_blocked(self) -> None:
        result = translate_reason_code("RISK_DAILY_LOSS_GUARD")
        self.assertEqual(result["raw_reason_code"], "RISK_DAILY_LOSS_GUARD")
        self.assertEqual(result["reason_code"], "RISK_BLOCKED")
        self.assertEqual(result["severity"], "danger")

    def test_alias_duplicate_ignored_maps_to_duplicate_signal(self) -> None:
        result = translate_reason_code("DUPLICATE_IGNORED")
        self.assertEqual(result["raw_reason_code"], "DUPLICATE_IGNORED")
        self.assertEqual(result["reason_code"], "DUPLICATE_SIGNAL")


if __name__ == "__main__":
    unittest.main()

