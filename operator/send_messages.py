#!/usr/bin/env python3
"""
STM32H745 Ethernet Interactive Message Sender/Receiver
Uses binary protocol (header 0xAADD, length, contents, checksum).
Connect to real board at 192.168.1.10 or to mock: --host 127.0.0.1 --port 8080
"""

import socket
import time
import sys
import select
import threading
import platform
import argparse

from protocol import (
    encode,
    extract_frames,
    MSG_HEARTBEAT,
    MSG_TEMPERATURE,
)

# Cross-platform single character input
if platform.system() == 'Windows':
    import msvcrt
    
    def get_char():
        """Get a single character from stdin (Windows)"""
        if msvcrt.kbhit():
            return msvcrt.getch().decode('utf-8', errors='ignore')
        return None
    
    def has_char():
        """Check if a character is available (Windows)"""
        return msvcrt.kbhit()
else:
    # Unix/Linux/macOS - using termios and tty
    import termios
    import tty
    
    # Store original terminal settings
    _old_terminal_settings = None
    
    def _setup_terminal():
        """Setup terminal for single character input"""
        global _old_terminal_settings
        if _old_terminal_settings is None:
            fd = sys.stdin.fileno()
            _old_terminal_settings = termios.tcgetattr(fd)
            tty.setraw(sys.stdin.fileno())
    
    def _restore_terminal():
        """Restore original terminal settings"""
        global _old_terminal_settings
        if _old_terminal_settings is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _old_terminal_settings)
            _old_terminal_settings = None
    
    def get_char():
        """Get a single character from stdin (Unix/macOS)"""
        _setup_terminal()
        try:
            if select.select([sys.stdin], [], [], 0.01)[0]:
                char = sys.stdin.read(1)
                return char
            return None
        except:
            return None
    
    def has_char():
        """Check if a character is available (Unix/macOS)"""
        return select.select([sys.stdin], [], [], 0)[0] != []

# Message type for LED (not in doc; mock logs as unknown)
MSG_LED = 0x30

RECEIVE_TIMEOUT = 0.1   # seconds for checking incoming messages

# Global socket and receive buffer for threads
sock = None
running = True
recv_buffer = b""


def format_received(contents: bytes) -> str:
    """Turn protocol contents into a readable string."""
    if not contents:
        return "(empty)"
    msg_type = contents[0]
    if msg_type == MSG_HEARTBEAT:
        return "Heartbeat"
    if msg_type == MSG_TEMPERATURE and len(contents) >= 3:
        return f"Temperature sensor={contents[1]} value={contents[2]}°C"
    return contents.hex(" ")


def receive_messages():
    """Thread function to continuously receive and display binary protocol frames."""
    global sock, running, recv_buffer

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
                    recv_buffer += data
                    frames, recv_buffer = extract_frames(recv_buffer)
                    for contents in frames:
                        timestamp = time.strftime("%H:%M:%S")
                        print(f"\n[{timestamp}] <<< {format_received(contents)}")
                        print("You: ", end='', flush=True)
                else:
                    timestamp = time.strftime("%H:%M:%S")
                    print(f"\n[{timestamp}] Connection closed by board")
                    running = False
                    break
        except (BlockingIOError, OSError) as e:
            if hasattr(e, 'winerror') and e.winerror == 10035:
                continue
            if hasattr(e, 'errno') and e.errno in (11, 10035):
                continue
            timestamp = time.strftime("%H:%M:%S")
            print(f"\n[{timestamp}] Socket error: {e}")
            running = False
            break
        except Exception as e:
            if running:
                print(f"\nReceive thread error: {e}")
                running = False
                break

def send_message_to_board(host: str, port: int):
    """Connect to board (or mock) and handle user input using binary protocol."""
    global sock, running, recv_buffer
    recv_buffer = b""
    message_count = 0

    print(f"Connecting to {host}:{port}...")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect((host, port))
        sock.setblocking(False)

        print("Connected (binary protocol).\n")
        print("  h / heartbeat + Enter  -> send heartbeat")
        print("  t / temp + Enter       -> request temperature")
        print("  g, r, y (no Enter)     -> LED command")
        print("  quit / exit / q + Enter -> disconnect\n")

        receive_thread = threading.Thread(target=receive_messages, daemon=True)
        receive_thread.start()

        input_buffer = ""
        print("You: ", end="", flush=True)

        try:
            while running:
                try:
                    if has_char():
                        char = get_char()
                        if char is None:
                            continue

                        if ord(char) == 13:  # Enter
                            if input_buffer.strip():
                                user_input = input_buffer.strip().lower()
                                input_buffer = ""

                                if user_input in ("quit", "exit", "q"):
                                    print("\nDisconnecting...")
                                    break

                                if user_input in ("h", "heartbeat"):
                                    contents = bytes([MSG_HEARTBEAT])
                                elif user_input in ("t", "temp"):
                                    contents = bytes([MSG_TEMPERATURE, 0x01])  # sensor 1
                                else:
                                    print("\nUnknown command. Use h, t, g, r, y, quit.")
                                    print("You: ", end="", flush=True)
                                    continue

                                frame = encode(contents)
                                sock.sendall(frame)
                                message_count += 1
                                timestamp = time.strftime("%H:%M:%S")
                                print(f"\n[{timestamp}] >>> Sent: {user_input}")
                                print("You: ", end="", flush=True)
                            else:
                                print("\nYou: ", end="", flush=True)

                        elif ord(char) in (8, 127):  # Backspace
                            if input_buffer:
                                input_buffer = input_buffer[:-1]
                                print("\b \b", end="", flush=True)

                        elif ord(char) == 3:  # Ctrl+C
                            print("\n\nDisconnecting...")
                            break

                        elif char.lower() in ("g", "r", "y"):
                            contents = bytes([MSG_LED, ord(char.lower())])
                            frame = encode(contents)
                            try:
                                sock.sendall(frame)
                                message_count += 1
                                timestamp = time.strftime("%H:%M:%S")
                                print(f"\n[{timestamp}] >>> Sent LED: {char}")
                                print("You: ", end="", flush=True)
                            except socket.error as e:
                                print(f"\n[{timestamp}] Error sending: {e}")
                                break
                        else:
                            input_buffer += char
                            print(char, end="", flush=True)
                    else:
                        time.sleep(0.01)
                except KeyboardInterrupt:
                    print("\n\nDisconnecting...")
                    break
                except Exception as e:
                    if running:
                        print(f"\nInput error: {e}")
                        break
        finally:
            if platform.system() != "Windows":
                try:
                    _restore_terminal()
                except Exception:
                    pass

    except socket.timeout:
        print("Error: Connection timeout. Is the board or mock running?")
        sys.exit(1)
    except ConnectionRefusedError:
        print("Error: Connection refused. Start the mock: python mock-microcontroller/mock_microcontroller.py")
        sys.exit(1)
    except socket.gaierror:
        print(f"Error: Could not resolve hostname {host}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nDisconnecting...")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        running = False
        if sock:
            sock.close()
            print(f"Connection closed. Total messages sent: {message_count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Operator client (binary protocol)")
    parser.add_argument("--host", default="192.168.1.10", help="Board or mock IP (use 127.0.0.1 for mock)")
    parser.add_argument("--port", type=int, default=8080, help="TCP port")
    args = parser.parse_args()
    send_message_to_board(args.host, args.port)
