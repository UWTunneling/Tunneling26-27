
"""
Each sensor drifts toward a configurable setpoint at a controllable rate,
with independent Gaussian noise added per tick. Setpoint, step_size, and
noise are mutable at any time (including mid-run), so values can sit inside
or outside nominal ranges intentionally.

See docs/sensor-simulator.md for the design.

Update rule per tick (Euler-Maruyama discretisation of OU):
    delta = step_size * (setpoint - current) + noise * N(0, 1)
    new_value = current + delta

- step_size  : float in [0, 1].  Fraction of gap to setpoint closed each tick.
               0 = pure noise, no drift.  1 = snap to setpoint ± noise each tick.
- noise      : float ≥ 0.  Gaussian std-dev in the same units as the sensor value.
               Set to 0 for deterministic convergence to the setpoint.
- setpoint   : target mean value.  May be outside [min_val, max_val] on purpose.
- clamp      : optional (min, max) tuple.  Hard-clamp if set; None = unclamped.
"""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass, field

DEFAULT_TICK_HZ = 100.0
from typing import Optional


@dataclass
class SensorConfig:
    """

    Changes to parameters (setpoint, step_size, noise) can be changed
    while the simulator is running — the effect takes place on the next tick.

    Attributes
    ----------
    setpoint   : Target mean.  The sensor drifts toward this value.
    step_size  : Drift coefficient in [0, 1].  How aggressively the sensor
                 pulls toward setpoint each tick.
    noise      : Gaussian noise std-dev in sensor units (absolute, not relative).
    min_val    : Nominal lower bound (informational; not enforced unless clamp is set).
    max_val    : Nominal upper bound (informational; not enforced unless clamp is set).
    clamp      : Optional (lo, hi) tuple.  If set, values are hard-clamped after
                 each tick.  Pass None to allow excursions outside nominal range.
    """
    setpoint: float
    step_size: float = 0.05     # fraction of (setpoint - current) per tick
    noise: float = 1.0          # Gaussian std-dev, sensor units
    min_val: float = 0.0        # nominal lower bound (informational)
    max_val: float = 100.0      # nominal upper bound (informational)
    clamp: Optional[tuple[float, float]] = None


# Default

def _cfg(
    min_val: float,
    max_val: float,
    *,
    step_size: float = 0.05,
    noise_frac: float = 0.008,
    clamp: bool = False,
) -> SensorConfig:
    """
    Helper: build a SensorConfig from range + fractional noise.

    noise_frac is expressed as a fraction of (max_val - min_val) so that
    sensors with different spans get proportionally similar jitter by default.
    """
    span = max_val - min_val
    mid = (min_val + max_val) / 2.0
    return SensorConfig(
        setpoint=mid,
        step_size=step_size,
        noise=noise_frac * span,
        min_val=min_val,
        max_val=max_val,
        clamp=(min_val, max_val) if clamp else None,
    )


DEFAULT_CONFIGS: dict[str, SensorConfig] = {
    # Temperatures: moderate noise, slow mean-reversion, unclamped
    "temp_1":    _cfg(0.0,   120.0,  step_size=0.04, noise_frac=0.010),
    "temp_2":    _cfg(0.0,   120.0,  step_size=0.04, noise_frac=0.010),
    "temp_3":    _cfg(0.0,   120.0,  step_size=0.04, noise_frac=0.010),
    # RPM: faster reversion, moderate noise
    "rpm":       _cfg(0.0,  6000.0,  step_size=0.06, noise_frac=0.008),
    # Voltage rails: tight noise, fast reversion (regulated rails are stiff)
    "voltage_1": _cfg(450.0, 510.0,  step_size=0.10, noise_frac=0.004),  # 480 V main
    "voltage_2": _cfg(110.0, 130.0,  step_size=0.10, noise_frac=0.004),  # 120 V
    "voltage_3": _cfg(4.7,   5.3,    step_size=0.10, noise_frac=0.004),  # 5 V rail
    # Currents: moderate everything
    "current_1": _cfg(0.0,   50.0,   step_size=0.05, noise_frac=0.010),
    "current_2": _cfg(0.0,   50.0,   step_size=0.05, noise_frac=0.010),
    # Flow: slow reversion, moderate noise
    "flow_1":    _cfg(0.0,  100.0,   step_size=0.04, noise_frac=0.010),
}

SENSOR_IDS: tuple[str, ...] = (
    "temp_1", "temp_2", "temp_3",
    "rpm",
    "voltage_1", "voltage_2", "voltage_3",
    "current_1", "current_2",
    "flow_1",
)

# Hardcode thresholds. 
# Otherwise, thresholds for error are directly from SensorConfig min/max values
THRESHOLDS: dict[str, tuple[float, float]] = {
        "temp_1":    (0.0,   120.0),
        "temp_2":    (0.0,   120.0),
        "temp_3":    (0.0,   120.0),
        "rpm":       (0.0,  6000.0),
        "voltage_1": (450.0, 510.0),
        "voltage_2": (110.0, 130.0),
        "voltage_3": (4.7,   5.3),
        "current_1": (0.0,   50.0),
        "current_2": (0.0,   50.0),
        "flow_1":    (0.0,  100.0),
        }


# Simulator
class SensorSimulator:
    """
    Each call to tick() advances every sensor one step:
        delta = step_size * (setpoint - current) + noise * gauss(0, 1)
        new_value = current + delta    [clamped if config.clamp is set]

    Runtime controls
    ----------------
    set_setpoint(sid, value)  — change target mean (takes effect next tick)
    set_step_size(sid, value) — change drift coefficient [0, 1]
    set_noise(sid, value)     — change Gaussian std-dev (sensor units)
    set_clamp(sid, lo, hi)    — enable/disable hard clamping
    config(sid)               — return the live SensorConfig for a sensor

    Thread safety
    -------------
    tick() is the only writer of _values.  get_snapshot() is safe to call
    from a reader thread concurrently with tick(), as dict reads in CPython
    are effectively atomic for the snapshot copy.  If tick() itself must be
    called from multiple threads, the caller should add external locking.
    """

    def __init__(
        self,
        seed: int | None = None,
        configs: dict[str, SensorConfig] | None = None,
    ) -> None:
        self._rng = random.Random(seed)
        # Deep-copy the configs so callers can't mutate the defaults
        self._configs: dict[str, SensorConfig] = {}
        for sid in SENSOR_IDS:
            src = (configs or {}).get(sid) or DEFAULT_CONFIGS.get(sid)
            if src is None:
                src = SensorConfig(setpoint=50.0)
            self._configs[sid] = SensorConfig(
                setpoint=src.setpoint,
                step_size=src.step_size,
                noise=src.noise,
                min_val=src.min_val,
                max_val=src.max_val,
                clamp=src.clamp,
            )
        # Initialise values at each sensor's setpoint
        self._values: dict[str, float] = {
            sid: self._configs[sid].setpoint for sid in SENSOR_IDS
        }
        self._tick_count: int = 0


    def tick(self) -> None:
        """
        Advance one step.  Update every sensor with:
            delta = step_size * (setpoint - current) + noise * gauss(0, 1)
        """
        for sid in SENSOR_IDS:
            cfg = self._configs[sid]
            current = self._values[sid]
            drift = cfg.step_size * (cfg.setpoint - current)
            jitter = cfg.noise * self._rng.gauss(0.0, 1.0)
            new_val = current + drift + jitter
            if cfg.clamp is not None:
                lo, hi = cfg.clamp
                new_val = max(lo, min(hi, new_val))
            self._values[sid] = new_val
        self._tick_count += 1

    def get_snapshot(self) -> dict[str, float]:
        """Return a shallow copy of current sensor values (no side effects)."""
        return dict(self._values)

    def get_value(self, sensor_id: str) -> float:
        """Return the current value of a single sensor."""
        return self._values[sensor_id]

    def get_tick_count(self) -> int:
        """Return number of ticks since creation or last reset."""
        return self._tick_count

    # Runtime

    def config(self, sensor_id: str) -> SensorConfig:
        """Return the live SensorConfig for *sensor_id* (mutable reference)."""
        return self._configs[sensor_id]

    def set_setpoint(self, sensor_id: str, value: float) -> None:
        """
        Change the target mean for *sensor_id*.

        The sensor will drift toward this value starting from the next tick.
        The value may be outside [min_val, max_val] — that is intentional.
        """
        self._configs[sensor_id].setpoint = value

    def set_step_size(self, sensor_id: str, value: float) -> None:
        """
        Set the drift coefficient for *sensor_id*.

        value should be in [0, 1]:
          0   — pure noise walk, no drift toward setpoint
          0.05 — gentle drift (default for most sensors)
          0.1  — moderate (default for stiff voltage rails)
          1.0  — snaps to setpoint ± noise each tick
        """
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"step_size must be in [0, 1], got {value!r}")
        self._configs[sensor_id].step_size = value

    def set_noise(self, sensor_id: str, value: float) -> None:
        """
        Set Gaussian noise std-dev (sensor units) for *sensor_id*.

        Set to 0 for deterministic convergence to the setpoint.
        No upper bound — callers are responsible for sensible values.
        """
        if value < 0.0:
            raise ValueError(f"noise must be ≥ 0, got {value!r}")
        self._configs[sensor_id].noise = value

    def set_clamp(
        self,
        sensor_id: str,
        lo: float | None,
        hi: float | None,
    ) -> None:
        """
        Enable or disable hard clamping for *sensor_id*.
        Pass (lo, hi) to clamp, or (None, None) to remove the clamp.
        """
        if lo is None and hi is None:
            self._configs[sensor_id].clamp = None
        elif lo is not None and hi is not None:
            if lo >= hi:
                raise ValueError(f"clamp lo ({lo}) must be < hi ({hi})")
            self._configs[sensor_id].clamp = (lo, hi)
        else:
            raise ValueError("Either both lo and hi must be set, or both must be None")

    # Apply changes to multiple sensors at a time

    def configure_many(
        self,
        sensor_ids: list[str],
        *,
        setpoint: float | None = None,
        step_size: float | None = None,
        noise: float | None = None,
    ) -> None:
        """
        Apply the same control change to a list of sensors.

        Only non-None keyword arguments are applied, so you can do:
            sim.configure_many(["temp_1", "temp_2"], setpoint=150.0)
        to push both sensors above nominal range without touching noise/step_size.
        """
        for sid in sensor_ids:
            if setpoint is not None:
                self.set_setpoint(sid, setpoint)
            if step_size is not None:
                self.set_step_size(sid, step_size)
            if noise is not None:
                self.set_noise(sid, noise)


    def reset(self, seed: int | None = None) -> None:
        """
        Reset sensor values to their current setpoints and restart tick count.
        """
        if seed is not None:
            self._rng = random.Random(seed)
        for sid in SENSOR_IDS:
            self._values[sid] = self._configs[sid].setpoint
        self._tick_count = 0

    # Nominal range checks
    # def is_in_range(self, sensor_id: str) -> bool:
    #     """Return True if the sensor's current value is within [min_val, max_val]."""
    #     cfg = self._configs[sensor_id]
    #     return cfg.min_val <= self._values[sensor_id] <= cfg.max_val
    
    #Threshold check
    def is_in_range(self, sensor_id: str) -> bool:
        """Return True if the sensor's current value is within the hardcoded THRESHOLDS."""
        lo, hi = THRESHOLDS[sensor_id]
        return lo <= self._values[sensor_id] <= hi

    def out_of_range_sensors(self) -> list[str]:
        """Return sensor IDs whose current values are outside their nominal range."""
        return [sid for sid in SENSOR_IDS if not self.is_in_range(sid)]

