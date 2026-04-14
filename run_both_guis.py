# #!/usr/bin/env python3
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
    sensor_script   = root / "mock-microcontroller" / "sensor_panel.py"

    missing = [s for s in (mock_script, sensor_script, operator_script) if not s.is_file()]
    if missing:
        print("Missing scripts:")
        for m in missing:
            print(f"  {m}")
        print("Missing GUI scripts. Run from Tunneling26-27 with mock-microcontroller/ and operator/ present.")
        sys.exit(1)

    procs = []

    print("Starting mock microcontroller GUI...")
    procs.append(subprocess.Popen(
        [sys.executable, str(mock_script)],
        cwd=str(root),
    ))

    print("Starting sensor control panel...")
    procs.append(subprocess.Popen(
        [sys.executable, str(sensor_script)],
        cwd=str(root / "mock-microcontroller"),
    ))

    print("Starting operator GUI...")
    procs.append(subprocess.Popen(
        [sys.executable, str(operator_script)],
        cwd=str(root),
    ))

    print("All GUIs started. Close their windows when done (or Ctrl+C here).")

    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait()
    print("Done.")


if __name__ == "__main__":
    main()


