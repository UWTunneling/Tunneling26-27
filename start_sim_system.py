#!/usr/bin/env python3
"""
Start the full simulation system in one process.

The mock microcontroller GUI and the sensor control panel share a single
SensorSimulator, so the panel's Start/Stop is the one authoritative clock
for sensor state. The MCU only samples that state — when the panel is
stopped, MCU readings freeze.

The operator GUI has no shared sensor state and runs as a separate
subprocess (its own Tk event loop).
"""

import subprocess
import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "mock-microcontroller"))

from sensor_simulator import SensorSimulator
from mock_microcontroller_gui import MockApp
from sensor_panel import SensorControlApp


def main() -> None:
    root = Path(__file__).resolve().parent
    operator_script = root / "operator" / "send_messages_gui.py"
    if not operator_script.is_file():
        print(f"Missing operator GUI: {operator_script}")
        sys.exit(1)

    sim = SensorSimulator(seed=None)

    tk_root = tk.Tk()
    tk_root.withdraw()

    mock_win = tk.Toplevel(tk_root)
    mock_app = MockApp(root=mock_win, simulator=sim)

    panel_win = tk.Toplevel(tk_root)
    panel_app = SensorControlApp(root=panel_win, simulator=sim)

    print("Starting operator GUI...")
    operator_proc = subprocess.Popen([sys.executable, str(operator_script)], cwd=str(root))

    def shutdown() -> None:
        try:
            mock_app.on_close()
        except Exception:
            pass
        try:
            panel_app._on_close()
        except Exception:
            pass
        try:
            tk_root.destroy()
        except Exception:
            pass

    mock_win.protocol("WM_DELETE_WINDOW", shutdown)
    panel_win.protocol("WM_DELETE_WINDOW", shutdown)

    print("Sim system running. Close either Tk window (or Ctrl+C here) to exit.")
    try:
        tk_root.mainloop()
    except KeyboardInterrupt:
        shutdown()
    finally:
        operator_proc.terminate()
        try:
            operator_proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            operator_proc.kill()
    print("Done.")


if __name__ == "__main__":
    main()
