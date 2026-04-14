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

from sensor_simulator import SENSOR_IDS, SensorSimulator

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
DEFAULT_POLL_HZ = 3.0
RECV_TIMEOUT = 0.1


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
