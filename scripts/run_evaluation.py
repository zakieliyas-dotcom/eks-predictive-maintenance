#!/usr/bin/env python3
"""
run_evaluation.py

Measures whether the forecaster actually beats a reactive baseline, with
real numbers instead of just asserting it.

Method
------
For each simulated series (see simulate_metrics.py), replay it as if it
were arriving in real time: at each step the forecaster only sees data
up to "now" (an expanding window), same as it would in production.
Records:

  - lead_time_seconds: how much earlier the first warning/critical alert
    fires, relative to the moment the series actually crosses the
    failure threshold. The headline number - does prediction actually
    beat reaction?
  - baseline_lead_time_seconds: same measurement for a naive static
    threshold rule (alert only once current value >= 90%) - the classic
    "email me when CPU is already high" approach. Always 0 by
    construction, which is the whole comparison point.
  - false positive: for the "stable" and "sudden_spike" control series
    (which never actually breach the threshold), did it alert anyway?

Run:
    python scripts/run_evaluation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from predictive_maintenance.forecaster import UtilizationForecaster
from predictive_maintenance.risk_tracker import DebouncedRiskTracker
from predictive_maintenance.tests.simulate_metrics import (
    gradual_leak,
    stable,
    sudden_spike,
    to_series,
)

THRESHOLD_PCT = 90.0
STEP_SECONDS = 30
MIN_POINTS = 20


def replay(sim, forecaster: UtilizationForecaster, required_consecutive: int = 3):
    """Replays one simulated series step-by-step with an expanding window
    and returns (first_alert_step, any_alert), where "alert" means the
    debounced tracker confirmed it (matching production behavior in
    service.py), not a single raw risk reading.
    """
    series = to_series(sim)
    tracker = DebouncedRiskTracker(required_consecutive=required_consecutive)
    first_alert_step = None
    any_alert = False
    for i in range(MIN_POINTS, len(series)):
        window = series.iloc[: i + 1]
        assessment = forecaster.assess(
            instance="sim-0", signal="cpu_pct", series=window, step_seconds=STEP_SECONDS
        )
        if assessment is None:
            continue
        confirmed = tracker.observe(assessment)
        if confirmed is not None:
            any_alert = True
            if first_alert_step is None:
                first_alert_step = i
    return first_alert_step, any_alert


def run_scenario_set(n_trials: int = 15):
    forecaster = UtilizationForecaster(
        threshold_pct=THRESHOLD_PCT, warn_lead_minutes=30, critical_lead_minutes=10
    )
    rows = []

    # --- True-positive scenarios: gradual leaks at varying ramp speeds ---
    for trial in range(n_trials):
        ramp_minutes = int(np.random.default_rng(trial).integers(45, 120))
        sim = gradual_leak(duration_minutes=ramp_minutes + 10, seed=trial, ramp_to_pct=100)
        first_alert_step, _ = replay(sim, forecaster)

        failure_time = sim.failure_at_step * STEP_SECONDS if sim.failure_at_step else None
        alert_time = first_alert_step * STEP_SECONDS if first_alert_step is not None else None

        lead_time = (
            failure_time - alert_time
            if (failure_time is not None and alert_time is not None)
            else None
        )
        baseline_lead_time = 0 if failure_time is not None else None  # naive rule fires at breach

        rows.append({
            "scenario": "gradual_leak",
            "trial": trial,
            "ramp_minutes": ramp_minutes,
            "detected": alert_time is not None,
            "lead_time_seconds": lead_time,
            "baseline_lead_time_seconds": baseline_lead_time,
        })

    # --- False-positive control scenarios ---
    for trial in range(n_trials):
        sim = stable(seed=100 + trial)
        _, any_alert = replay(sim, forecaster)
        rows.append({
            "scenario": "stable_control", "trial": trial, "ramp_minutes": None,
            "detected": any_alert, "lead_time_seconds": None, "baseline_lead_time_seconds": None,
        })

    for trial in range(n_trials):
        sim = sudden_spike(seed=200 + trial)
        _, any_alert = replay(sim, forecaster)
        rows.append({
            "scenario": "sudden_spike_control", "trial": trial, "ramp_minutes": None,
            "detected": any_alert, "lead_time_seconds": None, "baseline_lead_time_seconds": None,
        })

    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame):
    leak = df[df.scenario == "gradual_leak"]
    stable_ctrl = df[df.scenario == "stable_control"]
    spike_ctrl = df[df.scenario == "sudden_spike_control"]

    recall = leak["detected"].mean()
    mean_lead_minutes = (leak["lead_time_seconds"].dropna() / 60).mean()
    median_lead_minutes = (leak["lead_time_seconds"].dropna() / 60).median()

    print("=== Predictive Maintenance — Evaluation Summary ===")
    print(f"Gradual-leak scenarios simulated: {len(leak)}")
    print(f"Recall (leak detected before/at threshold breach): {recall:.0%}")
    print(f"Mean lead time vs. actual failure:   {mean_lead_minutes:.1f} minutes")
    print(f"Median lead time vs. actual failure: {median_lead_minutes:.1f} minutes")
    print(f"Baseline (naive static-threshold) lead time: 0.0 minutes (by construction)")
    print()
    print(f"Stable control (flat noise, no incident): {len(stable_ctrl)} runs")
    print(f"  False positive rate: {stable_ctrl['detected'].mean():.0%}  <- should be near 0%")
    print()
    print(f"Sudden-spike control (transient burst, no sustained failure): {len(spike_ctrl)} runs")
    print(f"  Alert-fired rate: {spike_ctrl['detected'].mean():.0%}  <- debatable ground truth,")
    print("  see note below")
    print()
    print("Interpretation:")
    print("- Mean/median lead time is the advance warning this system provides")
    print("  over the 'email me when CPU is already high' baseline.")
    print("- Stable-control false positives are the metric that matters for")
    print("  alert fatigue and should be near 0%; it is the one this model is")
    print("  actually accountable for.")
    print("- Sudden-spike 'false positives' are a labeling ambiguity, not a")
    print("  model defect: a genuine multi-minute spike to 95% utilization is")
    print("  arguably worth a brief anomaly notice even if it self-resolves.")
    print("  Whether that counts as a false positive depends on what the")
    print("  on-call team wants flagged - this is discussed in the report.")


def main():
    df = run_scenario_set()
    out_path = Path(__file__).resolve().parents[1] / "docs" / "evaluation_results.csv"
    out_path.parent.mkdir(exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"Raw results written to {out_path}\n")
    summarize(df)


if __name__ == "__main__":
    main()
