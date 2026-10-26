"""Tests for the MockMicrocontroller safety state machine.

    STOP --(unlatch)--> STOP unlatched --(hardware_start)--> READY
    READY --(software_start)--> RUNNING
    any --(latch_estop / software_stop / critical sensor / comm fail)--> STOP

These are the rules the real firmware must also follow, so treat a failure
here as "the machine could start when it shouldn't" until proven otherwise.
"""

import unittest

from mock_microcontroller import (
    MockMicrocontroller,
    REASON_BOOTUP,
    REASON_COMM_FAIL,
    REASON_CRITICAL_SENSOR,
    REASON_ESTOP,
    REASON_READY,
    REASON_RUNNING,
    REASON_SOFTWARE_STOP,
    STATE_READY,
    STATE_RUNNING,
    STATE_STOP,
)
from sensor_simulator import SensorSimulator


def _mcu() -> MockMicrocontroller:
    # Inject a simulator so the MCU doesn't need its polling thread for these tests.
    return MockMicrocontroller(simulator=SensorSimulator(seed=0))


def _running_mcu() -> MockMicrocontroller:
    mcu = _mcu()
    mcu.hardware_start()
    mcu.software_start()
    assert mcu.get_state() == STATE_RUNNING
    return mcu


class TestBoot(unittest.TestCase):

    def test_boots_into_stop_unlatched(self):
        mcu = _mcu()
        self.assertEqual(mcu.get_state(), STATE_STOP)
        self.assertFalse(mcu.is_estop_latched())
        self.assertEqual(mcu.get_reason(), REASON_BOOTUP)

    def test_bootup_always_notifies_listeners(self):
        mcu = _mcu()
        events = []
        mcu.add_state_listener(lambda *args: events.append(args))
        mcu.bootup()
        mcu.bootup()
        self.assertEqual(events, [(STATE_STOP, False, REASON_BOOTUP, False)] * 2)


class TestStartSequence(unittest.TestCase):

    def test_hardware_start_then_software_start(self):
        mcu = _mcu()
        self.assertTrue(mcu.hardware_start())
        self.assertEqual(mcu.get_state(), STATE_READY)
        self.assertEqual(mcu.get_reason(), REASON_READY)
        self.assertTrue(mcu.software_start())
        self.assertEqual(mcu.get_state(), STATE_RUNNING)
        self.assertEqual(mcu.get_reason(), REASON_RUNNING)

    def test_software_start_rejected_from_stop(self):
        mcu = _mcu()
        self.assertFalse(mcu.software_start())
        self.assertEqual(mcu.get_state(), STATE_STOP)

    def test_hardware_start_rejected_while_running(self):
        mcu = _running_mcu()
        self.assertFalse(mcu.hardware_start())
        self.assertEqual(mcu.get_state(), STATE_RUNNING)


class TestEStop(unittest.TestCase):

    def test_estop_from_every_state_goes_to_stop_latched(self):
        for setup in (_mcu, _running_mcu):
            mcu = setup()
            mcu.latch_estop()
            self.assertEqual(mcu.get_state(), STATE_STOP)
            self.assertTrue(mcu.is_estop_latched())
            self.assertEqual(mcu.get_reason(), REASON_ESTOP)

    def test_estop_from_ready(self):
        mcu = _mcu()
        mcu.hardware_start()
        mcu.latch_estop()
        self.assertEqual(mcu.get_state(), STATE_STOP)
        self.assertTrue(mcu.is_estop_latched())

    def test_cannot_arm_while_latched(self):
        mcu = _running_mcu()
        mcu.latch_estop()
        self.assertFalse(mcu.hardware_start())
        self.assertFalse(mcu.software_start())
        self.assertEqual(mcu.get_state(), STATE_STOP)

    def test_unlatch_stays_in_stop(self):
        """Recovery is two separate actions: unlatch, then hardware start."""
        mcu = _running_mcu()
        mcu.latch_estop()
        self.assertTrue(mcu.unlatch_estop())
        self.assertEqual(mcu.get_state(), STATE_STOP)
        self.assertFalse(mcu.is_estop_latched())
        self.assertTrue(mcu.hardware_start())
        self.assertEqual(mcu.get_state(), STATE_READY)

    def test_unlatch_when_not_latched_is_noop(self):
        self.assertFalse(_mcu().unlatch_estop())


class TestFaultStops(unittest.TestCase):

    def test_software_stop_from_running(self):
        mcu = _running_mcu()
        self.assertTrue(mcu.software_stop())
        self.assertEqual(mcu.get_state(), STATE_STOP)
        self.assertEqual(mcu.get_reason(), REASON_SOFTWARE_STOP)
        self.assertFalse(mcu.is_estop_latched())

    def test_critical_sensor_fault_from_running(self):
        mcu = _running_mcu()
        self.assertTrue(mcu.critical_sensor_fault())
        self.assertEqual(mcu.get_state(), STATE_STOP)
        self.assertEqual(mcu.get_reason(), REASON_CRITICAL_SENSOR)

    def test_comm_fail_from_ready(self):
        mcu = _mcu()
        mcu.hardware_start()
        self.assertTrue(mcu.comm_fail())
        self.assertEqual(mcu.get_state(), STATE_STOP)
        self.assertEqual(mcu.get_reason(), REASON_COMM_FAIL)

    def test_faults_ignored_when_already_stopped(self):
        mcu = _mcu()
        self.assertFalse(mcu.critical_sensor_fault())
        self.assertFalse(mcu.comm_fail())
        self.assertFalse(mcu.software_stop())
        self.assertEqual(mcu.get_reason(), REASON_BOOTUP)


class TestListeners(unittest.TestCase):

    def test_operator_commands_are_acked(self):
        mcu = _mcu()
        events = []
        mcu.add_state_listener(lambda *args: events.append(args))
        mcu.hardware_start()
        mcu.software_start()
        mcu.software_stop()
        self.assertEqual(events, [
            (STATE_READY, False, REASON_READY, False),
            (STATE_RUNNING, False, REASON_RUNNING, True),
            (STATE_STOP, False, REASON_SOFTWARE_STOP, True),
        ])

    def test_mcu_originated_events_are_not_acks(self):
        mcu = _running_mcu()
        events = []
        mcu.add_state_listener(lambda *args: events.append(args))
        mcu.latch_estop()
        self.assertEqual(events, [(STATE_STOP, True, REASON_ESTOP, False)])

    def test_no_notification_when_nothing_changes(self):
        mcu = _mcu()
        mcu.latch_estop()
        events = []
        mcu.add_state_listener(lambda *args: events.append(args))
        self.assertFalse(mcu.latch_estop())
        self.assertEqual(events, [])

