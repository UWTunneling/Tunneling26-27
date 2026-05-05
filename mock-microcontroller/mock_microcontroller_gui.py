#!/usr/bin/env python3
"""
Mock TCP server with simple tkinter UI: log sent/received, send hex bytes.
No extra deps (uses standard library tkinter).
"""

import socket
import time
import threading
import tkinter as tk
from tkinter import ttk
from pathlib import Path
import sys

# Allow importing protocol.py from the project root regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import protocol

from sensor_simulator import SENSOR_IDS, SensorSimulator
from mock_microcontroller import MockMicrocontroller, THRESHOLDS

# Display label and unit for each sensor (for GUI)
SENSOR_LABELS: dict[str, tuple[str, str]] = {
    "temp_1": ("Temp 1", "°C"),
    "temp_2": ("Temp 2", "°C"),
    "temp_3": ("Temp 3", "°C"),
    "rpm": ("RPM", ""),
    "voltage_1": ("480 V", "V"),
    "voltage_2": ("120 V", "V"),
    "voltage_3": ("5 V", "V"),
    "current_1": ("Current 1", "A"),
    "current_2": ("Current 2", "A"),
    "flow_1": ("Flow 1", "L/min"),
}


def parse_hex(line: str) -> bytes | None:
    line = line.strip().replace(" ", "").replace(",", "")
    if not line:
        return None
    try:
        return bytes.fromhex(line)
    except ValueError:
        return None


class ServerThread:
    def __init__(self, host: str, port: int, on_connected, on_received,
                 on_client_disconnected, on_stopped):
        self.host = host
        self.port = port
        self.on_connected = on_connected
        self.on_received = on_received
        self.on_client_disconnected = on_client_disconnected
        self.on_stopped = on_stopped
        self._running = True
        self._conn = None
        self._server = None
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass
            self._server = None
        if self._thread:
            self._thread.join(timeout=1.5)

    def _run(self):
        try:
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server.bind((self.host, self.port))
            self._server.listen(1)
            self._server.settimeout(0.5)
        except Exception:
            self.on_stopped()
            return
        while self._running:
            try:
                conn, addr = self._server.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            self._conn = conn
            self.on_connected(conn)
            conn.settimeout(0.2)
            while self._running and self._conn:
                try:
                    data = conn.recv(4096)
                    if not data:
                        break
                    self.on_received(data)

                except socket.timeout:
                    continue
                except (OSError, ConnectionResetError, BrokenPipeError):
                    break
                except Exception:
                    break
            try:
                conn.close()
            except Exception:
                pass
            self._conn = None
            # Only fire the callback when the client dropped; not when we
            # deliberately stopped the server (self._running will be False then).
            if self._running:
                self.on_client_disconnected()
        try:
            if self._server:
                self._server.close()
        except Exception:
            pass
        self._server = None
        self.on_stopped()


class MockApp:
    def __init__(self, root: tk.Misc | None = None, simulator: SensorSimulator | None = None):
        self.root = root if root is not None else tk.Tk()
        self.root.title("Mock microcontroller — TCP server")
        self.root.minsize(480, 360)
        self.root.geometry("560x420")
        self.server_thread = None
        self.conn = None
        self.mcu = MockMicrocontroller(simulator=simulator)
        self.mcu.start()
        self._sensor_tick_id: str | None = None
        self._health_check_id: str | None = None
        self.sensor_vars: dict[str, tk.StringVar] = {}
        self._stream_parser = protocol.StreamParser()
        self._last_rx_time: float | None = None
        self._connection_healthy: bool = True
        self.respondAck = True
        self.ack_count = 0

        # Track which sensors are faulted to log transitions
        self._sensor_faulted: dict[str, bool] = {sid: False for sid in SENSOR_IDS}
        # Ping-tracking — independent of the 2 Hz sensor-receive path; used
        # to detect the operator going silent for 3 heartbeat cycles.
        self._last_ping_time: float | None = None
        self._heartbeat_healthy: bool = True
        self._heartbeat_check_id: str | None = None

        # Server
        srv = ttk.LabelFrame(self.root, text="Server", padding=6)
        srv.pack(fill=tk.X, padx=6, pady=6)
        row = tk.Frame(srv)
        row.pack(fill=tk.X)
        ttk.Label(row, text="Bind:").pack(side=tk.LEFT, padx=(0, 4))
        self.host_var = tk.StringVar(value="0.0.0.0")
        self.host_edit = ttk.Entry(row, textvariable=self.host_var, width=14)
        self.host_edit.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(row, text="Port:").pack(side=tk.LEFT, padx=(0, 4))
        self.port_var = tk.IntVar(value=8080)
        self.port_spin = ttk.Spinbox(row, from_=1, to=65535, width=6, textvariable=self.port_var)
        self.port_spin.pack(side=tk.LEFT, padx=(0, 12))
        self.start_btn = ttk.Button(row, text="Start", command=self.on_start)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.stop_btn = ttk.Button(row, text="Stop", command=self.on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT)

        # Ack toggle — label reflects current state.
        self.toggle_btn = ttk.Button(row, text="Ack On", command=self.toggle_ack)
        self.toggle_btn.pack(side=tk.LEFT, padx=(0, 6))

        # Sensors (random state simulator) — 3 columns to use horizontal space
        sens_f = ttk.LabelFrame(self.root, text="Sensors", padding=6)
        sens_f.pack(fill=tk.X, padx=6, pady=6)
        for i, sid in enumerate(SENSOR_IDS):
            row, col = i // 3, i % 3
            cell = ttk.Frame(sens_f)
            cell.grid(row=row, column=col, sticky=tk.NW, padx=(0, 14), pady=2)
            label_text, unit = SENSOR_LABELS.get(sid, (sid, ""))
            ttk.Label(cell, text=f"{label_text}:").pack(anchor=tk.W)
            var = tk.StringVar(value="—")
            self.sensor_vars[sid] = var
            val_row = ttk.Frame(cell)
            val_row.pack(anchor=tk.W)
            ttk.Label(val_row, textvariable=var, width=10).pack(side=tk.LEFT)
            if unit:
                ttk.Label(val_row, text=unit).pack(side=tk.LEFT, padx=(2, 0))

        # Log
        ttk.Label(self.root, text="Log (sent / received):").pack(anchor=tk.W, padx=6, pady=(6, 0))
        log_frame = tk.Frame(self.root)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, state=tk.DISABLED, height=12)
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Send
        send_row = tk.Frame(self.root)
        send_row.pack(fill=tk.X, padx=6, pady=6)
        ttk.Label(send_row, text="Hex bytes:").pack(side=tk.LEFT, padx=(0, 6))
        self.hex_var = tk.StringVar()
        self.hex_edit = ttk.Entry(send_row, textvariable=self.hex_var, width=30)
        self.hex_edit.pack(side=tk.LEFT, padx=(0, 6))
        self.hex_edit.bind("<Return>", lambda e: self.send_hex())
        self.send_btn = ttk.Button(send_row, text="Send", command=self.send_hex, state=tk.DISABLED)
        self.send_btn.pack(side=tk.LEFT)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._tick_sensors()

    def _tick_sensors(self) -> None:
        """Refresh sensor display from the mock microcontroller (reschedules itself)."""
        snap = self.mcu.get_readings()
        for sid in SENSOR_IDS:
            val = snap.get(sid, 0)
            _, unit = SENSOR_LABELS.get(sid, (sid, ""))
            if sid == "rpm":
                text = f"{val:.0f}"
            elif unit == "°C":
                text = f"{val:.1f}"
            else:
                text = f"{val:.2f}"
            self.sensor_vars[sid].set(text)

        # Threshold alerts (logs on error and recovery)
        for sid in SENSOR_IDS:
            val = snap.get(sid, 0.0)
            out = not self.mcu.is_in_range(sid)
            was_faulted = self._sensor_faulted[sid]

            if out and not was_faulted:
                label, unit = SENSOR_LABELS.get(sid, (sid, ""))
                unit_str = f" {unit}" if unit else ""
                lo, hi = THRESHOLDS[sid]
                self.log(
                    f"THRESHOLD EXCEEDED — {label}: "
                    f"{val:.3g}{unit_str} "
                    f"(nominal {lo:.3g}–{hi:.3g}{unit_str})"
                )
                self._sensor_faulted[sid] = True

            elif not out and was_faulted:
                label, _ = SENSOR_LABELS.get(sid, (sid, ""))
                self.log(f"RECOVERED — {label}: back in range ({val:.3g})")
                self._sensor_faulted[sid] = False


        # Send all sensor readings to the connected operator.
        if self.conn:
            payload = b"".join([
                protocol.encode_temperature(1, snap["temp_1"]),
                protocol.encode_temperature(2, snap["temp_2"]),
                protocol.encode_temperature(3, snap["temp_3"]),
                protocol.encode_rpm(1, snap["rpm"]),
                protocol.encode_voltage(1, snap["voltage_1"]),
                protocol.encode_voltage(2, snap["voltage_2"]),
                protocol.encode_voltage(3, snap["voltage_3"]),
                protocol.encode_current(1, snap["current_1"]),
                protocol.encode_current(2, snap["current_2"]),
                protocol.encode_flow(1, snap["flow_1"]),
            ])
            self._send_bytes(payload)

        self._sensor_tick_id = self.root.after(500, self._tick_sensors)

    def _send_bytes(self, data: bytes) -> None:
        """Send raw bytes to the connected client (must be called from the main thread)."""
        if not self.conn:
            return
        try:
            self.conn.sendall(data)
        except Exception as e:
            self.log(f"Send error: {e}")
            self.conn = None
            self.send_btn.configure(state=tk.DISABLED)

    def _check_connection_health(self) -> None:
        """Warn once when no data has been received for >5 s; clear warning when it recovers."""
        if self.conn and self._last_rx_time is not None:
            age = time.time() - self._last_rx_time
            if age > 5.0 and self._connection_healthy:
                self._connection_healthy = False
                self.log(f"No data from client for {age:.0f} s — connection may be lost.")
            elif age <= 5.0 and not self._connection_healthy:
                self._connection_healthy = True
                self.log("Connection health restored.")
        self._health_check_id = self.root.after(2000, self._check_connection_health)

    def _check_heartbeat_health(self) -> None:
        """Fault once when no ping has arrived for >3 s (3 heartbeat cycles).

        Heartbeat cycle is 1 Hz on the operator side, so 3 cycles = 3 s.
        """
        if self.conn and self._last_ping_time is not None:
            age = time.time() - self._last_ping_time
            if age > 3.0 and self._heartbeat_healthy:
                self._heartbeat_healthy = False
                self.log(f"HEARTBEAT FAULT: no ping from operator for {age:.1f} s.")
            elif age <= 3.0 and not self._heartbeat_healthy:
                self._heartbeat_healthy = True
                self.log("Heartbeat recovered.")
        self._heartbeat_check_id = self.root.after(1000, self._check_heartbeat_health)

    def log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{ts}] {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def send(self, text: str):
            if not self.conn:
                return
            try:
                self.conn.sendall(text.encode("ascii"))
                self.log(f"Sent: {text}")
            except Exception as e:
                self.log(f"Send error: {e}")
                self.conn = None

    def on_start(self):
        host = self.host_var.get().strip() or "0.0.0.0"
        try:
            port = int(self.port_var.get())
        except tk.TclError:
            port = 8080

        def on_connected(conn):
            self.conn = conn
            self._stream_parser.reset()
            self._last_rx_time = None
            self._connection_healthy = True
            # Seed ping-tracking so the 3 s counter starts now, not from
            # whatever stale time the previous client left behind.
            self._last_ping_time = time.time()
            self._heartbeat_healthy = True
            self.root.after(0, lambda: self.send_btn.configure(state=tk.NORMAL))
            self.root.after(0, lambda: self.log("Client connected."))

        def on_received(data: bytes):
            # Parse on the server thread; dispatch decoded results to main thread.
            msgs = self._stream_parser.feed(data)
            hex_str = data.hex(" ")
            self._last_rx_time = time.time()

            def update(h=hex_str, m=msgs):
                self.log(f"Recv ({len(h.replace(' ', '')) // 2} B): {h}")
                for msg in m:
                    desc = protocol.describe(msg)
                    if msg.get("type") == "ping":
                        self._last_ping_time = time.time()
                        if self.respondAck:
                            self._send_bytes(protocol.build_ping())
                            self.log(f"  → {desc}  (pong sent)")
                        else:
                            self.log(f"  → {desc}  (ack off, no pong)")
                    else:
                        self.log(f"  → {desc}")
            self.root.after(0, update)

        def on_client_disconnected():
            def update():
                self.conn = None
                self._last_ping_time = None
                self._heartbeat_healthy = True
                self.send_btn.configure(state=tk.DISABLED)
                self.log("Client disconnected.")
            self.root.after(0, update)

        def on_stopped():
            self.conn = None
            self.root.after(0, self._on_stopped)

        self.server_thread = ServerThread(
            host, port, on_connected, on_received, on_client_disconnected, on_stopped
        )
        self.server_thread.start()
        self._check_connection_health()
        self._check_heartbeat_health()
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.host_edit.configure(state=tk.DISABLED)
        self.port_spin.configure(state=tk.DISABLED)
        self.log(f"Listening on {host}:{port}.")

    def _on_stopped(self):
        self.conn = None
        self.send_btn.configure(state=tk.DISABLED)
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self.host_edit.configure(state=tk.NORMAL)
        self.port_spin.configure(state=tk.NORMAL)
        self.server_thread = None
        self.log("Server stopped.")

    def on_stop(self):
        if self.server_thread:
            self.server_thread.stop()
            self.server_thread = None
        self.conn = None
        self.send_btn.configure(state=tk.DISABLED)
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self.host_edit.configure(state=tk.NORMAL)
        self.port_spin.configure(state=tk.NORMAL)
        self.log("Server stopped.")

    def send_hex(self):
        if not self.conn:
            return
        raw = parse_hex(self.hex_var.get())
        if raw is None:
            self.log("Invalid hex.")
            return
        try:
            self.conn.sendall(raw)
            self.log(f"Sent: {raw.hex(' ')}")
            self.hex_var.set("")
        except Exception as e:
            self.log(f"Send error: {e}")
            self.conn = None
            self.send_btn.configure(state=tk.DISABLED)

    def on_close(self):
        if self._sensor_tick_id is not None:
            self.root.after_cancel(self._sensor_tick_id)
            self._sensor_tick_id = None
        if self._health_check_id is not None:
            self.root.after_cancel(self._health_check_id)
            self._health_check_id = None
        if self._heartbeat_check_id is not None:
            self.root.after_cancel(self._heartbeat_check_id)
            self._heartbeat_check_id = None
        self.on_stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()

    def toggle_ack(self):
        self.ack_count = 0
        self.respondAck = not self.respondAck
        if self.respondAck:
            self.toggle_btn.config(text="Ack On")
        else:
            self.toggle_btn.config(text="Ack Off")

def main():
    app = MockApp()
    app.run()


if __name__ == "__main__":
    main()
