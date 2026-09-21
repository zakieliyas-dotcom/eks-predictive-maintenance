"""
forecaster.py

Forecasting core: Holt's linear trend exponential smoothing (statsmodels)
instead of a deep learning model. Per-node CPU/RAM series here are short
and noisy, so an LSTM would likely overfit without any real accuracy gain.

Produces three things per assessment: a trend forecast with a 95%
interval, a time-to-threshold estimate, and an anomaly z-score for
sudden spikes the trend alone would smooth over.

Kept interpretable on purpose - "trending up 1.8%/min, ~11 min to 90%"
is something an on-call engineer can act on immediately.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import Holt


@dataclass
class RiskAssessment:
    instance: str
    signal: str
    current_value: float
    trend_per_minute: float
    forecast_horizon_minutes: int
    forecast_value: float
    forecast_upper_95: float
    minutes_to_threshold: Optional[float]  # None if not trending toward threshold
    anomaly_zscore: float
    risk_level: str  # "ok" | "watch" | "warning" | "critical"


class UtilizationForecaster:
    """Fits a short-horizon trend model to one instance's signal series
    and produces a RiskAssessment against a configured threshold.
    """

    def __init__(
        self,
        threshold_pct: float = 90.0,
        warn_lead_minutes: float = 30.0,
        critical_lead_minutes: float = 10.0,
        min_points: int = 20,
    ):
        self.threshold_pct = threshold_pct
        self.warn_lead_minutes = warn_lead_minutes
        self.critical_lead_minutes = critical_lead_minutes
        self.min_points = min_points

    def assess(
        self,
        instance: str,
        signal: str,
        series: pd.Series,  # index = timestamp (seconds), value = pct utilization
        step_seconds: int,
        forecast_horizon_minutes: int = 30,
    ) -> Optional[RiskAssessment]:
        series = series.dropna()
        if len(series) < self.min_points:
            return None  # not enough history to fit yet

        values = series.values.astype(float)

        # 1. Fit Holt's linear trend model (additive trend, no seasonality
        #    at this horizon — 30s steps over a short window won't reveal
        #    daily seasonality; that's handled by using enough lookback
        #    at training time rather than modeling season here).
        model = Holt(values, initialization_method="estimated").fit(
            optimized=True
        )
        horizon_steps = int((forecast_horizon_minutes * 60) / step_seconds)
        forecast = model.forecast(horizon_steps)
        forecast_value = float(forecast[-1])

        # Prediction interval via residual std (simple, robust for this scale)
        resid_std = float(np.std(model.resid))
        forecast_upper_95 = forecast_value + 1.645 * resid_std * np.sqrt(horizon_steps)

        # 2. Trend per minute. Rather than reaching into internal component
        #    arrays (whose availability/shape varies across statsmodels
        #    versions), we derive the trend directly and robustly from the
        #    forecast itself: the average per-step change across the
        #    forecast horizon.
        forecast_diffs = np.diff(forecast)
        trend_per_step = float(np.mean(forecast_diffs)) if len(forecast_diffs) > 0 else 0.0
        trend_per_minute = trend_per_step * (60 / step_seconds)

        # 3. Time to threshold via linear extrapolation from current level
        current_value = float(values[-1])
        minutes_to_threshold = None
        if trend_per_minute > 0 and current_value < self.threshold_pct:
            minutes_to_threshold = (self.threshold_pct - current_value) / trend_per_minute

        # 4. Anomaly z-score of latest point vs. recent smoothed baseline
        baseline = float(np.mean(values[-min(len(values), 20):-1])) if len(values) > 1 else current_value
        baseline_std = float(np.std(values[-min(len(values), 20):-1])) or 1e-6
        anomaly_zscore = (current_value - baseline) / baseline_std

        risk_level = self._classify(minutes_to_threshold, anomaly_zscore, current_value)

        return RiskAssessment(
            instance=instance,
            signal=signal,
            current_value=round(current_value, 2),
            trend_per_minute=round(trend_per_minute, 4),
            forecast_horizon_minutes=forecast_horizon_minutes,
            forecast_value=round(forecast_value, 2),
            forecast_upper_95=round(forecast_upper_95, 2),
            minutes_to_threshold=(
                round(minutes_to_threshold, 1) if minutes_to_threshold is not None else None
            ),
            anomaly_zscore=round(anomaly_zscore, 2),
            risk_level=risk_level,
        )

    def _classify(
        self,
        minutes_to_threshold: Optional[float],
        anomaly_zscore: float,
        current_value: float,
    ) -> str:
        if current_value >= self.threshold_pct:
            return "critical"
        if minutes_to_threshold is not None:
            if minutes_to_threshold <= self.critical_lead_minutes:
                return "critical"
            if minutes_to_threshold <= self.warn_lead_minutes:
                return "warning"
        if abs(anomaly_zscore) >= 3.0:
            return "warning"
        if abs(anomaly_zscore) >= 2.0:
            return "watch"
        return "ok"
