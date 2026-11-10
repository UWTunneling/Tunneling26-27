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

    """
    def test_example(self):
        mcu = make_mcu()
        # TODO: replace with a real test
        self.assertEqual(mcu.get_state(), STATE_STOP)
    """


# ---------------------------------------------------------------------------
# Paras — fault injection tests
# ---------------------------------------------------------------------------

class TestFaultInjection(unittest.TestCase):

    """
    def test_example(self):
        mcu = make_mcu()
        # TODO: replace with a real test
        self.assertEqual(mcu.get_state(), STATE_STOP)
    """

    #changes the state of a mcu to RUNNING
    def arm_mcu(self ,mcu):
        self.assertTrue(mcu.hardware_start()) #STOP -> READY
        self.assertTrue(mcu.software_start()) #READY -> RUNNING

    def test_critical_sensor_from_running(self):
        mcu = make_mcu()

        self.arm_mcu(mcu)
        self.assertEqual(mcu.get_state(), STATE_RUNNING)

        #inject the sensor fault
        accepted = mcu.critical_sensor_fault()

        #check that fault was accepted and produced the correct STOP state
        self.assertTrue(accepted)
        self.assertEqual(mcu.get_state(), STATE_STOP)
        self.assertEqual(mcu.get_reason(), "critical_sensor")
        self.assertFalse(mcu.is_estop_latched())        
        
    def test_comm_fail(self):
        mcu = make_mcu()

        self.arm_mcu(mcu)
        self.assertEqual(mcu.get_state(), STATE_RUNNING)

        #inject the comm fail
        accepted = mcu.comm_fail()

        #check that fault was accepted and produced the correct STOP state
        self.assertTrue(accepted)
        self.assertEqual(mcu.get_state(), STATE_STOP)
        self.assertEqual(mcu.get_reason(), "comm_fail")
        self.assertFalse(mcu.is_estop_latched())   
    
    def test_latch_estop(self):
        mcu = make_mcu()

        self.arm_mcu(mcu)
        self.assertEqual(mcu.get_state(), STATE_RUNNING)

        #inject the E-stop
        accepted = mcu.latch_estop()

        #check that fault was accepted and produced the correct STOP state
        self.assertTrue(accepted)
        self.assertEqual(mcu.get_state(), STATE_STOP)
        self.assertEqual(mcu.get_reason(), "estop")
        self.assertTrue(mcu.is_estop_latched())   
    
    def test_software_stop(self):
        mcu = make_mcu()

        self.arm_mcu(mcu)
        self.assertEqual(mcu.get_state(), STATE_RUNNING)

        #inject fault from software stop
        accepted = mcu.software_stop()

        #check that fault was accepted and produced the correct STOP state
        self.assertTrue(accepted)
        self.assertEqual(mcu.get_state(), STATE_STOP)
        self.assertEqual(mcu.get_reason(), "software_stop")
        self.assertFalse(mcu.is_estop_latched())   
    

if __name__ == "__main__":
    unittest.main(verbosity=2)
