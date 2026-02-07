#!/usr/bin/env python3
"""
Launch both the mock microcontroller GUI and the operator GUI in one go.
Runs each in its own process; close both windows (or Ctrl+C here) to exit.
"""

import subprocess
import sys
from pathlib import Path

def main():
    root = Path(__file__).resolve().parent
    mock_script = root / "mock-microcontroller" / "mock_microcontroller_gui.py"
    operator_script = root / "operator" / "send_messages_gui.py"

    if not mock_script.is_file() or not operator_script.is_file():
        print("Missing GUI scripts. Run from Tunneling26-27 with mock-microcontroller/ and operator/ present.")
        sys.exit(1)

    print("Starting mock microcontroller GUI...")
    p_mock = subprocess.Popen(
        [sys.executable, str(mock_script)],
        cwd=str(root),
    )
    print("Starting operator GUI...")
    p_operator = subprocess.Popen(
        [sys.executable, str(operator_script)],
        cwd=str(root),
    )

    print("Both GUIs started. Close their windows when done (or Ctrl+C here).")
    try:
        p_mock.wait()
        p_operator.wait()
    except KeyboardInterrupt:
        p_mock.terminate()
        p_operator.terminate()
        p_mock.wait()
        p_operator.wait()
    print("Done.")


if __name__ == "__main__":
    main()
