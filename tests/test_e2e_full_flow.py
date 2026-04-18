from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import app as app_module
import command_writer
import e2e_full_flow
import execution_tracker
import state_manager


class E2EFullFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.output_dir = Path(self.tmp.name) / "output"
        e2e_full_flow.configure_output_dir(self.output_dir)
        state_manager.save_state(state_manager._default_state())
        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_webhook_to_fill_chain_and_notifies(self) -> None:
        res = e2e_full_flow.run_e2e_flow(self.client, include_duplicate_fill=True, fill_pnl=3.0, fill_cooldown_minutes=None)

        self.assertEqual(res.webhook_status, 200)
        self.assertIn(res.decision, ("CHASE", "RETEST", "SKIP"))
        self.assertIsNotNone(res.request_id)

        if res.decision in ("CHASE", "RETEST"):
            self.assertTrue(command_writer.ORDER_COMMAND_PATH.is_file())
            self.assertIsNotNone(res.order_command)
            assert res.order_command is not None
            self.assertEqual(res.order_command.get("request_id"), res.request_id)

        self.assertEqual(res.fill_first_status, 200)
        self.assertTrue(res.fill_first_body.get("applied"))

        self.assertEqual(res.fill_dup_status, 200)
        self.assertIsNotNone(res.fill_dup_body)
        assert res.fill_dup_body is not None
        self.assertFalse(res.fill_dup_body.get("applied"))
        self.assertEqual(res.fill_dup_body.get("reason"), "duplicate_fill")

        self.assertEqual(len(res.decision_notifications), 1)
        self.assertEqual(res.decision_notifications[0].get("decision"), res.decision)
        self.assertIsInstance(res.decision_trace, dict)
        assert res.decision_trace is not None
        self.assertEqual(
            res.decision_notifications[0].get("reason_code"),
            res.decision_trace.get("reason_code"),
        )

        self.assertEqual(len(res.fill_notifications), 2)
        self.assertTrue(res.fill_notifications[0].get("applied"))
        self.assertFalse(res.fill_notifications[1].get("applied"))
        self.assertTrue(res.fill_notifications[1].get("dedupe"))


if __name__ == "__main__":
    unittest.main()
