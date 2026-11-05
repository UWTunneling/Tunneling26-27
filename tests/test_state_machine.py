#!/usr/bin/env python3
"""Integration tests for the MockMicrocontroller state machine.

Run from the project root:
    python3 tests/test_state_machine.py

Authors:
    Saravana — TestStateTransitions
    Paras    — TestFaultInjection
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mock-microcontroller"))

from mock_microcontroller import (
    MockMicrocontroller,
    STATE_STOP, STATE_READY, STATE_RUNNING,
)


def make_mcu() -> MockMicrocontroller:
    """Create a fresh MCU in its boot state (STOP, unlatched)."""
    return MockMicrocontroller()


# ---------------------------------------------------------------------------
# Saravana — state transition tests
# ---------------------------------------------------------------------------

class TestStateTransitions(unittest.TestCase):

    def test_example(self):
        mcu = make_mcu()
        # TODO: replace with a real test
        self.assertEqual(mcu.get_state(), STATE_STOP)


# ---------------------------------------------------------------------------
# Paras — fault injection tests
# ---------------------------------------------------------------------------

class TestFaultInjection(unittest.TestCase):

    def test_example(self):
        mcu = make_mcu()
        # TODO: replace with a real test
        self.assertEqual(mcu.get_state(), STATE_STOP)


if __name__ == "__main__":
    unittest.main(verbosity=2)
