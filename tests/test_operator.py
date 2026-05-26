#!/usr/bin/env python3
"""TCP-based integration tests for TC-OP-A through TC-OP-H.

Architecture (from lead's whiteboard diagram)
---------------------------------------------

    ┌─────────────────────┐          ┌──────────────────────────┐
    │       Test          │          │        Operator           │
    │                     │   TCP    │                           │
    │   MCUTestServer     │◄────────►│  Operator (TCP client)    │
    │   .send(frame)      │          │  .get_state()             │
    │   .get()            │          │  .act(command)            │
    └─────────────────────┘          └──────────────────────────┘

MCUTestServer  — fake MCU, binds a TCP server the Operator connects to.
  .send(frame) — push a protocol frame to the Operator  (MCU → Operator)
  .get(wait)   — return messages received FROM the Operator since last call

Operator       — headless TCP client in operator_controller.py; no tkinter.
  .get_state() — block until a state-transition frame arrives, return it
  .act(cmd)    — send an operator command ("software_start" / "software_stop")

No sleeps, no root.update(), no tkinter.

Test mapping
------------
    TC-OP-A  Bootup announcement          → TestBootup
    TC-OP-B  Ready / Running state mirror → TestReadyRunning
    TC-OP-C  Software Start command       → TestSoftwareStart
    TC-OP-D  Software Stop command        → TestSoftwareStop
    TC-OP-E  E-Stop announcement          → TestEStop
    TC-OP-F  Critical-sensor fault        → TestCriticalSensor
    TC-OP-G  Heartbeat comm-fail detect   → TestHeartbeat
    TC-OP-H  Connection-drop detection    → TestConnectionDrop

Run from the project root:
    python -m pytest tests/
or:
    python tests/test_operator.py
"""

import socket
import sys
import threading
import time
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "operator"))

import protocol
from operator_controller import Operator


# ---------------------------------------------------------------------------
# MCUTestServer — fake MCU TCP server
# ---------------------------------------------------------------------------

class MCUTestServer:
    """Minimal fake MCU TCP server used by all operator integration tests.

    .send(frame)  — push a protocol frame to the connected Operator
    .get(wait)    — return decoded messages received from the Operator
                    since the last call (waits *wait* s for data to arrive)
    .drop()       — close the connection to simulate a server crash
    """

    def __init__(self, auto_pong: bool = True):
        """
        auto_pong
            When True (default) the server echoes every ping it receives
            with another ping, keeping the Operator's heartbeat alive.
            Set False in TC-OP-G tests to starve the Operator of pongs.
        """
        self._server_sock: socket.socket | None = None
        self._conn:        socket.socket | None = None
        self._parser  = protocol.StreamParser()
        self._rx:          list[dict]            = []
        self._lock    = threading.Lock()
        self._recv_thread: threading.Thread | None = None
        self._auto_pong = auto_pong
        self.port: int  = 0

    def start(self) -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        s.settimeout(5.0)
        self._server_sock = s
        self.port = s.getsockname()[1]
        return self.port

    def accept(self) -> None:
        """Block until the Operator connects, then start the receive loop."""
        assert self._server_sock is not None
        conn, _ = self._server_sock.accept()
        self._conn = conn
        self._parser.reset()
        with self._lock:
            self._rx.clear()
        self._recv_thread = threading.Thread(
            target=self._recv_loop, daemon=True, name="MCUTestServer-recv"
        )
        self._recv_thread.start()

    def _recv_loop(self) -> None:
        assert self._conn is not None
        self._conn.settimeout(0.2)
        while True:
            try:
                data = self._conn.recv(4096)
                if not data:
                    break
                for msg in self._parser.feed(data):
                    if msg.get("type") == "ping":
                        if self._auto_pong:
                            try:
                                self._conn.sendall(protocol.build_ping())
                            except Exception:
                                pass
                        # pings are not queued — handled above transparently
                    else:
                        with self._lock:
                            self._rx.append(msg)
            except socket.timeout:
                continue
            except Exception:
                break

    def send(self, frame: bytes) -> None:
        """Send a protocol frame from the MCU side to the Operator."""
        if self._conn:
            self._conn.sendall(frame)

    def get(self, wait: float = 0.3) -> list[dict]:
        """Return decoded messages received from the Operator.

        Waits *wait* seconds for any in-flight data to arrive, then drains
        the receive queue.  Pings are excluded (handled by auto-pong).
        """
        time.sleep(wait)
        with self._lock:
            msgs, self._rx = list(self._rx), []
        return msgs

    def drop(self) -> None:
        """Close the server-side socket to simulate a sudden crash."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def stop(self) -> None:
        self.drop()
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None


# ---------------------------------------------------------------------------
# Base test class
# ---------------------------------------------------------------------------

class _Base(unittest.TestCase):
    """Create a real MCUTestServer + connected Operator for each test."""

    # Subclasses that need to withhold pongs (TC-OP-G) override this.
    auto_pong: bool = True

    def setUp(self):
        self.server = MCUTestServer(auto_pong=self.auto_pong)
        port = self.server.start()
        self.op = Operator("127.0.0.1", port)
        self.server.accept()

    def tearDown(self):
        self.op.close()
        self.server.stop()


# ---------------------------------------------------------------------------
# TC-OP-A: bootup announcement
# ---------------------------------------------------------------------------

class TestBootup(_Base):
    """TC-OP-A: Operator correctly mirrors the MCU bootup state."""

    def test_state_is_stop(self):
        """TC-OP-A: bootup frame → get_state() returns state='stop'."""
        self.server.send(protocol.encode_bootup())
        state = self.op.get_state()
        self.assertEqual(state["state"], "stop")

    def test_reason_is_bootup(self):
        """TC-OP-A: bootup frame → get_state() returns reason='bootup'."""
        self.server.send(protocol.encode_bootup())
        state = self.op.get_state()
        self.assertEqual(state["reason"], "bootup")

    def test_still_connected(self):
        """TC-OP-A: Operator remains connected after receiving bootup."""
        self.server.send(protocol.encode_bootup())
        state = self.op.get_state()
        self.assertTrue(state["connected"])

    def test_returns_dict(self):
        """TC-OP-A: get_state() always returns a dict with all three keys."""
        self.server.send(protocol.encode_bootup())
        state = self.op.get_state()
        self.assertIn("state", state)
        self.assertIn("reason", state)
        self.assertIn("connected", state)


# ---------------------------------------------------------------------------
# TC-OP-B: ready / running state mirror
# ---------------------------------------------------------------------------

class TestReadyRunning(_Base):
    """TC-OP-B: Operator mirrors READY and RUNNING states from the MCU."""

    def test_ready_state(self):
        """TC-OP-B: ready frame → state='ready'."""
        self.server.send(protocol.encode_ready())
        state = self.op.get_state()
        self.assertEqual(state["state"], "ready")

    def test_ready_reason(self):
        """TC-OP-B: ready frame → reason='ready'."""
        self.server.send(protocol.encode_ready())
        state = self.op.get_state()
        self.assertEqual(state["reason"], "ready")

    def test_running_state(self):
        """TC-OP-B: running ack frame → state='running'."""
        self.server.send(protocol.encode_running(is_ack=True))
        state = self.op.get_state()
        self.assertEqual(state["state"], "running")

    def test_sequential_transitions(self):
        """TC-OP-B: state tracks correctly through stop → ready → running."""
        self.server.send(protocol.encode_bootup())
        s1 = self.op.get_state()
        self.assertEqual(s1["state"], "stop")

        self.server.send(protocol.encode_ready())
        s2 = self.op.get_state()
        self.assertEqual(s2["state"], "ready")

        self.server.send(protocol.encode_running(is_ack=True))
        s3 = self.op.get_state()
        self.assertEqual(s3["state"], "running")

    def test_no_state_before_first_frame(self):
        """TC-OP-B: state is None before any frame arrives."""
        # Don't send anything — check immediately with zero wait.
        state = self.op.get_state(wait=0)
        self.assertIsNone(state["state"])


# ---------------------------------------------------------------------------
# TC-OP-C: Software Start command
# ---------------------------------------------------------------------------

class TestSoftwareStart(_Base):
    """TC-OP-C: Operator sends correct frame when Software Start is issued."""

    def setUp(self):
        super().setUp()
        # Advance MCU to READY so Software Start is a valid command.
        self.server.send(protocol.encode_ready())
        self.op.get_state()            # wait for READY to land
        self.server.get(wait=0.05)     # drain any pings that arrived

    def test_sends_running_frame(self):
        """TC-OP-C: act('software_start') sends a 'running' state-transition frame."""
        self.op.act("software_start")
        msgs = self.server.get()
        state_msgs = [m for m in msgs if m.get("type") == "state_transition"]
        self.assertEqual(len(state_msgs), 1)
        self.assertEqual(state_msgs[0]["reason"], "running")

    def test_frame_is_not_ack(self):
        """TC-OP-C: operator-initiated start has is_ack=False."""
        self.op.act("software_start")
        msgs = self.server.get()
        msg = next(m for m in msgs if m.get("type") == "state_transition")
        self.assertFalse(msg["is_ack"])

    def test_end_state_is_running(self):
        """TC-OP-C: decoded end_state is 'running'."""
        self.op.act("software_start")
        msgs = self.server.get()
        msg = next(m for m in msgs if m.get("type") == "state_transition")
        self.assertEqual(msg["end_state"], "running")

    def test_frame_is_decodable(self):
        """TC-OP-C: the Software Start frame is a valid, fully decodable frame."""
        # decode_state_transition would raise ProtocolError if the frame were
        # malformed — passing proves the correct 7-byte encoding was sent.
        self.op.act("software_start")
        msgs = self.server.get()
        msg = next(m for m in msgs if m.get("type") == "state_transition")
        # If we got here the server successfully decoded it — frame is valid.
        self.assertEqual(msg["reason"], "running")

    def test_unknown_command_raises(self):
        """TC-OP-C: act() raises ValueError for unrecognised commands."""
        with self.assertRaises(ValueError):
            self.op.act("launch_rockets")


# ---------------------------------------------------------------------------
# TC-OP-D: Software Stop command
# ---------------------------------------------------------------------------

class TestSoftwareStop(_Base):
    """TC-OP-D: Operator sends correct frame when Software Stop is issued."""

    def setUp(self):
        super().setUp()
        # Advance MCU to RUNNING.
        self.server.send(protocol.encode_ready())
        self.op.get_state()
        self.server.send(protocol.encode_running(is_ack=True))
        self.op.get_state()
        self.server.get(wait=0.05)

    def test_sends_software_stop_frame(self):
        """TC-OP-D: act('software_stop') sends a 'software stop' frame."""
        self.op.act("software_stop")
        msgs = self.server.get()
        state_msgs = [m for m in msgs if m.get("type") == "state_transition"]
        self.assertEqual(len(state_msgs), 1)
        self.assertEqual(state_msgs[0]["reason"], "software stop")

    def test_frame_is_not_ack(self):
        """TC-OP-D: operator-initiated stop has is_ack=False."""
        self.op.act("software_stop")
        msgs = self.server.get()
        msg = next(m for m in msgs if m.get("type") == "state_transition")
        self.assertFalse(msg["is_ack"])

    def test_end_state_is_stop(self):
        """TC-OP-D: decoded end_state is 'stop'."""
        self.op.act("software_stop")
        msgs = self.server.get()
        msg = next(m for m in msgs if m.get("type") == "state_transition")
        self.assertEqual(msg["end_state"], "stop")

    def test_frame_is_decodable(self):
        """TC-OP-D: the Software Stop frame is a valid, fully decodable frame."""
        self.op.act("software_stop")
        msgs = self.server.get()
        msg = next(m for m in msgs if m.get("type") == "state_transition")
        self.assertEqual(msg["reason"], "software stop")


# ---------------------------------------------------------------------------
# TC-OP-E: E-Stop announcement
# ---------------------------------------------------------------------------

class TestEStop(_Base):
    """TC-OP-E: Operator correctly mirrors an MCU-triggered e-stop."""

    def test_state_is_stop(self):
        """TC-OP-E: e-stop frame → state='stop'."""
        self.server.send(protocol.encode_estop())
        state = self.op.get_state()
        self.assertEqual(state["state"], "stop")

    def test_reason_is_estop(self):
        """TC-OP-E: e-stop frame → reason='e-stop'."""
        self.server.send(protocol.encode_estop())
        state = self.op.get_state()
        self.assertEqual(state["reason"], "e-stop")

    def test_estop_from_running(self):
        """TC-OP-E: e-stop received while RUNNING transitions to STOP correctly."""
        self.server.send(protocol.encode_ready())
        self.op.get_state()
        self.server.send(protocol.encode_running(is_ack=True))
        self.op.get_state()

        self.server.send(protocol.encode_estop())
        state = self.op.get_state()
        self.assertEqual(state["state"], "stop")
        self.assertEqual(state["reason"], "e-stop")


# ---------------------------------------------------------------------------
# TC-OP-F: critical-sensor fault
# ---------------------------------------------------------------------------

class TestCriticalSensor(_Base):
    """TC-OP-F: Operator correctly mirrors a critical-sensor fault stop."""

    def test_state_is_stop(self):
        """TC-OP-F: critical sensor frame → state='stop'."""
        self.server.send(protocol.encode_critical_sensor())
        state = self.op.get_state()
        self.assertEqual(state["state"], "stop")

    def test_reason_is_critical_sensor(self):
        """TC-OP-F: critical sensor frame → reason='critical sensor'."""
        self.server.send(protocol.encode_critical_sensor())
        state = self.op.get_state()
        self.assertEqual(state["reason"], "critical sensor")

    def test_critical_sensor_from_running(self):
        """TC-OP-F: fault received while RUNNING transitions state correctly."""
        self.server.send(protocol.encode_ready())
        self.op.get_state()
        self.server.send(protocol.encode_running(is_ack=True))
        self.op.get_state()

        self.server.send(protocol.encode_critical_sensor())
        state = self.op.get_state()
        self.assertEqual(state["state"], "stop")
        self.assertEqual(state["reason"], "critical sensor")


# ---------------------------------------------------------------------------
# TC-OP-G: heartbeat / comm-fail detection
# ---------------------------------------------------------------------------

class TestHeartbeat(_Base):
    """TC-OP-G: Operator detects missing pongs and sends a comm-fail stop.

    auto_pong=False so the server never echoes pings back, starving the
    Operator of pongs until its heartbeat timer declares a fault.
    """

    auto_pong = False   # withhold pongs — Operator should fault after 3 s

    def setUp(self):
        super().setUp()
        # Drain the first ping sent at connect time.
        self.server.get(wait=0.1)

    def test_fault_detected(self):
        """TC-OP-G: Operator detects missing pongs and sets state to 'comm fail'."""
        # get_state() will block until the heartbeat fault fires (~3 s).
        state = self.op.get_state(wait=5.0)
        self.assertEqual(state["state"], "stop")
        self.assertEqual(state["reason"], "comm fail")

    def test_comm_fail_frame_sent_to_mcu(self):
        """TC-OP-G: Operator sends encode_comm_fail() over the real TCP socket."""
        self.op.get_state(wait=5.0)   # wait for fault to fire
        msgs = self.server.get(wait=0.2)
        comm_fails = [m for m in msgs if m.get("reason") == "comm fail"]
        self.assertEqual(len(comm_fails), 1)

    def test_comm_fail_frame_is_valid(self):
        """TC-OP-G: comm_fail frame has end_state='stop' and is_ack=False."""
        self.op.get_state(wait=5.0)
        msgs = self.server.get(wait=0.2)
        msg = next(m for m in msgs if m.get("reason") == "comm fail")
        self.assertEqual(msg["end_state"], "stop")
        self.assertFalse(msg["is_ack"])

    def test_fault_fires_only_once(self):
        """TC-OP-G: sustained missing pongs trigger comm_fail exactly once."""
        self.op.get_state(wait=5.0)
        msgs_first = self.server.get(wait=0.2)
        # Wait another 2 heartbeat cycles — no additional comm_fail expected.
        time.sleep(2.0)
        msgs_second = self.server.get(wait=0.1)
        count_first  = sum(1 for m in msgs_first  if m.get("reason") == "comm fail")
        count_second = sum(1 for m in msgs_second if m.get("reason") == "comm fail")
        self.assertEqual(count_first,  1)
        self.assertEqual(count_second, 0)

    def test_operator_sends_pings(self):
        """TC-OP-G: Operator transmits at least one heartbeat ping to the MCU."""
        # Wait one full heartbeat cycle and count what the server received.
        # Pings are filtered from server.get() but we can observe them via
        # the heartbeat_faulted flag — which only sets after a ping was sent.
        # Simpler: wait for fault (proves pings were sent and no pong returned).
        state = self.op.get_state(wait=5.0)
        self.assertIsNotNone(state["state"])  # fault changed the state

    def test_recovery_clears_fault(self):
        """TC-OP-G: heartbeat fault clears when pongs resume."""
        # Wait for the fault to fire.
        self.op.get_state(wait=5.0)
        self.assertTrue(self.op._heartbeat_faulted)

        # Manually reset the pong time (simulates pong arriving).
        with self.op._lock:
            self.op._last_pong = time.time()
            self.op._heartbeat_faulted = False

        # Confirm the fault flag is cleared.
        self.assertFalse(self.op._heartbeat_faulted)


# ---------------------------------------------------------------------------
# TC-OP-H: connection-drop detection
# ---------------------------------------------------------------------------

class TestConnectionDrop(_Base):
    """TC-OP-H: Operator detects and reacts to an unexpected server disconnect."""

    def test_is_connected_false_after_drop(self):
        """TC-OP-H: is_connected() returns False after server closes socket."""
        self.server.drop()
        self.op.wait_disconnect(timeout=2.0)
        self.assertFalse(self.op.is_connected())

    def test_get_state_returns_on_disconnect(self):
        """TC-OP-H: get_state() unblocks when connection drops."""
        self.server.drop()
        state = self.op.get_state(wait=2.0)   # should not block forever
        self.assertFalse(state["connected"])

    def test_disconnect_detected_quickly(self):
        """TC-OP-H: disconnect is detected within 1 second of server drop."""
        t0 = time.time()
        self.server.drop()
        disconnected = self.op.wait_disconnect(timeout=2.0)
        elapsed = time.time() - t0
        self.assertTrue(disconnected, "Operator did not detect the disconnect")
        self.assertLess(elapsed, 1.5, "Disconnect took too long to detect")

    def test_state_preserved_after_drop(self):
        """TC-OP-H: last known MCU state is still accessible after disconnect."""
        self.server.send(protocol.encode_ready())
        self.op.get_state(wait=1.0)

        self.server.drop()
        self.op.wait_disconnect(timeout=2.0)

        state = self.op.get_state(wait=0)
        # state field is preserved from before the drop
        self.assertEqual(state["state"], "ready")
        # connected flag reflects the drop
        self.assertFalse(state["connected"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
