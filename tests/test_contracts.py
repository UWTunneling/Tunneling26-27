"""Cross-module contract tests.

The operator, the mock MCU and protocol.py each keep their own copy of some
shared facts (sensor IDs, reason names, value ranges). These tests fail when
someone changes one side and forgets the other.
"""

import unittest
from types import SimpleNamespace

import protocol
from mock_microcontroller import (
    REASON_BOOTUP,
    REASON_COMM_FAIL,
    REASON_CRITICAL_SENSOR,
    REASON_ESTOP,
    REASON_SOFTWARE_STOP,
    STATE_RUNNING,
    STATE_STOP,
    THRESHOLDS,
    MockMicrocontroller,
)
from mock_microcontroller_gui import _STOP_ENCODERS, MockApp
from sensor_simulator import DEFAULT_CONFIGS, SENSOR_IDS, SensorSimulator
import send_messages_gui as operator_gui

# (encoder, sensor number) the MCU uses for each sensor; mirrors MockApp._tick_sensors.
MCU_SENSOR_FRAMES = {
    "temp_1":    (protocol.encode_temperature, 1),
    "temp_2":    (protocol.encode_temperature, 2),
    "temp_3":    (protocol.encode_temperature, 3),
    "rpm":       (protocol.encode_rpm, 1),
    "voltage_1": (protocol.encode_voltage, 1),
    "voltage_2": (protocol.encode_voltage, 2),
    "voltage_3": (protocol.encode_voltage, 3),
    "current_1": (protocol.encode_current, 1),
    "current_2": (protocol.encode_current, 2),
    "flow_1":    (protocol.encode_flow, 1),
}


class TestSensorIdsAgree(unittest.TestCase):

    def test_operator_and_simulator_sensor_ids_match(self):
        self.assertEqual(operator_gui.SENSOR_IDS, SENSOR_IDS)

    def test_every_sensor_has_threshold_and_default_config(self):
        self.assertEqual(set(THRESHOLDS), set(SENSOR_IDS))
        self.assertEqual(set(DEFAULT_CONFIGS), set(SENSOR_IDS))

    def test_mcu_frames_map_back_to_the_same_operator_sensor(self):
        """A reading sent by the MCU must land on the matching operator meter."""
        self.assertEqual(set(MCU_SENSOR_FRAMES), set(SENSOR_IDS))
        parser = protocol.StreamParser()
        for sid, (encoder, number) in MCU_SENSOR_FRAMES.items():
            msg = parser.feed(encoder(number, 1.0))[0]
            self.assertEqual(operator_gui._sensor_key(msg["type"], msg["sensor_id"]), sid)


class TestValueRangesFitTheWire(unittest.TestCase):

    def test_threshold_bounds_survive_encoding(self):
        """Values are sent as uint16 and silently clamped; a threshold the wire
        can't represent would hide an out-of-range reading from the operator."""
        for sid, (lo, hi) in THRESHOLDS.items():
            encoder, number = MCU_SENSOR_FRAMES[sid]
            for bound in (lo, hi):
                msg = protocol.StreamParser().feed(encoder(number, bound))[0]
                self.assertAlmostEqual(msg["value"], bound, places=2, msg=f"{sid}={bound}")


class TestStateFramesAgree(unittest.TestCase):

    def test_every_stop_reason_has_an_encoder(self):
        stop_reasons = {
            REASON_BOOTUP, REASON_ESTOP, REASON_SOFTWARE_STOP,
            REASON_CRITICAL_SENSOR, REASON_COMM_FAIL,
        }
        self.assertEqual(set(_STOP_ENCODERS), stop_reasons)
        for reason, encoder in _STOP_ENCODERS.items():
            _, end_state, _ = protocol.decode_state_transition(encoder().hex())
            self.assertEqual(end_state, "stop", reason)


class TestOperatorCommandsReachMcu(unittest.TestCase):
    """Frames the operator sends must be understood by the MCU's command handler."""

    def setUp(self):
        self.mcu = MockMicrocontroller(simulator=SensorSimulator(seed=0))
        self.logs = []
        # _handle_operator_command only touches self.mcu and self.log, so a
        # stand-in avoids building the Tk window.
        self.app = SimpleNamespace(mcu=self.mcu, log=self.logs.append)

    def _send(self, frame: bytes) -> None:
        for msg in protocol.StreamParser().feed(frame):
            MockApp._handle_operator_command(self.app, msg)

    def test_software_start(self):
        self.mcu.hardware_start()
        self._send(protocol.encode_running())
        self.assertEqual(self.mcu.get_state(), STATE_RUNNING)

    def test_software_stop(self):
        self.mcu.hardware_start()
        self.mcu.software_start()
        self._send(protocol.encode_software_stop())
        self.assertEqual(self.mcu.get_state(), STATE_STOP)
        self.assertEqual(self.mcu.get_reason(), REASON_SOFTWARE_STOP)

    def test_operator_comm_fail(self):
        self.mcu.hardware_start()
        self._send(protocol.encode_comm_fail())
        self.assertEqual(self.mcu.get_state(), STATE_STOP)
        self.assertEqual(self.mcu.get_reason(), REASON_COMM_FAIL)

    def test_operator_cannot_start_from_stop(self):
        self._send(protocol.encode_running())
        self.assertEqual(self.mcu.get_state(), STATE_STOP)
        self.assertTrue(any("Rejected" in line for line in self.logs))

    def test_ack_frames_are_ignored(self):
        self.mcu.hardware_start()
        self._send(protocol.encode_running(is_ack=True))
        self.assertNotEqual(self.mcu.get_state(), STATE_RUNNING)

    def test_operator_cannot_spoof_estop_or_ready(self):
        self._send(protocol.encode_ready())
        self._send(protocol.encode_estop())
        self.assertEqual(self.mcu.get_state(), STATE_STOP)
        self.assertFalse(self.mcu.is_estop_latched())

