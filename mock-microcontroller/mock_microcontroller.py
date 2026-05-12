#!/usr/bin/env python3
"""
Mock microcontroller: owns the sensor state (via SensorSimulator) and a
TCP server. Exposes MockMicrocontroller for programmatic use (e.g. the GUI)
and a CLI runner via `python mock_microcontroller.py`.
"""

import socket
import time
import argparse
import select
import threading
from typing import Callable

from sensor_simulator import SENSOR_IDS, SensorSimulator

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
DEFAULT_POLL_HZ = 3.0
RECV_TIMEOUT = 0.1

# System state machine.  Boot → STOP, E-Stop latched.
# Transitions: STOP --(unlatch)--> READY --(hardware_start)--> RUNNING --(latch)--> STOP
STATE_STOP    = 0
STATE_READY   = 1
STATE_RUNNING = 2
STATE_NAMES: dict[int, str] = {
    STATE_STOP:    "STOP",
    STATE_READY:   "READY",
    STATE_RUNNING: "RUNNING",
}

# Reason codes for state transitions. These are the human-readable strings
# emitted to listeners; the GUI maps them to protocol.encode_* functions.
REASON_BOOTUP          = "bootup"
REASON_ESTOP           = "estop"
REASON_SOFTWARE_STOP   = "software_stop"
REASON_CRITICAL_SENSOR = "critical_sensor"
REASON_COMM_FAIL       = "comm_fail"
REASON_READY           = "ready"
REASON_RUNNING         = "running"

# Listener signature: fn(new_state, estop_latched, reason, is_ack) -> None.
# is_ack is True when the transition was driven by an inbound operator command
# (so the MCU echoes the same frame back with the ack tag set); False for
# MCU-originated events (E-stop press, sensor fault, comm fail, bootup).
StateListener = Callable[[int, bool, str, bool], None]

# Operational thresholds enforced by the microcontroller. A reading outside
# its (lo, hi) band is considered an out-of-range fault — independent of the
# simulator's own SensorConfig.min_val/max_val (which describe the nominal
# range used for value generation, not fault detection).
THRESHOLDS: dict[str, tuple[float, float]] = {
    "temp_1":    (0.0,   120.0),
    "temp_2":    (0.0,   120.0),
    "temp_3":    (0.0,   120.0),
    "rpm":       (0.0,  6000.0),
    "voltage_1": (450.0, 510.0),
    "voltage_2": (110.0, 130.0),
    "voltage_3": (4.7,   5.3),
    "current_1": (0.0,   50.0),
    "current_2": (0.0,   50.0),
    "flow_1":    (0.0,  100.0),
}


class MockMicrocontroller:
    """Owns a SensorSimulator and polls it on a daemon thread at poll_hz."""

    def __init__(self, poll_hz: float = DEFAULT_POLL_HZ, simulator: SensorSimulator | None = None):
        self._poll_hz = poll_hz
        if simulator is None:
            self._sim = SensorSimulator(seed=None)
            self._owns_sim = True
        else:
            self._sim = simulator
            self._owns_sim = False
        self._readings: dict[str, float] = self._sim.get_snapshot()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # State machine — owned by the MCU, not the UI.
        # Boot in STOP unlatched: the physical E-stop button is not pressed at
        # power-on, so the latch reflects reality. Latch only goes True when the
        # physical button is pressed (latch_estop).
        self._state: int = STATE_STOP
        self._estop_latched: bool = False
        self._reason: str = REASON_BOOTUP
        self._listeners: list[StateListener] = []

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)
            self._thread = None

    def get_readings(self) -> dict[str, float]:
        with self._lock:
            return dict(self._readings)

    def is_in_range(self, sensor_id: str) -> bool:
        """Return True if the latest reading for *sensor_id* is within THRESHOLDS."""
        lo, hi = THRESHOLDS[sensor_id]
        with self._lock:
            val = self._readings[sensor_id]
        return lo <= val <= hi

    def out_of_range_sensors(self) -> list[str]:
        """Return sensor IDs whose latest readings are outside THRESHOLDS."""
        with self._lock:
            snap = dict(self._readings)
        return [sid for sid in SENSOR_IDS if not THRESHOLDS[sid][0] <= snap[sid] <= THRESHOLDS[sid][1]]

    # ---- State machine -----------------------------------------------------

    def get_state(self) -> int:
        return self._state

    def is_estop_latched(self) -> bool:
        return self._estop_latched

    def get_reason(self) -> str:
        return self._reason

    def add_state_listener(self, fn: StateListener) -> None:
        """Register a callback invoked after every state or latch change."""
        self._listeners.append(fn)

    def _set(self, state: int, latched: bool, reason: str, is_ack: bool = False) -> bool:
        """Apply a (state, latched, reason) update; notify listeners.

        Fires listeners whenever state, latch, or reason changes — a re-entry
        with a new reason (e.g. comm_fail vs estop, both landing in STOP) is
        a meaningful event the operator needs to see.
        """
        if (state == self._state and latched == self._estop_latched
                and reason == self._reason and not is_ack):
            return False
        self._state = state
        self._estop_latched = latched
        self._reason = reason
        for fn in self._listeners:
            fn(state, latched, reason, is_ack)
        return True

    def bootup(self) -> bool:
        """Emit a bootup event. Boot is STOP + unlatched (the E-stop button isn't
        physically pressed at power-on). Always fires listeners — a newly-connected
        operator needs the initial frame."""
        self._state = STATE_STOP
        self._estop_latched = False
        self._reason = REASON_BOOTUP
        for fn in self._listeners:
            fn(STATE_STOP, False, REASON_BOOTUP, False)
        return True

    def unlatch_estop(self) -> bool:
        """Clear the E-stop latch. Stays in STOP — operator must still hit hardware_start
        to arm to READY. This is action 1 of the 2-action recovery."""
        if self._state == STATE_STOP and self._estop_latched:
            return self._set(STATE_STOP, False, self._reason)
        return False

    def latch_estop(self, reason: str = REASON_ESTOP) -> bool:
        """Physical E-stop press: any state → STOP + latched.
        Only the physical button drives the latch — software/sensor/comm stops
        go to STOP but preserve whatever the latch currently is."""
        return self._set(STATE_STOP, True, reason)

    def hardware_start(self) -> bool:
        """STOP + unlatched → READY. Action 2 of the 2-action recovery."""
        if self._state == STATE_STOP and not self._estop_latched:
            return self._set(STATE_READY, False, REASON_READY)
        return False

    def software_start(self, is_ack: bool = True) -> bool:
        """Operator command: READY → RUNNING. Rejected outside READY."""
        if self._state == STATE_READY and not self._estop_latched:
            return self._set(STATE_RUNNING, False, REASON_RUNNING, is_ack=is_ack)
        return False

    def software_stop(self, is_ack: bool = True) -> bool:
        """Operator command: READY/RUNNING → STOP. Latch is preserved — software
        stops don't simulate a physical E-stop press."""
        if self._state in (STATE_READY, STATE_RUNNING):
            return self._set(STATE_STOP, self._estop_latched, REASON_SOFTWARE_STOP, is_ack=is_ack)
        return False

    def critical_sensor_fault(self) -> bool:
        """Internal trigger: any non-STOP → STOP, reason critical_sensor. Latch preserved."""
        if self._state != STATE_STOP:
            return self._set(STATE_STOP, self._estop_latched, REASON_CRITICAL_SENSOR)
        return False

    def comm_fail(self, is_ack: bool = False) -> bool:
        """Any non-STOP → STOP, reason comm_fail. Latch preserved.

        is_ack=True is used when the operator commanded the comm-fail stop
        (operator detected no pongs); is_ack=False when the MCU itself
        detected loss (no pings inbound).
        """
        if self._state != STATE_STOP:
            return self._set(STATE_STOP, self._estop_latched, REASON_COMM_FAIL, is_ack=is_ack)
        return False

    def _run(self) -> None:
        period = 1.0 / self._poll_hz
        while not self._stop.is_set():
            if self._owns_sim:
                self._sim.tick()
            snap = self._sim.get_snapshot()
            with self._lock:
                self._readings = snap
            self._stop.wait(timeout=period)


running = True


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_hex(line: str) -> bytes | None:
    line = line.strip().replace(" ", "").replace(",", "")
    if not line:
        return None
    try:
        return bytes.fromhex(line)
    except ValueError:
        return None


def receive_loop(conn: socket.socket) -> None:
    global running
    conn.settimeout(RECV_TIMEOUT)
    while running:
        try:
            ready = select.select([conn], [], [], RECV_TIMEOUT)
            if ready[0]:
                data = conn.recv(4096)
                if not data:
                    log("Connection closed by peer")
                    break
                log(f"<<< {data.hex(' ')}")
                print("You: ", end="", flush=True)
        except (ConnectionResetError, BrokenPipeError, OSError) as e:
            if running:
                log(f"Receive: {e}")
            break
        except Exception as e:
            if running:
                log(f"Receive error: {e}")
            break


def serve_client(conn: socket.socket, addr: tuple, mcu: MockMicrocontroller) -> bool:
    """Handle one client. Returns True to quit the program, False to accept next client."""
    global running
    running = True
    log(f"Client connected from {addr}")
    threading.Thread(target=receive_loop, args=(conn,), daemon=True).start()

    print("Type hex to send (e.g. AA DD 01). 'sensors' = show readings. quit = exit program.\n")
    try:
        while running:
            try:
                line = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                running = False
                conn.close()
                return True
            if not line:
                continue
            if line.lower() in ("quit", "exit", "q"):
                running = False
                conn.close()
                log("Quit.")
                return True
            if line.lower() == "sensors":
                snap = mcu.get_readings()
                parts = [f"{sid}={snap.get(sid, 0):.2f}" for sid in SENSOR_IDS]
                log("sensors: " + ", ".join(parts))
                continue
            raw = parse_hex(line)
            if raw is None:
                print("Invalid hex. Use e.g. AA DD 01 or AADD01")
                continue
            try:
                conn.sendall(raw)
                log(f">>> {raw.hex(' ')}")
            except Exception as e:
                log(f"Send error: {e}")
                break
    finally:
        running = False
        conn.close()
        log("Disconnected.")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock TCP server (send/receive hex bytes)")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Bind host (default {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Bind port (default {DEFAULT_PORT})")
    parser.add_argument("--poll-hz", type=float, default=DEFAULT_POLL_HZ, help=f"Sensor poll rate in Hz (default {DEFAULT_POLL_HZ})")
    args = parser.parse_args()

    mcu = MockMicrocontroller(poll_hz=args.poll_hz)
    mcu.start()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((args.host, args.port))
        server.listen(1)
        log(f"Listening on {args.host}:{args.port}. Polling sensors at {args.poll_hz} Hz. quit or Ctrl+C to exit.")
        while True:
            try:
                conn, addr = server.accept()
            except KeyboardInterrupt:
                break
            if serve_client(conn, addr, mcu):
                break
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        mcu.stop()
        log("Stopped.")


if __name__ == "__main__":
    main()
