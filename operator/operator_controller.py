#!/usr/bin/env python3
"""
Headless operator logic — TCP client with no GUI dependency.

Used directly by the automated test suite (TC-OP-A through TC-OP-H).
The GUI (send_messages_gui.py / OperatorApp) is the display layer on top
of this; tests drive Operator directly so they don't need tkinter, sleeps,
or root.update() calls.

Public API
----------
    op = Operator("127.0.0.1", port)   # connect to the test TCP server

    # Ask the operator what state it currently believes the MCU is in.
    # Blocks until a state-transition message arrives or `wait` s elapses.
    state = op.get_state(wait=2.0)
    # → {"state": "stop"|"ready"|"running"|None,
    #    "reason": "bootup"|"e-stop"|...|None,
    #    "connected": bool}

    # Tell the operator to send a command to the MCU.
    op.act("software_start")   # sends encode_running() over TCP
    op.act("software_stop")    # sends encode_software_stop() over TCP

    op.close()                 # shutdown cleanly
"""

import socket
import threading
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import protocol

# How often (seconds) the operator pings the MCU.
HEARTBEAT_INTERVAL = 1.0
# How long (seconds) of silence before the operator declares a comm fail.
PONG_TIMEOUT = 3.0


class Operator:
    """Headless TCP-client operator for use in automated tests.

    Manages its own recv and heartbeat threads so the calling thread
    (the test) never has to poll or sleep — just call get_state() and act().
    """

    def __init__(self, host: str, port: int, heartbeat: bool = True):
        """Connect to the MCU/test server immediately.

        Parameters
        ----------
        host, port
            Address of the TCP server (test MCUTestServer or real MCU).
        heartbeat
            When True (default) send a 1 Hz ping and detect missing pongs.
            Set False in tests that want to control heartbeat manually.
        """
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(5.0)
        self._sock.connect((host, port))

        self._parser = protocol.StreamParser()

        # State machine mirror — updated from incoming state_transition frames.
        self._state: str | None = None
        self._reason: str | None = None
        self._connected: bool = True

        self._lock = threading.Lock()
        # Fires whenever state or connectivity changes so get_state() unblocks.
        self._state_event = threading.Event()
        # Fires when the TCP connection is lost.
        self._disconnect_event = threading.Event()

        # Heartbeat tracking — initialise pong time to now so the fault
        # timer only starts counting after the first missed pong cycle.
        self._last_pong: float = time.time()
        self._heartbeat_enabled = heartbeat
        self._heartbeat_faulted: bool = False   # True after first comm-fail

        self._recv_running = True
        self._recv_thread = threading.Thread(
            target=self._recv_loop, daemon=True, name="Operator-recv"
        )
        self._recv_thread.start()

        if heartbeat:
            self._hb_running = True
            self._hb_thread = threading.Thread(
                target=self._heartbeat_loop, daemon=True, name="Operator-hb"
            )
            self._hb_thread.start()
        else:
            self._hb_running = False
            self._hb_thread = None

    # ------------------------------------------------------------------ recv

    def _recv_loop(self) -> None:
        self._sock.settimeout(0.25)
        while self._recv_running:
            try:
                data = self._sock.recv(4096)
                if not data:
                    break
                for msg in self._parser.feed(data):
                    self._process_msg(msg)
            except socket.timeout:
                continue
            except Exception:
                break

        # Connection lost — update state and unblock any waiting get_state().
        with self._lock:
            self._connected = False
        self._disconnect_event.set()
        self._state_event.set()

    def _process_msg(self, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == "ping":
            # MCU echoed our ping back — record pong arrival time.
            with self._lock:
                self._last_pong = time.time()
                self._heartbeat_faulted = False   # recover if it had faulted
        elif mtype == "state_transition":
            with self._lock:
                self._state = msg["end_state"]
                self._reason = msg["reason"]
            self._state_event.set()

    # --------------------------------------------------------------- heartbeat

    def _heartbeat_loop(self) -> None:
        """Send a ping every HEARTBEAT_INTERVAL seconds; fault on pong timeout."""
        while self._hb_running:
            # Send ping.
            try:
                self._sock.sendall(protocol.build_ping())
            except Exception:
                break

            time.sleep(HEARTBEAT_INTERVAL)

            # Check whether the MCU has been silent too long.
            with self._lock:
                age = time.time() - self._last_pong
                already_faulted = self._heartbeat_faulted
                connected = self._connected

            if not connected:
                break
            if age > PONG_TIMEOUT and not already_faulted:
                # Declare comm fail.
                with self._lock:
                    self._heartbeat_faulted = True
                    self._state = "stop"
                    self._reason = "comm fail"
                self._state_event.set()
                try:
                    self._sock.sendall(protocol.encode_comm_fail())
                except Exception:
                    pass   # socket likely already dead

    # ---------------------------------------------------------------- public API

    def get_state(self, wait: float = 2.0) -> dict:
        """Return the current MCU state mirror.

        Blocks up to *wait* seconds for a new state-transition frame (or a
        connectivity change) to arrive, then returns immediately.  If the
        state was already updated before this call, returns at once.

        Returns
        -------
        dict with keys:
            "state"     – "stop" | "ready" | "running" | None (not yet received)
            "reason"    – reason string | None
            "connected" – True if the TCP connection is still up
        """
        self._state_event.wait(timeout=wait)
        self._state_event.clear()
        with self._lock:
            return {
                "state":     self._state,
                "reason":    self._reason,
                "connected": self._connected,
            }

    def act(self, command: str) -> None:
        """Send an operator command to the MCU over TCP.

        Parameters
        ----------
        command
            "software_start" – send encode_running() (READY → RUNNING)
            "software_stop"  – send encode_software_stop() (READY/RUNNING → STOP)

        Raises
        ------
        ValueError  if *command* is not recognised.
        OSError     if the socket is closed / not connected.
        """
        if command == "software_start":
            self._sock.sendall(protocol.encode_running())
        elif command == "software_stop":
            self._sock.sendall(protocol.encode_software_stop())
        else:
            raise ValueError(f"Operator.act(): unknown command {command!r}")

    def is_connected(self) -> bool:
        """Return True while the TCP connection is up."""
        with self._lock:
            return self._connected

    def wait_disconnect(self, timeout: float = 2.0) -> bool:
        """Block until the connection drops.  Returns True if it dropped in time."""
        return self._disconnect_event.wait(timeout=timeout)

    def close(self) -> None:
        """Shut down background threads and close the socket."""
        self._recv_running = False
        self._hb_running = False
        try:
            self._sock.close()
        except Exception:
            pass
        self._recv_thread.join(timeout=1.0)
        if self._hb_thread is not None:
            self._hb_thread.join(timeout=1.5)
