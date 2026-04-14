#!/usr/bin/env python3
"""
Random state simulator for the mock microcontroller.

Simulates 10 sensors with a single seeded RNG and tick-based updates:
- 3 temperature, 1 RPM, 3 voltage (480 V, 120 V, 5 V rails), 2 current, 1 flow.

See docs/random-state-simulator.md for the design.
"""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass, field

DEFAULT_TICK_HZ = 100.0


@dataclass
class SensorSpec:
    """Configuration for one sensor: range and random-walk step scale."""

    min_val: float
    max_val: float
    step_scale: float = 1.0  # max abs delta per tick (fraction of range if < 1, else absolute)


# Default ranges: voltage rails match machine power (480 V main, 120 V, 5 V for sensors/mcu)
DEFAULT_SPECS: dict[str, SensorSpec] = {
    "temp_1": SensorSpec(0.0, 120.0, 0.02),
    "temp_2": SensorSpec(0.0, 120.0, 0.02),
    "temp_3": SensorSpec(0.0, 120.0, 0.02),
    "rpm": SensorSpec(0.0, 6000.0, 0.03),
    "voltage_1": SensorSpec(450.0, 510.0, 0.015),   # 480 V main
    "voltage_2": SensorSpec(110.0, 130.0, 0.015),  # 120 V
    "voltage_3": SensorSpec(4.7, 5.3, 0.01),      # 5 V sensors/microcontroller
    "current_1": SensorSpec(0.0, 50.0, 0.025),
    "current_2": SensorSpec(0.0, 50.0, 0.025),
    "flow_1": SensorSpec(0.0, 100.0, 0.02),
}

SENSOR_IDS: tuple[str, ...] = (
    "temp_1", "temp_2", "temp_3",
    "rpm",
    "voltage_1", "voltage_2", "voltage_3",
    "current_1", "current_2",
    "flow_1",
)


class SensorSimulator:
    """
    Tick-based sensor simulator with a single seeded RNG.

    - tick(): advance RNG and update all sensor values (random walk, clamped to range).
    - get_snapshot(): return current values (no RNG); safe to call from any thread
      if tick() is the only writer.
    """

    def __init__(self, seed: int | None = None, specs: dict[str, SensorSpec] | None = None):
        self._rng = random.Random(seed)
        self._specs = dict(specs or DEFAULT_SPECS)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Current value per sensor; init at midpoint of range
        self._values: dict[str, float] = {}
        for sid in SENSOR_IDS:
            spec = self._specs.get(sid)
            if spec is None:
                spec = SensorSpec(0.0, 100.0, 0.02)
            mid = (spec.min_val + spec.max_val) / 2.0
            self._values[sid] = mid
        self._tick_count = 0

    def start(self, tick_hz: float = DEFAULT_TICK_HZ) -> None:
        """Begin advancing state on a daemon thread at tick_hz."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        period = 1.0 / tick_hz
        self._thread = threading.Thread(target=self._run, args=(period,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)
            self._thread = None

    def _run(self, period: float) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(timeout=period)

    def tick(self) -> None:
        """Advance one step: update all sensors with random walk and clamp to range."""
        with self._lock:
            for sid in SENSOR_IDS:
                spec = self._specs.get(sid)
                if spec is None:
                    continue
                current = self._values[sid]
                span = spec.max_val - spec.min_val
                # step scale: if < 1 treat as fraction of range, else absolute
                max_step = spec.step_scale * span if spec.step_scale < 1 else spec.step_scale
                delta = self._rng.uniform(-max_step, max_step)
                new_val = current + delta
                new_val = max(spec.min_val, min(spec.max_val, new_val))
                self._values[sid] = new_val
            self._tick_count += 1

    def get_snapshot(self) -> dict[str, float]:
        """Return a copy of current sensor values (no randomness)."""
        with self._lock:
            return dict(self._values)

    def get_tick_count(self) -> int:
        """Return number of ticks since creation (or last reset)."""
        return self._tick_count

    def reset(self, seed: int | None = None) -> None:
        """Reinit RNG (optional new seed) and set values back to range midpoints."""
        if seed is not None:
            self._rng = random.Random(seed)
        for sid in SENSOR_IDS:
            spec = self._specs.get(sid)
            if spec is None:
                spec = SensorSpec(0.0, 100.0, 0.02)
            mid = (spec.min_val + spec.max_val) / 2.0
            self._values[sid] = mid
        self._tick_count = 0


def main() -> None:
    """Run a short demo: seeded simulator, a few ticks, print snapshots."""
    sim = SensorSimulator(seed=42)
    print("SensorSimulator demo (seed=42, random walk)")
    print("IDs:", SENSOR_IDS)
    for i in range(5):
        sim.tick()
        snap = sim.get_snapshot()
        print(f"  tick {sim.get_tick_count()}: temp_1={snap['temp_1']:.1f} °C, rpm={snap['rpm']:.0f}, voltage_1={snap['voltage_1']:.2f} V")
    print("Done.")


if __name__ == "__main__":
    main()
