#!/usr/bin/env python3
"""
Mock STM32 microcontroller: TCP server that speaks the binary protocol.
Use for testing without hardware. See docs/communication_protocol.md.
"""

import socket
import sys
import time
import argparse
from pathlib import Path

# Protocol lives in operator/; add that folder so we import our protocol, not stdlib 'operator'
_operator_dir = Path(__file__).resolve().parent.parent / "operator"
if str(_operator_dir) not in sys.path:
    sys.path.insert(0, str(_operator_dir))
from protocol import (
    encode,
    extract_frames,
    MSG_HEARTBEAT,
    MSG_TEMPERATURE,
)

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def handle_frame(conn: socket.socket, contents: bytes) -> bytes | None:
    """
    Handle one received frame (contents only). Optionally return response contents to send.
    """
    if not contents:
        return None
    msg_type = contents[0]
    if msg_type == MSG_HEARTBEAT:
        log("<<< Heartbeat")
        return bytes([MSG_HEARTBEAT])
    if msg_type == MSG_TEMPERATURE:
        # e.g. 0x10 sensor_id value — echo or send dummy temp
        if len(contents) >= 3:
            sensor_id, value = contents[1], contents[2]
            log(f"<<< Temperature sensor={sensor_id} value={value}")
        else:
            log(f"<<< Temperature (malformed): {contents.hex()}")
        # Reply with a dummy temperature: sensor 1, 25°C (0x19)
        return bytes([MSG_TEMPERATURE, 0x01, 0x19])
    log(f"<<< Unknown message type 0x{msg_type:02X}: {contents.hex()}")
    return None


def serve_client(conn: socket.socket, client_addr: tuple) -> None:
    buffer = b""
    try:
        conn.settimeout(30.0)
        while True:
            try:
                data = conn.recv(4096)
            except socket.timeout:
                continue
            if not data:
                break
            buffer += data
            frames, buffer = extract_frames(buffer)
            for contents in frames:
                response = handle_frame(conn, contents)
                if response is not None:
                    frame = encode(response)
                    conn.sendall(frame)
                    log(f">>> Sent {len(response)} byte(s)")
    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        log(f"Client closed: {e}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock STM32 TCP server (binary protocol)")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Bind host (default {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Bind port (default {DEFAULT_PORT})")
    args = parser.parse_args()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((args.host, args.port))
        server.listen(1)
        log(f"Mock microcontroller listening on {args.host}:{args.port}")
        log("Connect with your client (e.g. point BOARD_IP to this host). Ctrl+C to stop.")
        while True:
            conn, addr = server.accept()
            log(f"Client connected from {addr}")
            serve_client(conn, addr)
    except KeyboardInterrupt:
        log("Stopping.")
    finally:
        server.close()


if __name__ == "__main__":
    main()
