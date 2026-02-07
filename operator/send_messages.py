#!/usr/bin/env python3
"""
Simple TCP client: type hex bytes (e.g. AA DD 01 or AADD01), Enter to send.
Prints received bytes as hex. No protocol.
"""

import socket
import time
import sys
import select
import threading
import argparse

RECEIVE_TIMEOUT = 0.1
sock = None
running = True


def parse_hex(line: str) -> bytes | None:
    """Parse a line of hex (spaces optional). Returns None if invalid."""
    line = line.strip().replace(" ", "").replace(",", "")
    if not line:
        return None
    try:
        return bytes.fromhex(line)
    except ValueError:
        return None


def receive_loop():
    global sock, running
    while sock is None and running:
        time.sleep(0.1)
    if not running or sock is None:
        return
    while running:
        try:
            ready = select.select([sock], [], [], RECEIVE_TIMEOUT)
            if ready[0]:
                data = sock.recv(1024)
                if data:
                    ts = time.strftime("%H:%M:%S")
                    print(f"\n[{ts}] <<< {data.hex(' ')}")
                    print("You: ", end="", flush=True)
                else:
                    ts = time.strftime("%H:%M:%S")
                    print(f"\n[{ts}] Connection closed")
                    running = False
                    break
        except (BlockingIOError, OSError) as e:
            if getattr(e, "winerror", None) == 10035 or getattr(e, "errno", None) in (11, 10035):
                continue
            print(f"\nSocket error: {e}")
            running = False
            break
        except Exception as e:
            if running:
                print(f"\nReceive error: {e}")
                running = False
            break


def main(host: str, port: int):
    global sock, running
    running = True

    print(f"Connecting to {host}:{port}...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect((host, port))
        sock.setblocking(False)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    print("Connected. Type hex bytes (e.g. AA DD 01 or AADD01), Enter to send. quit = exit.\n")
    threading.Thread(target=receive_loop, daemon=True).start()

    try:
        while running:
            try:
                line = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line.lower() in ("quit", "exit", "q"):
                break
            raw = parse_hex(line)
            if raw is None:
                print("Invalid hex. Use e.g. AA DD 01 or AADD01")
                continue
            try:
                sock.sendall(raw)
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] >>> {raw.hex(' ')}")
            except Exception as e:
                print(f"Send error: {e}")
                break
    finally:
        running = False
        if sock:
            sock.close()
        print("Disconnected.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send hex bytes over TCP")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Port (default 8080)")
    args = parser.parse_args()
    main(args.host, args.port)
