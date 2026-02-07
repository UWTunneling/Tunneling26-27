#!/usr/bin/env python3
"""
Mock TCP server: type hex to send, prints received bytes as hex (no echo).
No protocol. Use with operator send_messages.py for simple hex testing.
"""

import socket
import time
import argparse
import select
import threading

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
RECV_TIMEOUT = 0.1
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


def serve_client(conn: socket.socket, addr: tuple) -> bool:
    """Handle one client. Returns True to quit the program, False to accept next client."""
    global running
    running = True
    log(f"Client connected from {addr}")
    threading.Thread(target=receive_loop, args=(conn,), daemon=True).start()

    print("Type hex to send (e.g. AA DD 01). quit = exit program.\n")
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
    args = parser.parse_args()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((args.host, args.port))
        server.listen(1)
        log(f"Listening on {args.host}:{args.port}. quit or Ctrl+C to exit.")
        while True:
            try:
                conn, addr = server.accept()
            except KeyboardInterrupt:
                break
            if serve_client(conn, addr):
                break
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        log("Stopped.")


if __name__ == "__main__":
    main()
