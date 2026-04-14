#!/usr/bin/env python3
"""
Sensor control panel — live view and runtime controls for SensorSimulator.

Per-sensor controls: setpoint, step size (slider), noise, clamp lo/hi.
Fault injection bar: target a sensor, push setpoint/noise/step all at once.
Tick rate and reset are global.
"""

import tkinter as tk
from tkinter import ttk

from sensor_simulator import SensorSimulator, SENSOR_IDS, DEFAULT_CONFIGS

SENSOR_META: dict[str, tuple[str, str]] = {
    "temp_1":    ("Temp 1",      "°C"),
    "temp_2":    ("Temp 2",      "°C"),
    "temp_3":    ("Temp 3",      "°C"),
    "rpm":       ("RPM",         ""),
    "voltage_1": ("480 V rail",  "V"),
    "voltage_2": ("120 V rail",  "V"),
    "voltage_3": ("5 V rail",    "V"),
    "current_1": ("Current 1",   "A"),
    "current_2": ("Current 2",   "A"),
    "flow_1":    ("Flow 1",      "L/min"),
}

DEFAULT_TICK_MS = 200


def _fmt(sid: str, value: float) -> str:
    _, unit = SENSOR_META.get(sid, (sid, ""))
    if sid == "rpm":
        return f"{value:.0f}"
    if unit == "°C":
        return f"{value:.1f}"
    return f"{value:.3f}"


# ── Per-sensor

class SensorRow:
    """One row in the sensor table: live value + all runtime controls."""

    def __init__(self, parent: tk.Widget, sid: str, sim: SensorSimulator, row: int):
        self.sid = sid
        self.sim = sim
        label, unit = SENSOR_META.get(sid, (sid, ""))
        cfg = sim.config(sid)

        col = 0

        # Sensor name
        ttk.Label(parent, text=label, width=11, anchor=tk.W).grid(
            row=row, column=col, padx=(6, 8), pady=3, sticky=tk.W)
        col += 1

        # Live value
        self.val_var = tk.StringVar(value="—")
        ttk.Label(parent, textvariable=self.val_var, width=9, anchor=tk.E,
                  font=("Courier", 10)).grid(row=row, column=col, padx=(0, 2), sticky=tk.E)
        col += 1
        ttk.Label(parent, text=unit, width=6, anchor=tk.W).grid(
            row=row, column=col, padx=(0, 8), sticky=tk.W)
        col += 1

        # In-range badge
        self.range_var = tk.StringVar(value=" OK ")
        self.range_lbl = ttk.Label(parent, textvariable=self.range_var,
                                   width=5, anchor=tk.CENTER, relief=tk.FLAT)
        self.range_lbl.grid(row=row, column=col, padx=(0, 14))
        col += 1

        # ── Setpoint ──
        ttk.Label(parent, text="Setpoint", anchor=tk.E, foreground="gray").grid(
            row=row, column=col, padx=(0, 4), sticky=tk.E)
        col += 1
        self.setpoint_var = tk.StringVar(value=f"{cfg.setpoint:.4g}")
        sp_entry = ttk.Entry(parent, textvariable=self.setpoint_var, width=9)
        sp_entry.grid(row=row, column=col, padx=(0, 14))
        sp_entry.bind("<Return>",   self._apply_setpoint)
        sp_entry.bind("<FocusOut>", self._apply_setpoint)
        col += 1

        # ── Step size (slider + readout)
        ttk.Label(parent, text="Step", anchor=tk.E, foreground="gray").grid(
            row=row, column=col, padx=(0, 4), sticky=tk.E)
        col += 1
        self.step_var = tk.DoubleVar(value=cfg.step_size)
        ttk.Scale(parent, from_=0.0, to=1.0, variable=self.step_var,
                  orient=tk.HORIZONTAL, length=100,
                  command=self._apply_step).grid(row=row, column=col, padx=(0, 2))
        col += 1
        self.step_lbl = ttk.Label(parent, text=f"{cfg.step_size:.3f}", width=5,
                                  anchor=tk.W, font=("Courier", 9))
        self.step_lbl.grid(row=row, column=col, padx=(0, 14))
        col += 1

        # Noise
        ttk.Label(parent, text="Noise", anchor=tk.E, foreground="gray").grid(
            row=row, column=col, padx=(0, 4), sticky=tk.E)
        col += 1
        self.noise_var = tk.StringVar(value=f"{cfg.noise:.4g}")
        noise_entry = ttk.Entry(parent, textvariable=self.noise_var, width=8)
        noise_entry.grid(row=row, column=col, padx=(0, 14))
        noise_entry.bind("<Return>",   self._apply_noise)
        noise_entry.bind("<FocusOut>", self._apply_noise)
        col += 1

        # Clamp
        self.clamp_var = tk.BooleanVar(value=cfg.clamp is not None)
        ttk.Checkbutton(parent, text="Clamp", variable=self.clamp_var,
                        command=self._apply_clamp).grid(row=row, column=col, padx=(0, 4))
        col += 1
        lo_default = cfg.clamp[0] if cfg.clamp else cfg.min_val
        hi_default = cfg.clamp[1] if cfg.clamp else cfg.max_val
        self.clamp_lo_var = tk.StringVar(value=f"{lo_default:.4g}")
        self.clamp_hi_var = tk.StringVar(value=f"{hi_default:.4g}")
        self.lo_entry = ttk.Entry(parent, textvariable=self.clamp_lo_var, width=8)
        self.lo_entry.grid(row=row, column=col, padx=(0, 2))
        self.lo_entry.bind("<Return>",   self._apply_clamp)
        self.lo_entry.bind("<FocusOut>", self._apply_clamp)
        col += 1
        ttk.Label(parent, text="–").grid(row=row, column=col)
        col += 1
        self.hi_entry = ttk.Entry(parent, textvariable=self.clamp_hi_var, width=8)
        self.hi_entry.grid(row=row, column=col, padx=(2, 6))
        self.hi_entry.bind("<Return>",   self._apply_clamp)
        self.hi_entry.bind("<FocusOut>", self._apply_clamp)

        self._update_clamp_state()

    # Control callbacks

    def _apply_setpoint(self, _=None):
        try:
            self.sim.set_setpoint(self.sid, float(self.setpoint_var.get()))
        except (ValueError, tk.TclError):
            pass

    def _apply_step(self, _=None):
        v = round(self.step_var.get(), 3)
        self.step_lbl.configure(text=f"{v:.3f}")
        try:
            self.sim.set_step_size(self.sid, v)
        except ValueError:
            pass

    def _apply_noise(self, _=None):
        try:
            self.sim.set_noise(self.sid, float(self.noise_var.get()))
        except (ValueError, tk.TclError):
            pass

    def _apply_clamp(self, _=None):
        self._update_clamp_state()
        if self.clamp_var.get():
            try:
                lo = float(self.clamp_lo_var.get())
                hi = float(self.clamp_hi_var.get())
                if lo < hi:
                    self.sim.set_clamp(self.sid, lo, hi)
            except (ValueError, tk.TclError):
                pass
        else:
            self.sim.set_clamp(self.sid, None, None)

    def _update_clamp_state(self):
        state = tk.NORMAL if self.clamp_var.get() else tk.DISABLED
        self.lo_entry.configure(state=state)
        self.hi_entry.configure(state=state)

    # Refresh from sim

    def refresh(self, value: float, in_range: bool):
        self.val_var.set(_fmt(self.sid, value))
        if in_range:
            self.range_var.set(" OK ")
            self.range_lbl.configure(foreground="green")
        else:
            self.range_var.set(" OOR ")
            self.range_lbl.configure(foreground="red")

    def load_config(self):
        """Reload widgets from live sim config (used after fault clear)."""
        cfg = self.sim.config(self.sid)
        self.setpoint_var.set(f"{cfg.setpoint:.4g}")
        self.step_var.set(cfg.step_size)
        self.step_lbl.configure(text=f"{cfg.step_size:.3f}")
        self.noise_var.set(f"{cfg.noise:.4g}")
        self.clamp_var.set(cfg.clamp is not None)
        if cfg.clamp:
            self.clamp_lo_var.set(f"{cfg.clamp[0]:.4g}")
            self.clamp_hi_var.set(f"{cfg.clamp[1]:.4g}")
        self._update_clamp_state()


#   ── Main application

class SensorControlApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Sensor simulator — control panel")
        self.root.resizable(True, True)

        self.sim = SensorSimulator(seed=None)
        self._tick_id: str | None = None
        self._running = False

        self._build_toolbar()
        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(fill=tk.X)
        self._build_sensor_table()
        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(6, 0))
        self._build_fault_bar()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # UI

    def _build_toolbar(self):
        bar = ttk.Frame(self.root, padding=(8, 6))
        bar.pack(fill=tk.X)

        self.run_btn = ttk.Button(bar, text="▶  Start", command=self._toggle_run, width=10)
        self.run_btn.pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(bar, text="Reset", command=self._reset).pack(side=tk.LEFT, padx=(0, 20))

        ttk.Label(bar, text="Tick (ms):").pack(side=tk.LEFT, padx=(0, 4))
        self.tick_var = tk.IntVar(value=DEFAULT_TICK_MS)
        ttk.Spinbox(bar, from_=50, to=5000, increment=50, textvariable=self.tick_var,
                    width=6).pack(side=tk.LEFT, padx=(0, 20))

        self.tick_count_var = tk.StringVar(value="Tick: 0")
        ttk.Label(bar, textvariable=self.tick_count_var,
                  foreground="gray").pack(side=tk.LEFT)

    def _build_sensor_table(self):
        # Outer frame with both scrollbars
        outer = ttk.Frame(self.root)
        outer.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        vbar = ttk.Scrollbar(outer, orient=tk.VERTICAL)
        hbar = ttk.Scrollbar(outer, orient=tk.HORIZONTAL)
        canvas = tk.Canvas(outer, highlightthickness=0,
                           yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        vbar.configure(command=canvas.yview)
        hbar.configure(command=canvas.xview)

        vbar.pack(side=tk.RIGHT,  fill=tk.Y)
        hbar.pack(side=tk.BOTTOM, fill=tk.X)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        inner = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=inner, anchor=tk.NW)

        inner.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width)
                    if e.width > canvas.bbox("all")[2] else None)

        # Mouse-wheel scroll
        def _scroll(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _scroll)

        # Column headers
        HEADERS = [
            "Sensor", "Value", "Unit", "Range",
            "Setpoint", "",
            "Step", "", "",
            "Noise", "",
            "Clamp", "Lo", "–", "Hi",
        ]
        for c, h in enumerate(HEADERS):
            ttk.Label(inner, text=h, font=("", 9, "bold"),
                      foreground="gray").grid(row=0, column=c, padx=4, pady=(4, 2), sticky=tk.W)

        ttk.Separator(inner, orient=tk.HORIZONTAL).grid(
            row=1, column=0, columnspan=len(HEADERS), sticky=tk.EW, pady=(0, 2))

        # Alternating-row backgrounds via a thin highlight
        self.sensor_rows: dict[str, SensorRow] = {}
        for i, sid in enumerate(SENSOR_IDS):
            sr = SensorRow(inner, sid, self.sim, row=i + 2)
            self.sensor_rows[sid] = sr

    def _build_fault_bar(self):
        frame = ttk.LabelFrame(self.root, text="Fault injection", padding=(8, 6))
        frame.pack(fill=tk.X, padx=6, pady=6)

        row = ttk.Frame(frame)
        row.pack(fill=tk.X)

        # Sensor selector
        ttk.Label(row, text="Sensor:").pack(side=tk.LEFT, padx=(0, 4))
        self.fault_sid_var = tk.StringVar(value=SENSOR_IDS[0])
        ttk.Combobox(row, textvariable=self.fault_sid_var, values=list(SENSOR_IDS),
                     width=12, state="readonly").pack(side=tk.LEFT, padx=(0, 16))

        # Setpoint override
        ttk.Label(row, text="Setpoint:").pack(side=tk.LEFT, padx=(0, 4))
        self.fi_sp = tk.StringVar()
        ttk.Entry(row, textvariable=self.fi_sp, width=9).pack(side=tk.LEFT, padx=(0, 12))

        # Noise override
        ttk.Label(row, text="Noise:").pack(side=tk.LEFT, padx=(0, 4))
        self.fi_noise = tk.StringVar()
        ttk.Entry(row, textvariable=self.fi_noise, width=7).pack(side=tk.LEFT, padx=(0, 12))

        # Step override
        ttk.Label(row, text="Step:").pack(side=tk.LEFT, padx=(0, 4))
        self.fi_step = tk.StringVar()
        ttk.Entry(row, textvariable=self.fi_step, width=6).pack(side=tk.LEFT, padx=(0, 16))

        ttk.Button(row, text="Inject",           command=self._inject_fault).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(row, text="Restore defaults", command=self._clear_fault).pack(side=tk.LEFT)

    #   ── Simulation control  

    def _toggle_run(self):
        if self._running:
            self._stop()
        else:
            self._start()

    def _start(self):
        self._running = True
        self.run_btn.configure(text="■  Stop")
        self._schedule_tick()

    def _stop(self):
        self._running = False
        self.run_btn.configure(text="▶  Start")
        if self._tick_id:
            self.root.after_cancel(self._tick_id)
            self._tick_id = None

    def _schedule_tick(self):
        try:
            ms = max(50, int(self.tick_var.get()))
        except (ValueError, tk.TclError):
            ms = DEFAULT_TICK_MS
        self._tick_id = self.root.after(ms, self._do_tick)

    def _do_tick(self):
        if not self._running:
            return
        self.sim.tick()
        snap = self.sim.get_snapshot()
        for sid in SENSOR_IDS:
            self.sensor_rows[sid].refresh(snap[sid], self.sim.is_in_range(sid))
        self.tick_count_var.set(f"Tick: {self.sim.get_tick_count()}")
        self._schedule_tick()

    def _reset(self):
        self.sim.reset()
        self.tick_count_var.set("Tick: 0")

    # ── Fault injection 

    def _inject_fault(self):
        sid = self.fault_sid_var.get()
        row = self.sensor_rows[sid]
        changed = False

        def _try(fn, var_str):
            nonlocal changed
            v = var_str.get().strip()
            if v:
                try:
                    fn(float(v))
                    changed = True
                except ValueError:
                    pass

        _try(lambda v: self.sim.set_setpoint(sid, v),  self.fi_sp)
        _try(lambda v: self.sim.set_noise(sid, v),     self.fi_noise)
        _try(lambda v: self.sim.set_step_size(sid, v), self.fi_step)

        if changed:
            row.load_config()

    def _clear_fault(self):
        sid = self.fault_sid_var.get()
        default = DEFAULT_CONFIGS[sid]
        self.sim.set_setpoint(sid,  default.setpoint)
        self.sim.set_noise(sid,     default.noise)
        self.sim.set_step_size(sid, default.step_size)
        self.sim.set_clamp(sid, None, None)
        self.sensor_rows[sid].load_config()


    def _on_close(self):
        self._stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    app = SensorControlApp()
    app.run()


if __name__ == "__main__":
    main()