"""
simulate_metrics.py

Generates synthetic node utilization time series with KNOWN, labeled
degradation events, so we can measure the forecaster against ground
truth instead of eyeballing it. This is necessary because a real EKS
demo cluster won't organically produce enough real failures in the
time available for a course project to evaluate precision/recall.

Two series types are generated:
  - "gradual_leak": utilization ramps linearly toward 100% over
    `ramp_minutes` (simulates a memory leak / gradually filling disk /
    growing workload) - this is the case Phase 1 is specifically built
    to catch early.
  - "sudden_spike": a short spike that returns to baseline (simulates a
    transient load burst) - this should NOT trigger a sustained
    threshold-crossing alert, only (at most) a brief anomaly note. Used
    to measure false-positive behavior.
  - "stable": flat noisy baseline, no incident. Pure false-positive
    control group.

Ground truth label for each generated series: the wall-clock second at
which the series first crosses the 90% failure threshold (None if it
never does).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class SimulatedSeries:
    kind: str
    step_seconds: int
    values: np.ndarray
    failure_at_step: int | None  # index into values where threshold is first crossed


def gradual_leak(
    duration_minutes: int = 90,
    step_seconds: int = 30,
    start_pct: float = 40.0,
    ramp_to_pct: float = 100.0,
    noise_std: float = 1.5,
    threshold_pct: float = 90.0,
    seed: int | None = None,
) -> SimulatedSeries:
    rng = np.random.default_rng(seed)
    n = int(duration_minutes * 60 / step_seconds)
    trend = np.linspace(start_pct, ramp_to_pct, n)
    noise = rng.normal(0, noise_std, n)
    values = np.clip(trend + noise, 0, 100)
    crossing = np.argmax(values >= threshold_pct) if np.any(values >= threshold_pct) else None
    return SimulatedSeries("gradual_leak", step_seconds, values, crossing)


def sudden_spike(
    duration_minutes: int = 90,
    step_seconds: int = 30,
    baseline_pct: float = 45.0,
    spike_pct: float = 95.0,
    spike_duration_steps: int = 4,
    noise_std: float = 1.5,
    seed: int | None = None,
) -> SimulatedSeries:
    rng = np.random.default_rng(seed)
    n = int(duration_minutes * 60 / step_seconds)
    values = baseline_pct + rng.normal(0, noise_std, n)
    spike_start = n // 2
    values[spike_start:spike_start + spike_duration_steps] = spike_pct
    values = np.clip(values, 0, 100)
    # Not a sustained failure - this series is a false-positive control,
    # so failure_at_step is None regardless of the transient spike.
    return SimulatedSeries("sudden_spike", step_seconds, values, None)


def stable(
    duration_minutes: int = 90,
    step_seconds: int = 30,
    baseline_pct: float = 40.0,
    noise_std: float = 1.5,
    seed: int | None = None,
) -> SimulatedSeries:
    rng = np.random.default_rng(seed)
    n = int(duration_minutes * 60 / step_seconds)
    values = np.clip(baseline_pct + rng.normal(0, noise_std, n), 0, 100)
    return SimulatedSeries("stable", step_seconds, values, None)


def to_series(sim: SimulatedSeries, start_ts: float = 0.0) -> pd.Series:
    timestamps = start_ts + np.arange(len(sim.values)) * sim.step_seconds
    return pd.Series(sim.values, index=timestamps)
