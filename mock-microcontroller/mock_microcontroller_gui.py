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

from sensor_simulator import SensorSimulator, SENSOR_IDS

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
    def __init__(self, host: str, port: int, on_connected, on_received, on_stopped):
        self.host = host
        self.port = port
        self.on_connected = on_connected
        self.on_received = on_received
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
                    hex_str = data.hex(" ")
                    self.on_received(hex_str)
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
        try:
            if self._server:
                self._server.close()
        except Exception:
            pass
        self._server = None
        self.on_stopped()


class MockApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Mock microcontroller — TCP server")
        self.root.minsize(480, 360)
        self.root.geometry("560x420")
        self.server_thread = None
        self.conn = None
        self.simulator = SensorSimulator(seed=None)
        self._sensor_tick_id: str | None = None
        self.sensor_vars: dict[str, tk.StringVar] = {}

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
        """Advance simulator one tick and refresh sensor display (reschedules itself)."""
        self.simulator.tick()
        snap = self.simulator.get_snapshot()
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
        self._sensor_tick_id = self.root.after(200, self._tick_sensors)

    def log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{ts}] {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def on_start(self):
        host = self.host_var.get().strip() or "0.0.0.0"
        try:
            port = int(self.port_var.get())
        except tk.TclError:
            port = 8080

        def on_connected(conn):
            self.conn = conn
            self.root.after(0, lambda: self.send_btn.configure(state=tk.NORMAL))
            self.root.after(0, lambda: self.log("Client connected."))

        def on_received(hex_str):
            self.root.after(0, lambda h=hex_str: self.log(f"Received: {h}"))

        def on_stopped():
            self.conn = None
            self.root.after(0, self._on_stopped)

        self.server_thread = ServerThread(host, port, on_connected, on_received, on_stopped)
        self.server_thread.start()
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
        self.on_stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    app = MockApp()
    app.run()


if __name__ == "__main__":
    main()
