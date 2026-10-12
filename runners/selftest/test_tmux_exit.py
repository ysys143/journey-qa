#!/usr/bin/env python3
"""Deterministic exit-status readiness checks; no tmux server or real wait."""
import sys

sys.dont_write_bytecode = True

import os  # noqa: E402
import unittest  # noqa: E402
from unittest.mock import Mock, patch  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "terminal"))
import tmux_driver as driver  # noqa: E402


class ExitReadiness(unittest.TestCase):
    def session(self, states):
        session = driver.Session("unused", "unused")
        session.state = Mock(side_effect=states)
        return session

    def test_closed_pty_is_not_complete_until_exit_status_is_available(self):
        session = self.session([(True, None, 0), (True, None, 0), (True, 0, 0)])
        with patch.object(driver.time, "monotonic", return_value=0), patch.object(driver.time, "sleep") as sleep:
            self.assertEqual(session.wait_exit(1, 0.05), "matched")
        self.assertEqual(session.state.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_a_running_program_must_exit_before_its_status_is_ready(self):
        session = self.session([(False, None, 0), (True, 7, 0)])
        with patch.object(driver.time, "monotonic", return_value=0), patch.object(driver.time, "sleep"):
            self.assertEqual(session.wait_exit(1, 0.05), "matched")
        self.assertEqual(session.state.call_count, 2)

    def test_unknown_exit_status_uses_the_existing_deadline(self):
        session = self.session([(True, None, 0), (True, None, 0)])
        with patch.object(driver.time, "monotonic", side_effect=[0, 2]), patch.object(driver.time, "sleep"):
            self.assertEqual(session.wait_exit(1, 0.05), "timeout")

    def test_a_missing_pane_is_not_a_known_exit_status(self):
        session = self.session([None])
        with patch.object(driver.time, "sleep") as sleep:
            self.assertEqual(session.wait_exit(1, 0.05), "exited")
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
