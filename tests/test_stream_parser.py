"""Tests for protocol.StreamParser — reassembling frames from a TCP byte stream.

Both the operator and the mock MCU read every inbound byte through this
class, so a regression here silently drops sensor readings or commands.
"""

import unittest

import protocol


class TestStreamParserChunking(unittest.TestCase):

    def setUp(self):
        self.parser = protocol.StreamParser()

    def test_single_complete_frame(self):
        msgs = self.parser.feed(protocol.build_ping())
        self.assertEqual(msgs, [{"type": "ping"}])

    def test_multiple_frames_in_one_chunk(self):
        data = (
            protocol.build_ping()
            + protocol.encode_temperature(1, 25.0)
            + protocol.encode_ready()
        )
        msgs = self.parser.feed(data)
        self.assertEqual([m["type"] for m in msgs], ["ping", "temperature", "state_transition"])

    def test_frame_split_across_every_byte(self):
        """TCP may deliver one byte at a time; the frame must still arrive once."""
        frame = protocol.encode_voltage(1, 480.0)
        msgs = []
        for i in range(len(frame)):
            msgs.extend(self.parser.feed(frame[i:i + 1]))
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["type"], "voltage")
        self.assertAlmostEqual(msgs[0]["value"], 480.0, places=2)

    def test_split_between_header_bytes(self):
        self.assertEqual(self.parser.feed(b"\xAA"), [])
        self.assertEqual(self.parser.feed(b"\xDD\x01\x01\x01"), [{"type": "ping"}])

    def test_incomplete_frame_waits_for_more(self):
        frame = protocol.encode_rpm(1, 3000)
        self.assertEqual(self.parser.feed(frame[:-1]), [])
        self.assertEqual(len(self.parser.feed(frame[-1:])), 1)


class TestStreamParserResync(unittest.TestCase):

    def setUp(self):
        self.parser = protocol.StreamParser()

    def test_garbage_before_header_is_discarded(self):
        msgs = self.parser.feed(b"\x00\x13\x37" + protocol.build_ping())
        self.assertEqual(msgs, [{"type": "ping"}])

    def test_bad_checksum_frame_is_dropped_and_next_frame_survives(self):
        bad = bytearray(protocol.encode_temperature(1, 25.0))
        bad[-1] ^= 0xFF
        msgs = self.parser.feed(bytes(bad) + protocol.encode_estop())
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["reason"], "e-stop")

    def test_reset_discards_partial_frame(self):
        self.parser.feed(protocol.encode_flow(1, 10.0)[:4])
        self.parser.reset()
        self.assertEqual(self.parser.feed(protocol.build_ping()), [{"type": "ping"}])

    def test_unknown_message_type_is_reported_not_dropped(self):
        frame = protocol.build_message(bytes([0x7F, 0x01]))
        msgs = self.parser.feed(frame)
        self.assertEqual(msgs[0]["type"], "unknown")


class TestStreamParserDecodedFields(unittest.TestCase):
    """The dict shape is what the operator UI and MCU command handler key off."""

    def test_sensor_dict_fields(self):
        msg = protocol.StreamParser().feed(protocol.encode_current(2, 12.5))[0]
        self.assertEqual(msg["type"], "current")
        self.assertEqual(msg["sensor_id"], 2)
        self.assertAlmostEqual(msg["value"], 12.5, places=2)
        self.assertEqual(msg["unit"], "A")

    def test_state_transition_ack_flag(self):
        msg = protocol.StreamParser().feed(protocol.encode_running(is_ack=True))[0]
        self.assertEqual(msg["reason"], "running")
        self.assertEqual(msg["end_state"], "running")
        self.assertTrue(msg["is_ack"])

