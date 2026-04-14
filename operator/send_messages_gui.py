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

# Allow importing protocol.py from the project root regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import protocol


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
        self._stream_parser = protocol.StreamParser()
        self._last_rx_time: float | None = None
        self._connection_healthy: bool = True
        self._heartbeat_id: str | None = None
        self._health_check_id: str | None = None

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
                        self.log(f"  → {protocol.describe(msg)}")
                self.root.after(0, update)
            except socket.timeout:
                continue
            except (OSError, ConnectionResetError, BrokenPipeError):
                break
            except Exception:
                break

    def _send_heartbeat(self) -> None:
        """Send a ping frame and reschedule; stops automatically when disconnected."""
        if not self.sock:
            return
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
            elif age <= 5.0 and not self._connection_healthy:
                self._connection_healthy = True
                self.log("Connection health restored.")
        self._health_check_id = self.root.after(2000, self._check_connection_health)

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
        self._last_rx_time = None
        self._connection_healthy = True
        self.recv_running = True
        self.recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self.recv_thread.start()
        self._send_heartbeat()
        self._check_connection_health()
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


def main():
    app = OperatorApp()
    app.run()


if __name__ == "__main__":
    main()
