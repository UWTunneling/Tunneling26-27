#!/usr/bin/env python3
"""
STM32H745 Ethernet Interactive Message Sender/Receiver
Allows user to type messages and displays sent/received messages
LED commands (g, r, y) are sent immediately without Enter
"""

import socket
import time
import sys
import select
import threading
import platform

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

# Board configuration
BOARD_IP = "192.168.1.10"
BOARD_PORT = 8080
RECEIVE_TIMEOUT = 0.1   # seconds for checking incoming messages

# Global socket for threads
sock = None
running = True

def receive_messages():
    """Thread function to continuously receive and display messages from board"""
    global sock, running
    
    # Wait for socket to be initialized
    while sock is None and running:
        time.sleep(0.1)
    
    if not running or sock is None:
        return
    
    while running:
        try:
            ready = select.select([sock], [], [], RECEIVE_TIMEOUT)
            if ready[0]:
                # Data available to receive
                response = sock.recv(1024)
                if response:
                    timestamp = time.strftime("%H:%M:%S")
                    decoded = response.decode('utf-8', errors='ignore').strip()
                    print(f"\n[{timestamp}] <<< Received: {decoded}")
                    print("You: ", end='', flush=True)  # Re-print prompt
                else:
                    # Connection closed by peer
                    timestamp = time.strftime("%H:%M:%S")
                    print(f"\n[{timestamp}] Connection closed by board")
                    running = False
                    break
        except (BlockingIOError, OSError) as e:
            # Non-blocking socket would block - this is normal, continue
            if hasattr(e, 'winerror') and e.winerror == 10035:  # WSAEWOULDBLOCK on Windows
                continue
            elif hasattr(e, 'errno') and e.errno in (11, 10035):  # EAGAIN/EWOULDBLOCK
                continue
            else:
                timestamp = time.strftime("%H:%M:%S")
                print(f"\n[{timestamp}] Socket error: {e}")
                running = False
                break
        except Exception as e:
            if running:
                print(f"\nReceive thread error: {e}")
                running = False
                break

def send_message_to_board():
    """Connect to board and handle user input"""
    
    global sock, running
    message_count = 0
    
    print(f"Connecting to STM32 board at {BOARD_IP}:{BOARD_PORT}...")
    
    try:
        # Create socket and connect
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)  # 5 second timeout for initial connection
        sock.connect((BOARD_IP, BOARD_PORT))
        
        # Make socket non-blocking for listening
        sock.setblocking(False)
        
        print(f"Connected successfully!\n")
        print("LED commands: Press 'g', 'r', or 'y' to toggle LEDs (sent immediately)")
        print("Other messages: Type and press Enter to send.")
        print("Type 'quit' or 'exit' to disconnect.\n")
        
        # Start receive thread
        receive_thread = threading.Thread(target=receive_messages, daemon=True)
        receive_thread.start()
        
        # Buffer for multi-character input
        input_buffer = ""
        
        # Print initial prompt
        print("You: ", end='', flush=True)
        
        # Main loop for user input
        try:
            while running:
                try:
                    # Check if a key is available (non-blocking, cross-platform)
                    if has_char():
                        char = get_char()
                        if char is None:
                            continue
                        
                        # Handle special characters
                        if ord(char) == 13:  # Enter key
                            if input_buffer.strip():
                                user_input = input_buffer.strip()
                                input_buffer = ""
                                
                                # Check for quit commands
                                if user_input.lower() in ['quit', 'exit', 'q']:
                                    print("\nDisconnecting...")
                                    break
                                
                                # Send the message
                                message = user_input + "\n"
                                timestamp = time.strftime("%H:%M:%S")
                                message_count += 1
                                
                                try:
                                    sock.sendall(message.encode('utf-8'))
                                    print(f"\n[{timestamp}] >>> Sent: {user_input}")
                                    print("You: ", end='', flush=True)
                                except socket.error as e:
                                    print(f"\n[{timestamp}] Error sending: {e}")
                                    break
                            else:
                                # Empty Enter, just print new prompt
                                print("\nYou: ", end='', flush=True)
                        
                        elif ord(char) == 8 or ord(char) == 127:  # Backspace
                            if input_buffer:
                                input_buffer = input_buffer[:-1]
                                print('\b \b', end='', flush=True)
                        
                        elif ord(char) == 3:  # Ctrl+C
                            print("\n\nDisconnecting...")
                            break
                        
                        elif char.lower() in ['g', 'r', 'y']:
                            # LED command - send immediately
                            timestamp = time.strftime("%H:%M:%S")
                            message = char + "\n"
                            try:
                                sock.sendall(message.encode('utf-8'))
                                print(f"\n[{timestamp}] >>> Sent LED command: {char}")
                                print("You: ", end='', flush=True)
                            except socket.error as e:
                                print(f"\n[{timestamp}] Error sending: {e}")
                                break
                        
                        else:
                            # Regular character - add to buffer
                            input_buffer += char
                            print(char, end='', flush=True)
                    
                    else:
                        # No key pressed, sleep briefly to avoid busy waiting
                        time.sleep(0.01)
                        
                except KeyboardInterrupt:
                    print("\n\nDisconnecting...")
                    break
                except Exception as e:
                    if running:
                        print(f"\nInput error: {e}")
                        break
        finally:
            # Restore terminal settings on Unix/macOS
            if platform.system() != 'Windows':
                try:
                    _restore_terminal()
                except:
                    pass
                
    except socket.timeout:
        print("Error: Connection timeout. Check:")
        print("  1. Board is powered and running")
        print("  2. Ethernet cable is connected")
        print("  3. Laptop IP is configured as 192.168.1.1")
        print("  4. Board IP is 192.168.1.10")
        sys.exit(1)
    except ConnectionRefusedError:
        print("Error: Connection refused. Check:")
        print("  1. Board is running the TCP echo server code")
        print("  2. Port 8080 is correct")
        sys.exit(1)
    except socket.gaierror:
        print(f"Error: Could not resolve hostname {BOARD_IP}")
        print("Check your network configuration")
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
    send_message_to_board()
