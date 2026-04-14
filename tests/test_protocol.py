#!/usr/bin/env python3
"""Tests for protocol.encode() and protocol.decode().

Run from the project root:
    python -m pytest tests/
or without pytest:
    python tests/test_protocol.py
"""

import sys
import unittest
from pathlib import Path

# Make sure protocol.py at the project root is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import protocol
from protocol import ProtocolError


# ---------------------------------------------------------------------------
# Round-trip tests  (encode → hex string → decode → original values)
# ---------------------------------------------------------------------------

class TestRoundTrip(unittest.TestCase):

    def _round_trip(self, sensor_type, sensor_number, value):
        """Encode, convert to hex string, decode, and assert values match."""
        frame = protocol.encode(sensor_type, sensor_number, value)
        result = protocol.decode(frame.hex(" "))
        self.assertEqual(result[0], sensor_type)
        self.assertEqual(result[1], sensor_number)
        self.assertAlmostEqual(result[2], value, places=2)

    def test_temperature_sensor_1(self):
        self._round_trip("temperature", 1, 25.0)

    def test_temperature_sensor_2(self):
        self._round_trip("temperature", 2, 0.0)

    def test_temperature_sensor_3_max(self):
        self._round_trip("temperature", 3, 120.0)

    def test_temperature_decimal(self):
        # 36.51 °C — verifies 0.01 °C resolution is preserved
        self._round_trip("temperature", 1, 36.51)

    def test_rpm(self):
        self._round_trip("rpm", 1, 3000.0)

    def test_rpm_zero(self):
        self._round_trip("rpm", 1, 0.0)

    def test_rpm_max(self):
        self._round_trip("rpm", 1, 6000.0)

    def test_voltage_480v_rail(self):
        self._round_trip("voltage", 1, 480.0)

    def test_voltage_5v_rail(self):
        self._round_trip("voltage", 3, 5.0)

    def test_current(self):
        self._round_trip("current", 1, 12.50)

    def test_current_sensor_2(self):
        self._round_trip("current", 2, 0.25)

    def test_flow(self):
        self._round_trip("flow", 1, 50.0)

    def test_decode_without_spaces(self):
        """decode() must accept hex with no spaces."""
        frame = protocol.encode("temperature", 1, 25.0)
        result = protocol.decode(frame.hex())       # no spaces
        self.assertEqual(result[0], "temperature")
        self.assertAlmostEqual(result[2], 25.0, places=2)

    def test_decode_uppercase_hex(self):
        frame = protocol.encode("rpm", 1, 1500.0)
        result = protocol.decode(frame.hex(" ").upper())
        self.assertEqual(result[0], "rpm")

    def test_known_temperature_bytes(self):
        """Verify the exact byte sequence from the protocol spec example.

        From the docs: 25.00 °C, sensor 1 → AA DD 04 10 01 09 C4 DE
        """
        result = protocol.decode("AA DD 04 10 01 09 C4 DE")
        self.assertEqual(result, ("temperature", 1, 25.0))

    def test_encode_returns_bytes(self):
        frame = protocol.encode("voltage", 1, 12.0)
        self.assertIsInstance(frame, bytes)

    def test_decode_returns_tuple(self):
        frame = protocol.encode("flow", 1, 10.0)
        result = protocol.decode(frame.hex())
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)


# ---------------------------------------------------------------------------
# encode() error tests
# ---------------------------------------------------------------------------

class TestEncodeErrors(unittest.TestCase):

    def test_unknown_sensor_type(self):
        with self.assertRaises(ProtocolError):
            protocol.encode("pressure", 1, 100.0)

    def test_empty_sensor_type(self):
        with self.assertRaises(ProtocolError):
            protocol.encode("", 1, 0.0)

    def test_sensor_type_case_insensitive(self):
        """encode() should accept any casing of a valid type name."""
        frame = protocol.encode("Temperature", 1, 25.0)
        result = protocol.decode(frame.hex())
        self.assertEqual(result[0], "temperature")


# ---------------------------------------------------------------------------
# decode() error tests  —  bad hex, wrong structure, bad checksum
# ---------------------------------------------------------------------------

class TestDecodeErrors(unittest.TestCase):

    def test_empty_string(self):
        with self.assertRaises(ProtocolError):
            protocol.decode("")

    def test_whitespace_only(self):
        with self.assertRaises(ProtocolError):
            protocol.decode("   ")

    def test_invalid_hex_characters(self):
        with self.assertRaises(ProtocolError):
            protocol.decode("ZZ ZZ ZZ ZZ ZZ")

    def test_too_short_one_byte(self):
        with self.assertRaises(ProtocolError):
            protocol.decode("AA")

    def test_too_short_four_bytes(self):
        # Need at least 5 bytes: header(2) + length(1) + 1 content + checksum(1)
        with self.assertRaises(ProtocolError):
            protocol.decode("AA DD 01 01")

    def test_wrong_header_first_byte(self):
        with self.assertRaises(ProtocolError):
            protocol.decode("BB DD 01 01 01")

    def test_wrong_header_second_byte(self):
        with self.assertRaises(ProtocolError):
            protocol.decode("AA CC 01 01 01")

    def test_length_too_large(self):
        # Length field says 4 content bytes but only 1 is present (+ header + checksum)
        with self.assertRaises(ProtocolError):
            protocol.decode("AA DD 04 10 01")

    def test_length_too_small(self):
        # Length field says 2 content bytes but a valid sensor message needs 4
        # AA DD 02 10 01 <checksum 0x11> — length matches but payload wrong
        frame = b"\xAA\xDD\x02\x10\x01\x11"
        with self.assertRaises(ProtocolError):
            protocol.decode(frame.hex())

    def test_extra_trailing_bytes(self):
        # One valid message frame followed by a stray byte — total length mismatch
        frame = protocol.encode("temperature", 1, 25.0) + b"\x00"
        with self.assertRaises(ProtocolError):
            protocol.decode(frame.hex())

    def test_bad_checksum(self):
        # Flip the last byte (checksum) of a valid frame
        frame = bytearray(protocol.encode("temperature", 1, 25.0))
        frame[-1] ^= 0xFF
        with self.assertRaises(ProtocolError):
            protocol.decode(bytes(frame).hex())

    def test_bad_checksum_off_by_one(self):
        frame = bytearray(protocol.encode("rpm", 1, 1000.0))
        frame[-1] = (frame[-1] + 1) & 0xFF
        with self.assertRaises(ProtocolError):
            protocol.decode(bytes(frame).hex())

    def test_ping_message_raises(self):
        # Ping is a valid frame but has no sensor payload to decode
        with self.assertRaises(ProtocolError):
            protocol.decode(protocol.build_ping().hex())

    def test_unknown_message_type(self):
        # Craft a frame with type 0xFF (undefined)
        content = bytes([0xFF, 0x01, 0x00, 0x64])
        frame = protocol.build_message(content)
        with self.assertRaises(ProtocolError):
            protocol.decode(frame.hex())

    def test_corrupt_middle_byte(self):
        # Flip a bit in the value bytes — checksum will catch it
        frame = bytearray(protocol.encode("voltage", 1, 120.0))
        frame[4] ^= 0x01   # corrupt first value byte
        with self.assertRaises(ProtocolError):
            protocol.decode(bytes(frame).hex())


# ---------------------------------------------------------------------------
# Checksum correctness
# ---------------------------------------------------------------------------

class TestChecksum(unittest.TestCase):

    def test_spec_example_heartbeat(self):
        """Protocol spec: heartbeat = AA DD 01 01 01."""
        self.assertEqual(protocol.build_ping(), bytes.fromhex("AADD010101"))

    def test_spec_example_temperature(self):
        """Protocol spec: 25.00 °C, sensor 1 = AA DD 04 10 01 09 C4 DE."""
        self.assertEqual(
            protocol.encode("temperature", 1, 25.0),
            bytes.fromhex("AADD04100109C4DE"),
        )

    def test_checksum_wraps_around(self):
        """Checksum is the LOW byte only — values > 0xFF must wrap."""
        # Build a content where sum > 255 and verify checksum is low byte
        content = bytes([0xFF, 0xFF])
        frame = protocol.build_message(content)
        computed = (0xFF + 0xFF) & 0xFF   # 0xFE
        self.assertEqual(frame[-1], computed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
