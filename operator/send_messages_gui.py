#!/usr/bin/env python3
"""
TCP client with simple tkinter UI: log sent/received, send hex bytes.
No extra deps (uses standard library tkinter).
"""

import socket
import time
import threading
import tkinter as tk
from tkinter import ttk
from pathlib import Path
import sys


from PySide6.QtWidgets import QApplication, QMainWindow, QWidget
from PySide6.QtUiTools import QUiLoader
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtCore import Qt, QRectF, QFile, QPointF
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout





# Allow importing protocol.py from the project root regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import protocol



SENSOR_IDS: tuple[str, ...] = (
    "temp_1", "temp_2", "temp_3",
    "rpm",
    "voltage_1", "voltage_2", "voltage_3",
    "current_1", "current_2",
    "flow_1",
)

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


def _sensor_key(msg_type: str, sensor_id: int) -> str | None:
    """Map a decoded sensor message to the canonical sensor-id string."""
    if msg_type == "temperature":
        return f"temp_{sensor_id}"
    if msg_type == "rpm":
        return "rpm"
    if msg_type in ("voltage", "current", "flow"):
        return f"{msg_type}_{sensor_id}"
    return None


def parse_hex(line: str) -> bytes | None:
    line = line.strip().replace(" ", "").replace(",", "")
    if not line:
        return None
    try:
        return bytes.fromhex(line)
    except ValueError:
        return None


class OperatorApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Operator — TCP client")
        self.root.minsize(480, 360)
        self.root.geometry("560x420")
        self.sock = None
        self.recv_running = False
        self.recv_thread = None
        # Heartbeat toggle: when True, _send_heartbeat emits a ping every 1 s.
        self.heartbeat = True
        self._stream_parser = protocol.StreamParser()
        self._last_rx_time: float | None = None
        self._connection_healthy: bool = True
        self._heartbeat_id: str | None = None
        self._health_check_id: str | None = None
        # Pong tracking — independent of the sensor stream.
        self._last_pong_time: float | None = None
        self._heartbeat_healthy: bool = True
        self._heartbeat_check_id: str | None = None

        # Decoded sensor state — updated as frames arrive from the microcontroller.
        self.sensor_state: dict[str, float | None] = {sid: None for sid in SENSOR_IDS}
        self.sensor_vars: dict[str, tk.StringVar] = {}

        # MCU state mirror — populated from inbound state_transition frames.
        # "—" until the first frame (typically the MCU's bootup-on-connect).
        self.current_mcu_state: str | None = None
        self.current_reason: str | None = None

        # Connection
        conn = ttk.LabelFrame(self.root, text="Connection", padding=6)
        conn.pack(fill=tk.X, padx=6, pady=6)
        row = tk.Frame(conn)
        row.pack(fill=tk.X)
        ttk.Label(row, text="Host:").pack(side=tk.LEFT, padx=(0, 4))
        self.host_var = tk.StringVar(value="127.0.0.1")
        self.host_edit = ttk.Entry(row, textvariable=self.host_var, width=14)
        self.host_edit.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(row, text="Port:").pack(side=tk.LEFT, padx=(0, 4))
        self.port_var = tk.IntVar(value=8080)
        self.port_spin = ttk.Spinbox(row, from_=1, to=65535, width=6, textvariable=self.port_var)
        self.port_spin.pack(side=tk.LEFT, padx=(0, 12))
        self.connect_btn = ttk.Button(row, text="Connect", command=self.on_connect)
        self.connect_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.disconnect_btn = ttk.Button(row, text="Disconnect", command=self.on_disconnect, state=tk.DISABLED)
        self.disconnect_btn.pack(side=tk.LEFT)

        # State — labels + Software Start/Stop. Populated from inbound state_transition frames.
        state_f = ttk.LabelFrame(self.root, text="State", padding=6)
        state_f.pack(fill=tk.X, padx=6, pady=(0, 6))
        state_row = tk.Frame(state_f)
        state_row.pack(fill=tk.X)
        bold = ("TkDefaultFont", 10, "bold")
        ttk.Label(state_row, text="State:").pack(side=tk.LEFT)
        self.state_var = tk.StringVar(value="—")
        ttk.Label(state_row, textvariable=self.state_var, width=10, font=bold).pack(side=tk.LEFT, padx=(4, 16))
        ttk.Label(state_row, text="Reason:").pack(side=tk.LEFT)
        self.reason_var = tk.StringVar(value="—")
        ttk.Label(state_row, textvariable=self.reason_var, width=18, font=bold).pack(side=tk.LEFT, padx=(4, 16))
        self.sw_start_btn = ttk.Button(
            state_row, text="Software Start", command=self.on_software_start, state=tk.DISABLED
        )
        self.sw_start_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.sw_stop_btn = ttk.Button(
            state_row, text="Software Stop", command=self.on_software_stop, state=tk.DISABLED
        )
        self.sw_stop_btn.pack(side=tk.LEFT)

        # Sensors — values decoded from the microcontroller stream (3 columns).
        sens_f = ttk.LabelFrame(self.root, text="Sensors", padding=6)
        sens_f.pack(fill=tk.X, padx=6, pady=6)
        for i, sid in enumerate(SENSOR_IDS):
            r, c = i // 3, i % 3
            cell = ttk.Frame(sens_f)
            cell.grid(row=r, column=c, sticky=tk.NW, padx=(0, 14), pady=2)
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

        # Heartbeat toggle — label reflects current state.
        self.toggle_btn = ttk.Button(send_row, text="Heartbeat On", command=self.toggle_heartbeat)
        self.toggle_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{ts}] {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _recv_loop(self):
        if self.sock:
            self.sock.settimeout(0.25)
        while self.recv_running and self.sock:
            try:
                data = self.sock.recv(4096)
                if not data:
                    break
                msgs = self._stream_parser.feed(data)
                hex_str = data.hex(" ")
                self._last_rx_time = time.time()

                def update(h=hex_str, m=msgs):
                    self.log(f"Recv ({len(h.replace(' ', '')) // 2} B): {h}")
                    for msg in m:
                        mtype = msg.get("type")
                        if mtype == "ping":
                            self._last_pong_time = time.time()
                        elif mtype == "state_transition":
                            self._apply_state_transition(msg)
                        else:
                            self._apply_sensor_update(msg)
                        self.log(f"  → {protocol.describe(msg)}")
                    self._refresh_sensor_display()
                self.root.after(0, update)
            except socket.timeout:
                continue
            except (OSError, ConnectionResetError, BrokenPipeError):
                break
            except Exception:
                break

        # If recv_running is still True we didn't disconnect deliberately —
        # the server dropped the connection. Clean up the UI on the main thread.
        if self.recv_running:
            def on_lost():
                self.log("Connection lost — server disconnected.")
                self.on_disconnect()
            self.root.after(0, on_lost)

    def _apply_state_transition(self, msg: dict) -> None:
        """Update MCU state mirror + UI labels from a decoded state_transition frame."""
        end_state = msg.get("end_state", "—")
        reason = msg.get("reason", "—")
        self.current_mcu_state = end_state
        self.current_reason = reason
        self.state_var.set(end_state.upper())
        # Reason is most useful on STOP; for ready/running it's redundant with state.
        self.reason_var.set(reason if end_state == "stop" else "—")
        self._refresh_state_controls()

    def _refresh_state_controls(self) -> None:
        """Enable/disable Software Start and Software Stop by current MCU state."""
        if not self.sock:
            self.sw_start_btn.configure(state=tk.DISABLED)
            self.sw_stop_btn.configure(state=tk.DISABLED)
            return
        st = self.current_mcu_state
        self.sw_start_btn.configure(state=tk.NORMAL if st == "ready" else tk.DISABLED)
        self.sw_stop_btn.configure(
            state=tk.NORMAL if st in ("ready", "running") else tk.DISABLED
        )

    def on_software_start(self) -> None:
        if not self.sock:
            return
        try:
            self.sock.sendall(protocol.encode_running())
            self.log("Sent: Software Start")
        except Exception as e:
            self.log(f"Send error: {e}")
            self.on_disconnect()

    def on_software_stop(self) -> None:
        if not self.sock:
            return
        try:
            self.sock.sendall(protocol.encode_software_stop())
            self.log("Sent: Software Stop")
        except Exception as e:
            self.log(f"Send error: {e}")
            self.on_disconnect()

    def _apply_sensor_update(self, msg: dict) -> None:
        """Save a decoded sensor message into self.sensor_state."""
        if "sensor_id" not in msg or "value" not in msg:
            return
        sid = _sensor_key(msg.get("type", ""), msg["sensor_id"])
        if sid is None:
            return
        self.sensor_state[sid] = msg["value"]
        if sid == "voltage_1":
            self.voltage1_meter.setValue(self.sensor_state[sid])

    def _refresh_sensor_display(self) -> None:
        """Redraw every sensor label from self.sensor_state."""
        for sid in SENSOR_IDS:
            val = self.sensor_state.get(sid)
            if val is None:
                self.sensor_vars[sid].set("—")
                continue
            _, unit = SENSOR_LABELS.get(sid, (sid, ""))
            if sid == "rpm":
                text = f"{val:.0f}"
            elif unit == "°C":
                text = f"{val:.1f}"
            else:
                text = f"{val:.2f}"
            self.sensor_vars[sid].set(text)

    def _send_heartbeat(self) -> None:
        """Send a ping frame and reschedule; stops automatically when disconnected.

        Skips the send when self.heartbeat is False (operator-side toggle), but
        keeps the timer alive so toggling back on resumes transmission.
        """
        if not self.sock:
            return
        if self.heartbeat:
            try:
                self.sock.sendall(protocol.build_ping())
            except Exception:
                pass  # on_disconnect will clean up if the connection is truly broken
        self._heartbeat_id = self.root.after(1000, self._send_heartbeat)

    def _check_connection_health(self) -> None:
        """Warn once when no data arrives for >5 s; clear when it recovers."""
        if self.sock and self._last_rx_time is not None:
            age = time.time() - self._last_rx_time
            if age > 5.0 and self._connection_healthy:
                self._connection_healthy = False
                self.log(f"No data from server for {age:.0f} s — connection may be lost.")
                self.on_disconnect()
            elif age <= 5.0 and not self._connection_healthy:
                self._connection_healthy = True
                self.log("Connection health restored.")
        self._health_check_id = self.root.after(2000, self._check_connection_health)

    def _check_heartbeat_health(self) -> None:
        """Fault once when no pong has arrived within 3 s of the last ping.

        On fault: operator concludes the MCU is unreachable, optimistically
        forces its local state mirror to STOP (so the UI reflects the
        operator's intent) and sends encode_software_stop() to the MCU. The
        command may or may not arrive — best-effort.
        """
        if self.sock and self.heartbeat and self._last_pong_time is not None:
            age = time.time() - self._last_pong_time
            if age > 3.0 and self._heartbeat_healthy:
                self._heartbeat_healthy = False
                self.log(f"HEARTBEAT FAULT: no pong for {age:.1f} s — stopping.")
                self._fault_stop("comm fail")
            elif age <= 3.0 and not self._heartbeat_healthy:
                self._heartbeat_healthy = True
                self.log("Heartbeat recovered.")
        self._heartbeat_check_id = self.root.after(1000, self._check_heartbeat_health)

    def _fault_stop(self, reason: str) -> None:
        """Operator-side decision to stop the machine on detected fault.

        Forces local state to STOP with the given reason and best-effort
        sends a comm_fail frame to the MCU so its echo carries the same
        reason (rather than overwriting our UI with 'software stop').
        """
        self.current_mcu_state = "stop"
        self.current_reason = reason
        self.state_var.set("STOP")
        self.reason_var.set(reason)
        self._refresh_state_controls()
        if self.sock:
            try:
                self.sock.sendall(protocol.encode_comm_fail())
                self.log("Sent: comm_fail (operator fault stop)")
            except Exception:
                pass  # Connection likely already broken; on_disconnect cleans up.

    def on_connect(self):
        host = self.host_var.get().strip() or "127.0.0.1"
        try:
            port = int(self.port_var.get())
        except tk.TclError:
            port = 8080
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5.0)
            self.sock.connect((host, port))
        except Exception as e:
            self.log(f"Connect error: {e}")
            if self.sock:
                self.sock.close()
                self.sock = None
            return
        self._stream_parser.reset()
        # Seed _last_rx_time so the 5 s connection-health timer starts from
        # connect — otherwise a server that goes silent immediately (e.g. Ack
        # Off on the MCU) would never trip the disconnect check.
        self._last_rx_time = time.time()
        self._connection_healthy = True
        # Seed pong-tracking so the 3 s silence counter starts now, not from
        # whenever the previous session received its last frame.
        self._last_pong_time = time.time()
        self._heartbeat_healthy = True
        self.sensor_state = {sid: None for sid in SENSOR_IDS}
        self._refresh_sensor_display()
        self.current_mcu_state = None
        self.current_reason = None
        self.state_var.set("—")
        self.reason_var.set("—")
        self._refresh_state_controls()
        self.recv_running = True
        self.recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self.recv_thread.start()
        self._send_heartbeat()
        self._check_connection_health()
        self._check_heartbeat_health()
        self.connect_btn.configure(state=tk.DISABLED)
        self.disconnect_btn.configure(state=tk.NORMAL)
        self.send_btn.configure(state=tk.NORMAL)
        self.host_edit.configure(state=tk.DISABLED)
        self.port_spin.configure(state=tk.DISABLED)
        self.log("Connected.")

    def on_disconnect(self):
        if self._heartbeat_id is not None:
            self.root.after_cancel(self._heartbeat_id)
            self._heartbeat_id = None
        if self._health_check_id is not None:
            self.root.after_cancel(self._health_check_id)
            self._health_check_id = None
        if self._heartbeat_check_id is not None:
            self.root.after_cancel(self._heartbeat_check_id)
            self._heartbeat_check_id = None
        self.recv_running = False
        if self.recv_thread:
            self.recv_thread.join(timeout=0.6)
            self.recv_thread = None
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        self.send_btn.configure(state=tk.DISABLED)
        self.connect_btn.configure(state=tk.NORMAL)
        self.disconnect_btn.configure(state=tk.DISABLED)
        self.host_edit.configure(state=tk.NORMAL)
        self.port_spin.configure(state=tk.NORMAL)
        self.current_mcu_state = None
        self.current_reason = None
        self.state_var.set("—")
        self.reason_var.set("—")
        self._refresh_state_controls()
        self.log("Disconnected.")

    def send_hex(self):
        if not self.sock:
            return
        raw = parse_hex(self.hex_var.get())
        if raw is None:
            self.log("Invalid hex.")
            return
        try:
            self.sock.sendall(raw)
            self.log(f"Sent: {raw.hex(' ')}")
            self.hex_var.set("")
        except Exception as e:
            self.log(f"Send error: {e}")
            self.on_disconnect()

    def on_close(self):
        self.on_disconnect()
        self.root.destroy()

    def run(self):
        self.root.mainloop()

    def toggle_heartbeat(self):
        """Enable/disable the 1 Hz binary ping sender."""
        self.heartbeat = not self.heartbeat
        if self.heartbeat:
            # Reset the pong baseline so the stale timer from the off-period
            # doesn't immediately trip a fault on re-enable.
            self._last_pong_time = time.time()
            self._heartbeat_healthy = True
            self.toggle_btn.config(text="Heartbeat On")
            self.log("Heartbeat enabled.")
        else:
            self.toggle_btn.config(text="Heartbeat Off")
            self.log("Heartbeat disabled.")

class OperatorWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        loader = QUiLoader()
        ui_file = QFile(r"operator\Operator GUI.ui")
        ui_file.open(QFile.ReadOnly)

        self.ui = loader.load(ui_file, self)
        ui_file.close()
        self.ui.show()

        self.app = OperatorApp()
        self.ui.Connect.toggled.connect(lambda checked: checked and self.app.on_connect())
        self.ui.Disconnect.toggled.connect(lambda checked: checked and self.app.on_disconnect())
        self.ui.Heartbeat.toggled.connect(lambda checked: checked and self.app.toggle_heartbeat())
        self.ui.SoftwareStart.toggled.connect(lambda checked: checked and self.app.on_software_start())
        self.ui.SoftwareStop.toggled.connect(lambda checked: checked and self.app.on_software_stop())
        self.state = "Stop"

        ##Temp 1 Meter
        self.temp1_meter = RectangularMeter(None, "Temp 1", 40, 80)
        layout = self.ui.Temp1.layout()
        if layout is None:
            layout = QHBoxLayout(self.ui.Temp1)
            self.ui.Temp1.setLayout(layout)
        self.temp1_meter.setMinimumSize(180, 60)
        self.temp1_meter.setValue(60)
        layout.addWidget(self.temp1_meter)

        ##Temp 2 Meter
        self.temp2_meter = RectangularMeter(None, "Temp 2", 40, 80)
        layout = self.ui.Temp2.layout()
        if layout is None:
            layout = QHBoxLayout(self.ui.Temp2)
            self.ui.Temp2.setLayout(layout)
        self.temp2_meter.setMinimumSize(180, 60)
        self.temp2_meter.setValue(60)
        layout.addWidget(self.temp2_meter)

        ##Temp 3 Meter
        self.temp3_meter = RectangularMeter(None, "Temp 3", 40, 80)
        layout = self.ui.Temp3.layout()
        if layout is None:
            layout = QHBoxLayout(self.ui.Temp3)
            self.ui.Temp3.setLayout(layout)
        self.temp3_meter.setMinimumSize(180, 60)
        self.temp3_meter.setValue(60)
        layout.addWidget(self.temp3_meter)

        ##Voltage 1 Meter
        self.voltage1_meter = RectangularMeter(None, "Voltage 1", 0, 10)
        layout = self.ui.Voltage1.layout()
        if layout is None:
            layout = QHBoxLayout(self.ui.Voltage1)
            self.ui.Voltage1.setLayout(layout)
        self.voltage1_meter.setMinimumSize(180, 60)
        self.voltage1_meter.setValue(5)
        layout.addWidget(self.voltage1_meter)

        ##Voltage 2 Meter
        self.voltage2_meter = RectangularMeter(None, "Voltage 2", 150, 180)
        layout = self.ui.Voltage2.layout()
        if layout is None:
            layout = QHBoxLayout(self.ui.Voltage2)
            self.ui.Voltage2.setLayout(layout)
        self.voltage2_meter.setMinimumSize(180, 60)
        self.voltage2_meter.setValue(165)
        layout.addWidget(self.voltage2_meter)

        ##Voltage 3 Meter
        self.voltage3_meter = RectangularMeter(None, "Voltage 3", 450, 510)
        layout = self.ui.Voltage3.layout()
        if layout is None:
            layout = QHBoxLayout(self.ui.Voltage3)
            self.ui.Voltage3.setLayout(layout)
        self.voltage3_meter.setMinimumSize(180, 60)
        self.voltage3_meter.setValue(480)
        layout.addWidget(self.voltage3_meter)
        
        ##Current 1 Meter
        self.current1_meter = RectangularMeter(None, "Current 1", 15, 35)
        layout = self.ui.Current1.layout()
        if layout is None:
            layout = QHBoxLayout(self.ui.Current1)
            self.ui.Current1.setLayout(layout)
        self.current1_meter.setMinimumSize(180, 60)
        self.current1_meter.setValue(25)
        layout.addWidget(self.current1_meter)

        ##Current 2 Meter
        self.current2_meter = RectangularMeter(None, "Current 2", 15, 35)
        layout = self.ui.Current2.layout()
        if layout is None:
            layout = QHBoxLayout(self.ui.Current2)
            self.ui.Current2.setLayout(layout)
        self.current2_meter.setMinimumSize(180, 60)
        self.current2_meter.setValue(25)
        layout.addWidget(self.current2_meter)

        ##Flow
        self.flow_meter = RectangularMeter(None, "Flow", 25, 75)
        layout = self.ui.Flow.layout()
        if layout is None:
            layout = QHBoxLayout(self.ui.Flow)
            self.ui.Flow.setLayout(layout)
        self.flow_meter.setMinimumSize(180, 60)
        self.flow_meter.setValue(50)
        layout.addWidget(self.flow_meter)

        ##RPM
        self.RPM_meter = RectangularMeter(None, "RPM", 2500, 3500)
        layout = self.ui.RPM.layout()
        if layout is None:
            layout = QHBoxLayout(self.ui.RPM)
            self.ui.RPM.setLayout(layout)
        self.RPM_meter.setMinimumSize(180, 60)
        self.RPM_meter.setValue(3000)
        layout.addWidget(self.RPM_meter)
    
    def update_state(self, new_State):
        self.state = new_State


class RectangularMeter(QWidget):
    def __init__(self, parent=None, name = "Empty", min_value=0, max_value=100, low_alarm = None, high_alarm = None):
        super().__init__(parent)
        self.min_value = min_value
        self.max_value = max_value
        self.low_alarm = low_alarm
        self.high_alarm = high_alarm
        self.name = name
        self.value = 0

    def setValue(self, v):
        self.value = max(self.min_value, min(v, self.max_value))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # Outer border
        pen = QPen(Qt.black, 2)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.rect())

        # Fill amount
        ratio = (self.value - self.min_value) / (self.max_value - self.min_value)
        fill_width = ratio * w

        painter.setBrush(QColor(0, 180, 0))
        painter.setPen(Qt.NoPen)
        painter.drawRect(QRectF(0, 0, fill_width, h))

        painter.setPen(Qt.black)
        self.label_text = self.name
        painter.drawText(0, 0, w, 20, Qt.AlignCenter, self.label_text)


        # Ticks + labels
        painter.setPen(QPen(Qt.black, 1))

        tick_values = [
            self.min_value,
            self.min_value + 0.25*(self.max_value - self.min_value),
            self.min_value + 0.50*(self.max_value - self.min_value),
            self.min_value + 0.75*(self.max_value - self.min_value),
            self.max_value
        ]

        if self.low_alarm is not None:
            tick_values.append(self.low_alarm)
        if self.high_alarm is not None: 
            tick_values.append(self.high_alarm)

        for v in tick_values:
            x = int((v - self.min_value) / (self.max_value - self.min_value) * w)
            painter.drawLine(x, h/2, x, h/2 + 6)

            label = f"{v:.1f}".rstrip("0").rstrip(".")
            painter.drawText(x - 10, h - 5, label)




def main():
    app = OperatorApp()
    app.run()


if __name__ == "__main__":
    app = QApplication([])
    window = OperatorWindow()
    window.show()
    app.exec()
    main()


