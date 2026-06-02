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
from PySide6.QtCore import Qt, QRectF, QFile, QPointF, Signal   # <-- added Signal
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout

# Allow importing protocol.py from the project root regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import protocol


SENSOR_IDS = (
    "temp_1", "temp_2", "temp_3",
    "rpm",
    "voltage_1", "voltage_2", "voltage_3",
    "current_1", "current_2",
    "flow_1",
)


def _sensor_key(msg_type: str, sensor_id: int) -> str | None:
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


class OperatorBackend:
    """
    PURE BACKEND — NO TKINTER, NO UI CODE.
    PySide6 UI will attach callbacks to receive updates.
    """

    def __init__(self):
        self.sock = None
        self.recv_running = False
        self.recv_thread = None

        self.heartbeat = True
        self._stream_parser = protocol.StreamParser()

        self._last_rx_time = None
        self._connection_healthy = True

        self._last_pong_time = None
        self._heartbeat_healthy = True

        # Sensor state
        self.sensor_state = {sid: None for sid in SENSOR_IDS}

        # MCU state
        self.current_mcu_state = None
        self.current_reason = None

        # CALLBACKS (UI attaches to these)
        self.on_log = lambda msg: None
        self.on_sensor_update = lambda sid, value: None
        self.on_state_change = lambda state, reason: None

        # Timers (thread-based, not Tkinter)
        self._stop_timers = False

    # ------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------
    def connect(self, host: str, port: int):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5.0)
            self.sock.connect((host, port))
        except Exception as e:
            self.on_log(f"Connect error: {e}")
            self.sock = None
            return False

        self.on_log("Connected.")

        self._stream_parser.reset()
        self._last_rx_time = time.time()
        self._last_pong_time = time.time()

        self.recv_running = True
        self._stop_timers = False

        self.recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self.recv_thread.start()

        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        threading.Thread(target=self._connection_health_loop, daemon=True).start()
        threading.Thread(target=self._heartbeat_health_loop, daemon=True).start()

        return True

    def disconnect(self):
        self._stop_timers = True
        self.recv_running = False

        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None

        self.on_log("Disconnected.")

    # ------------------------------------------------------------
    # Receive loop
    # ------------------------------------------------------------
    def _recv_loop(self):
        if self.sock:
            self.sock.settimeout(0.25)

        while self.recv_running and self.sock:
            try:
                data = self.sock.recv(4096)
                if not data:
                    break

                msgs = self._stream_parser.feed(data)
                self._last_rx_time = time.time()

                for msg in msgs:
                    mtype = msg.get("type")

                    if mtype == "ping":
                        self._last_pong_time = time.time()

                    elif mtype == "state_transition":
                        self._apply_state_transition(msg)

                    else:
                        self._apply_sensor_update(msg)

                    self.on_log(f"→ {protocol.describe(msg)}")

            except socket.timeout:
                continue
            except Exception:
                break

        if self.recv_running:
            self.on_log("Connection lost — server disconnected.")
            self.disconnect()

    # ------------------------------------------------------------
    # State updates
    # ------------------------------------------------------------
    def _apply_state_transition(self, msg):
        state = msg.get("end_state", "—")
        reason = msg.get("reason", "—")

        self.current_mcu_state = state
        self.current_reason = reason

        self.on_state_change(state, reason)

    def _apply_sensor_update(self, msg):
        if "sensor_id" not in msg or "value" not in msg:
            return

        sid = _sensor_key(msg.get("type", ""), msg["sensor_id"])
        if sid is None:
            return

        value = msg["value"]
        self.sensor_state[sid] = value

        self.on_sensor_update(sid, value)

    # ------------------------------------------------------------
    # Heartbeat + health
    # ------------------------------------------------------------
    def _heartbeat_loop(self):
        while not self._stop_timers:
            if self.sock and self.heartbeat:
                try:
                    self.sock.sendall(protocol.build_ping())
                except:
                    pass
            time.sleep(1)

    def _connection_health_loop(self):
        while not self._stop_timers:
            if self.sock and self._last_rx_time:
                age = time.time() - self._last_rx_time
                if age > 5 and self._connection_healthy:
                    self._connection_healthy = False
                    self.on_log(f"No data for {age:.0f}s — disconnecting.")
                    self.disconnect()
            time.sleep(2)

    def _heartbeat_health_loop(self):
        while not self._stop_timers:
            if self.sock and self.heartbeat and self._last_pong_time:
                age = time.time() - self._last_pong_time
                if age > 3 and self._heartbeat_healthy:
                    self._heartbeat_healthy = False
                    self.on_log(f"HEARTBEAT FAULT ({age:.1f}s) — stopping.")
                    self._fault_stop("comm fail")
            time.sleep(1)

    def _fault_stop(self, reason):
        self.current_mcu_state = "stop"
        self.current_reason = reason
        self.on_state_change("stop", reason)

        if self.sock:
            try:
                self.sock.sendall(protocol.encode_comm_fail())
            except:
                pass

    # ------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------
    def software_start(self):
        if not self.sock:
            return
        try:
            self.sock.sendall(protocol.encode_running())
            self.on_log("Sent: Software Start")
        except:
            self.disconnect()

    def software_stop(self):
        if not self.sock:
            return
        try:
            self.sock.sendall(protocol.encode_software_stop())
            self.on_log("Sent: Software Stop")
        except:
            self.disconnect()

    def send_hex(self, raw_bytes: bytes):
        if not self.sock:
            return
        try:
            self.sock.sendall(raw_bytes)
            self.on_log(f"Sent: {raw_bytes.hex(' ')}")
        except:
            self.disconnect()

    def toggle_heartbeat(self):
        self.heartbeat = not self.heartbeat
        self.on_log(f"Heartbeat {'enabled' if self.heartbeat else 'disabled'}.")


class OperatorWindow(QMainWindow):
    # ---- Qt signals for thread-safe UI updates ----
    sensor_update_signal = Signal(str, float)
    log_signal = Signal(str)
    state_signal = Signal(str, str)

    def __init__(self):
        super().__init__()

        # Connect signals to UI methods
        self.sensor_update_signal.connect(self.update_meter)
        self.log_signal.connect(self.append_log)
        self.state_signal.connect(self.update_state)

        loader = QUiLoader()
        ui_file = QFile(r"operator\Operator GUI.ui")
        ui_file.open(QFile.ReadOnly)

        self.ui = loader.load(ui_file, self)
        ui_file.close()
        self.ui.show()

        self.backend = OperatorBackend()

        # Route backend callbacks through Qt signals
        self.backend.on_log = lambda msg: self.log_signal.emit(msg)
        self.backend.on_sensor_update = lambda sid, value: self.sensor_update_signal.emit(sid, value)
        self.backend.on_state_change = lambda state, reason: self.state_signal.emit(state, reason)

        DEFAULT_HOST = "127.0.0.1"
        DEFAULT_PORT = 8080

        self.ui.Connect.clicked.connect(
            lambda: self.backend.connect(DEFAULT_HOST, DEFAULT_PORT)
        )

        self.ui.Disconnect.clicked.connect(
            lambda: self.backend.disconnect()
        )

        self.ui.Heartbeat.clicked.connect(
            lambda: self.backend.toggle_heartbeat()
        )

        self.ui.SoftwareStart.clicked.connect(
            lambda: self.backend.software_start()
        )

        self.ui.SoftwareStop.clicked.connect(
            lambda: self.backend.software_stop()
        )

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
        self.voltage1_meter = RectangularMeter(None, "Voltage 1", 470, 490)
        layout = self.ui.Voltage1.layout()
        if layout is None:
            layout = QHBoxLayout(self.ui.Voltage1)
            self.ui.Voltage1.setLayout(layout)
        self.voltage1_meter.setMinimumSize(180, 60)
        self.voltage1_meter.setValue(480)
        layout.addWidget(self.voltage1_meter)

        ##Voltage 2 Meter
        self.voltage2_meter = RectangularMeter(None, "Voltage 2", 105, 135)
        layout = self.ui.Voltage2.layout()
        if layout is None:
            layout = QHBoxLayout(self.ui.Voltage2)
            self.ui.Voltage2.setLayout(layout)
        self.voltage2_meter.setMinimumSize(180, 60)
        self.voltage2_meter.setValue(120)
        layout.addWidget(self.voltage2_meter)

        ##Voltage 3 Meter
        self.voltage3_meter = RectangularMeter(None, "Voltage 3", 2, 8)
        layout = self.ui.Voltage3.layout()
        if layout is None:
            layout = QHBoxLayout(self.ui.Voltage3)
            self.ui.Voltage3.setLayout(layout)
        self.voltage3_meter.setMinimumSize(180, 60)
        self.voltage3_meter.setValue(5)
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

    def append_log(self, msg):
        try:
            self.ui.Log.append(msg)
        except:
            print("LOG:", msg)
    
    def update_meter(self, sid, value):
        mapping = {
            "temp_1": self.temp1_meter,
            "temp_2": self.temp2_meter,
            "temp_3": self.temp3_meter,
            "voltage_1": self.voltage1_meter,
            "voltage_2": self.voltage2_meter,
            "voltage_3": self.voltage3_meter,
            "current_1": self.current1_meter,
            "current_2": self.current2_meter,
            "flow_1": self.flow_meter,
            "rpm": self.RPM_meter,
        }

        if sid in mapping:
            mapping[sid].setValue(value)

    def update_state(self, state, reason):
        self.state = state
        try:
            self.ui.CurrState.setText(f"{state}")
            self.ui.CurrReason.setText(f"{reason}")
        except:
            print("STATE:", state, reason)


class RectangularMeter(QWidget):
    def __init__(self, parent=None, name="Empty", min_value=0, max_value=100, low_alarm=None, high_alarm=None):
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

        pen = QPen(Qt.black, 2)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.rect())

        ratio = (self.value - self.min_value) / (self.max_value - self.min_value)
        fill_width = ratio * w

        painter.setBrush(QColor(0, 180, 0))
        painter.setPen(Qt.NoPen)
        painter.drawRect(QRectF(0, 0, fill_width, h))

        painter.setPen(Qt.black)
        painter.drawText(0, 0, w, 20, Qt.AlignCenter, self.name)

        painter.setPen(QPen(Qt.black, 1))

        tick_values = [
            self.min_value,
            self.min_value + 0.25 * (self.max_value - self.min_value),
            self.min_value + 0.50 * (self.max_value - self.min_value),
            self.min_value + 0.75 * (self.max_value - self.min_value),
            self.max_value
        ]

        if self.low_alarm is not None:
            tick_values.append(self.low_alarm)
        if self.high_alarm is not None:
            tick_values.append(self.high_alarm)

        for v in tick_values:
            x = int((v - self.min_value) / (self.max_value - self.min_value) * w)
            painter.drawLine(x, h / 2, x, h / 2 + 6)

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
