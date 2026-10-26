"""End-to-end tests for OperatorBackend over a real localhost TCP socket.

A tiny fake MCU stands in for the board / mock microcontroller so no GUI is
needed. Importing send_messages_gui requires PySide6 (see requirements-dev.txt).
"""

import socket
import threading
import time
import unittest

import protocol
from send_messages_gui import OperatorBackend


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class FakeMcu:
    """Accepts one client on an ephemeral port and records what it sends."""

    def __init__(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.conn = None
        self.received = bytearray()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        self.conn, _ = self.server.accept()
        while True:
            try:
                data = self.conn.recv(4096)
            except OSError:
                return
            if not data:
                return
            self.received.extend(data)

    def send(self, data: bytes) -> None:
        self.conn.sendall(data)

    def close(self) -> None:
        # shutdown() before close() so the peer sees EOF even while our recv
        # thread is still blocked on this socket.
        for s in (self.conn, self.server):
            if s is not None:
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                s.close()


class TestOperatorBackend(unittest.TestCase):

    def setUp(self):
        self.mcu = FakeMcu()
        self.backend = OperatorBackend()
        self.logs, self.states, self.sensors = [], [], []
        self.backend.on_log = self.logs.append
        self.backend.on_state_change = lambda state, reason: self.states.append((state, reason))
        self.backend.on_sensor_update = lambda sid, value: self.sensors.append((sid, value))
        self.assertTrue(self.backend.connect("127.0.0.1", self.mcu.port))
        self.assertTrue(_wait_for(lambda: self.mcu.conn is not None))

    def tearDown(self):
        self.backend.disconnect()
        self.mcu.close()

    def test_receives_state_transition(self):
        self.mcu.send(protocol.encode_ready())
        self.assertTrue(_wait_for(lambda: self.states))
        self.assertEqual(self.states[0], ("ready", "ready"))
        self.assertEqual(self.backend.current_mcu_state, "ready")

    def test_receives_sensor_readings(self):
        self.mcu.send(protocol.encode_temperature(2, 36.5) + protocol.encode_voltage(1, 480.0))
        self.assertTrue(_wait_for(lambda: len(self.sensors) == 2))
        self.assertAlmostEqual(self.backend.sensor_state["temp_2"], 36.5, places=2)
        self.assertAlmostEqual(self.backend.sensor_state["voltage_1"], 480.0, places=2)

    def test_software_start_and_stop_send_commands(self):
        self.backend.software_start()
        self.backend.software_stop()
        start, stop = protocol.encode_running(), protocol.encode_software_stop()
        self.assertTrue(_wait_for(lambda: start in self.mcu.received and stop in self.mcu.received))
        self.assertLess(self.mcu.received.find(start), self.mcu.received.find(stop))

    def test_sends_heartbeat(self):
        ping = protocol.build_ping()
        self.assertTrue(_wait_for(lambda: ping in self.mcu.received))

    def test_server_disconnect_is_detected(self):
        self.mcu.close()
        self.assertTrue(_wait_for(lambda: self.backend.sock is None))
        self.assertTrue(any("Connection lost" in line for line in self.logs))


class TestOperatorBackendConnectFailure(unittest.TestCase):

    def test_connect_to_closed_port_returns_false(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        backend = OperatorBackend()
        logs = []
        backend.on_log = logs.append
        self.assertFalse(backend.connect("127.0.0.1", port))
        self.assertIsNone(backend.sock)
        self.assertTrue(any("Connect error" in line for line in logs))

