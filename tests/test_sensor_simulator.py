"""Tests for SensorSimulator and the MCU's threshold checks on its readings."""

import time
import unittest

from mock_microcontroller import THRESHOLDS, MockMicrocontroller
from sensor_simulator import DEFAULT_CONFIGS, SENSOR_IDS, SensorSimulator


class TestSimulatorDynamics(unittest.TestCase):

    def test_same_seed_is_reproducible(self):
        a, b = SensorSimulator(seed=42), SensorSimulator(seed=42)
        for _ in range(50):
            a.tick()
            b.tick()
        self.assertEqual(a.get_snapshot(), b.get_snapshot())

    def test_converges_to_setpoint_without_noise(self):
        sim = SensorSimulator(seed=0)
        sim.set_noise("temp_1", 0.0)
        sim.set_step_size("temp_1", 0.5)
        sim.set_setpoint("temp_1", 90.0)
        for _ in range(60):
            sim.tick()
        self.assertAlmostEqual(sim.get_value("temp_1"), 90.0, places=6)

    def test_clamp_is_enforced(self):
        sim = SensorSimulator(seed=0)
        sim.set_clamp("rpm", 0.0, 1000.0)
        sim.configure_many(["rpm"], setpoint=5000.0, step_size=1.0, noise=0.0)
        sim.tick()
        self.assertEqual(sim.get_value("rpm"), 1000.0)

    def test_tick_count_and_reset(self):
        sim = SensorSimulator(seed=0)
        sim.tick()
        sim.tick()
        self.assertEqual(sim.get_tick_count(), 2)
        sim.reset()
        self.assertEqual(sim.get_tick_count(), 0)
        self.assertEqual(sim.get_value("flow_1"), sim.config("flow_1").setpoint)

    def test_instances_do_not_share_or_mutate_default_configs(self):
        original = DEFAULT_CONFIGS["temp_1"].setpoint
        sim = SensorSimulator(seed=0)
        sim.set_setpoint("temp_1", 999.0)
        self.assertEqual(DEFAULT_CONFIGS["temp_1"].setpoint, original)
        self.assertEqual(SensorSimulator(seed=0).config("temp_1").setpoint, original)


class TestSimulatorValidation(unittest.TestCase):

    def setUp(self):
        self.sim = SensorSimulator(seed=0)

    def test_step_size_out_of_range(self):
        for bad in (-0.1, 1.1):
            with self.assertRaises(ValueError):
                self.sim.set_step_size("temp_1", bad)

    def test_negative_noise(self):
        with self.assertRaises(ValueError):
            self.sim.set_noise("temp_1", -1.0)

    def test_clamp_lo_must_be_below_hi(self):
        with self.assertRaises(ValueError):
            self.sim.set_clamp("temp_1", 10.0, 10.0)

    def test_clamp_needs_both_bounds(self):
        with self.assertRaises(ValueError):
            self.sim.set_clamp("temp_1", 0.0, None)


class TestMcuThresholds(unittest.TestCase):

    def test_defaults_start_inside_thresholds(self):
        """A freshly booted system must not immediately trip a critical-sensor stop."""
        mcu = MockMicrocontroller(simulator=SensorSimulator(seed=0))
        self.assertEqual(mcu.out_of_range_sensors(), [])

    def test_out_of_range_sensor_is_detected(self):
        sim = SensorSimulator(seed=0)
        sim.configure_many(["voltage_1"], setpoint=600.0, step_size=1.0, noise=0.0)
        sim.tick()
        mcu = MockMicrocontroller(simulator=sim)
        self.assertEqual(mcu.out_of_range_sensors(), ["voltage_1"])
        self.assertFalse(mcu.is_in_range("voltage_1"))
        self.assertTrue(mcu.is_in_range("voltage_2"))

    def test_threshold_bounds_are_inclusive(self):
        sim = SensorSimulator(seed=0)
        lo, hi = THRESHOLDS["temp_2"]
        sim.configure_many(["temp_2"], setpoint=hi, step_size=1.0, noise=0.0)
        sim.tick()
        self.assertTrue(MockMicrocontroller(simulator=sim).is_in_range("temp_2"))

    def test_polling_thread_picks_up_new_readings(self):
        sim = SensorSimulator(seed=0)
        mcu = MockMicrocontroller(poll_hz=200.0, simulator=sim)
        sim.configure_many(["flow_1"], setpoint=500.0, step_size=1.0, noise=0.0)
        sim.tick()
        mcu.start()
        try:
            for _ in range(100):
                if mcu.get_readings()["flow_1"] == 500.0:
                    break
                time.sleep(0.01)
            self.assertEqual(mcu.get_readings()["flow_1"], 500.0)
        finally:
            mcu.stop()

