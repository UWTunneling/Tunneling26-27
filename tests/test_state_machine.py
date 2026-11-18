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

    # Test 1: Checks if we can turn on the machine, run it,
    # and turn it off normally.
    def test_normal_startup_and_stop(self):
        mcu = make_mcu()

        # starts in STOP
        self.assertEqual(mcu.get_state(), STATE_STOP)

        # STOP -> READY
        self.assertTrue(mcu.hardware_start())
        self.assertEqual(mcu.get_state(), STATE_READY)
        self.assertEqual(mcu.get_reason(), "ready")

        # READY -> RUNNING
        self.assertTrue(mcu.software_start())
        self.assertEqual(mcu.get_state(), STATE_RUNNING)
        self.assertEqual(mcu.get_reason(), "running")

        # RUNNING -> STOP
        self.assertTrue(mcu.software_stop())
        self.assertEqual(mcu.get_state(), STATE_STOP)
        self.assertEqual(mcu.get_reason(), "software_stop")

    # Test 2: Checks if pressing the emergency button
    # stops and locks the machine.
    def test_estop_from_ready(self):
        mcu = make_mcu()

        # get to READY
        self.assertTrue(mcu.hardware_start())
        self.assertEqual(mcu.get_state(), STATE_READY)

        # press the E-stop
        self.assertTrue(mcu.latch_estop())

        # should be stopped AND locked
        self.assertEqual(mcu.get_state(), STATE_STOP)
        self.assertEqual(mcu.get_reason(), "estop")
        self.assertTrue(mcu.is_estop_latched())

    # Test 3: Checks that after an emergency stop that you can't
    # restart unless the machine is unlocked. When it's unlocked,
    # starting the machine makes it ready.
    def test_recovery_after_estop(self):
        mcu = make_mcu()

        # get to RUNNING, then press the E-stop
        self.assertTrue(mcu.hardware_start())
        self.assertTrue(mcu.software_start())
        self.assertTrue(mcu.latch_estop())

        # while locked, starting again is refused
        self.assertFalse(mcu.hardware_start())
        self.assertEqual(mcu.get_state(), STATE_STOP)

        # step 1: unlock -> unlocked, but still in STOP
        self.assertTrue(mcu.unlatch_estop())
        self.assertFalse(mcu.is_estop_latched())
        self.assertEqual(mcu.get_state(), STATE_STOP)

        # step 2: hardware start -> READY
        self.assertTrue(mcu.hardware_start())
        self.assertEqual(mcu.get_state(), STATE_READY)

    # Test 4: Checks that pressing a certain button in the wrong state
    # doesn't work and that the machine is still in the same state.
    def test_wrong_commands_are_refused(self):
        mcu = make_mcu()

        # in STOP: can't jump straight to RUNNING
        self.assertFalse(mcu.software_start())
        self.assertEqual(mcu.get_state(), STATE_STOP)

        # in STOP: can't stop again
        self.assertFalse(mcu.software_stop())
        self.assertEqual(mcu.get_state(), STATE_STOP)

        # get to RUNNING
        self.assertTrue(mcu.hardware_start())
        self.assertTrue(mcu.software_start())

        # in RUNNING: hardware start does nothing
        self.assertFalse(mcu.hardware_start())
        self.assertEqual(mcu.get_state(), STATE_RUNNING)


# ---------------------------------------------------------------------------
# Paras — fault injection tests
# ---------------------------------------------------------------------------

class TestFaultInjection(unittest.TestCase):

    def arm_mcu(self, mcu):
        self.assertTrue(mcu.hardware_start())
        self.assertTrue(mcu.software_start())

    def test_critical_sensor_from_running(self):
        mcu = make_mcu()

        self.arm_mcu(mcu)
        self.assertEqual(mcu.get_state(), STATE_RUNNING)

        accepted = mcu.critical_sensor_fault()

        self.assertTrue(accepted)
        self.assertEqual(mcu.get_state(), STATE_STOP)
        self.assertEqual(mcu.get_reason(), "critical_sensor")
        self.assertFalse(mcu.is_estop_latched())

    def test_comm_fail(self):
        mcu = make_mcu()

        self.arm_mcu(mcu)
        self.assertEqual(mcu.get_state(), STATE_RUNNING)

        accepted = mcu.comm_fail()

        self.assertTrue(accepted)
        self.assertEqual(mcu.get_state(), STATE_STOP)
        self.assertEqual(mcu.get_reason(), "comm_fail")
        self.assertFalse(mcu.is_estop_latched())

    def test_latch_estop(self):
        mcu = make_mcu()

        self.arm_mcu(mcu)
        self.assertEqual(mcu.get_state(), STATE_RUNNING)

        accepted = mcu.latch_estop()

        self.assertTrue(accepted)
        self.assertEqual(mcu.get_state(), STATE_STOP)
        self.assertEqual(mcu.get_reason(), "estop")
        self.assertTrue(mcu.is_estop_latched())

    def test_software_stop(self):
        mcu = make_mcu()

        self.arm_mcu(mcu)
        self.assertEqual(mcu.get_state(), STATE_RUNNING)

        accepted = mcu.software_stop()

        self.assertTrue(accepted)
        self.assertEqual(mcu.get_state(), STATE_STOP)
        self.assertEqual(mcu.get_reason(), "software_stop")
        self.assertFalse(mcu.is_estop_latched())


if __name__ == "__main__":
    unittest.main(verbosity=2)
