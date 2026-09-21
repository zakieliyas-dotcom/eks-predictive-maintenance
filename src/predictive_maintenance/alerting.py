"""
alerting.py

Email alerts via Amazon SES. Two things beyond a plain send_email call:
cooldown/dedup per (instance, signal) so a sustained risk period doesn't
spam one email per cycle, and a body that states current value, trend,
and time-to-threshold - not just "ALERT" - so it's actually actionable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Tuple

import boto3

from .forecaster import RiskAssessment

ALERTABLE_LEVELS = {"warning", "critical"}


@dataclass
class AlerterConfig:
    sender: str
    recipients: list[str]
    cooldown_seconds: int = 900  # 15 min - don't re-alert on the same
                                   # (instance, signal) risk within this window
    aws_region: str = "us-east-1"


class SesAlerter:
    def __init__(self, config: AlerterConfig, ses_client=None):
        self.config = config
        self.ses = ses_client or boto3.client("ses", region_name=config.aws_region)
        self._last_sent: Dict[Tuple[str, str], float] = {}

    def _in_cooldown(self, key: Tuple[str, str], now: float) -> bool:
        last = self._last_sent.get(key)
        return last is not None and (now - last) < self.config.cooldown_seconds

    def maybe_alert(self, assessment: RiskAssessment) -> bool:
        """Sends an email if the assessment warrants one and we're not in
        cooldown. Returns True if an email was sent.
        """
        if assessment.risk_level not in ALERTABLE_LEVELS:
            return False

        key = (assessment.instance, assessment.signal)
        now = time.time()
        if self._in_cooldown(key, now):
            return False

        subject, body = self._render(assessment)
        self.ses.send_email(
            Source=self.config.sender,
            Destination={"ToAddresses": self.config.recipients},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Text": {"Data": body}},
            },
        )
        self._last_sent[key] = now
        return True

    @staticmethod
    def _render(a: RiskAssessment) -> tuple[str, str]:
        subject = f"[{a.risk_level.upper()}] {a.instance} {a.signal} trending toward threshold"

        eta = (
            f"~{a.minutes_to_threshold} minutes"
            if a.minutes_to_threshold is not None
            else "not currently on a threshold-crossing trend"
        )

        body = (
            f"Predictive maintenance alert\n"
            f"-----------------------------\n"
            f"Instance:            {a.instance}\n"
            f"Signal:              {a.signal}\n"
            f"Current value:       {a.current_value}%\n"
            f"Trend:               {a.trend_per_minute:+.3f} %/min\n"
            f"{a.forecast_horizon_minutes}-min forecast:    {a.forecast_value}% "
            f"(upper 95% bound: {a.forecast_upper_95}%)\n"
            f"Estimated time to threshold: {eta}\n"
            f"Anomaly z-score:     {a.anomaly_zscore}\n"
            f"Risk level:          {a.risk_level}\n\n"
            f"This is a forecast, not a threshold breach that has already "
            f"happened - you are receiving this before the node reaches "
            f"critical utilization, not after.\n"
        )
        return subject, body
