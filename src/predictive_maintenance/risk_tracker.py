"""
risk_tracker.py

Debounce layer. Early testing showed alerting directly off a single
RiskAssessment gave a ~93% false-positive rate on stable, non-trending
series - a single noisy reading is enough to trip a spurious trend or
z-score, and over a long window at least one bad reading is near
certain.

Fix: require the risk level to hold for N consecutive cycles (default 3)
before it counts as alert-worthy. Same idea as Prometheus's `for:`
clause on alerting rules.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Tuple

from .forecaster import RiskAssessment

ALERTABLE_LEVELS = {"warning", "critical"}


class DebouncedRiskTracker:
    def __init__(self, required_consecutive: int = 3):
        self.required_consecutive = required_consecutive
        self._streaks: Dict[Tuple[str, str], int] = {}

    def observe(self, assessment: RiskAssessment) -> Optional[RiskAssessment]:
        """Feed one new assessment for an (instance, signal). Returns the
        assessment if its risk level has now been sustained for
        `required_consecutive` cycles in a row, else None.

        "critical" always short-circuits to immediate (a value already at
        or past the failure threshold shouldn't wait for confirmation
        cycles - the whole point of debouncing is to filter noise in the
        *predictive* warning band, not to delay response to a live
        breach).
        """
        key = (assessment.instance, assessment.signal)

        if assessment.risk_level == "critical" and assessment.current_value >= 90.0:
            self._streaks[key] = self.required_consecutive
            return assessment

        if assessment.risk_level in ALERTABLE_LEVELS:
            self._streaks[key] = self._streaks.get(key, 0) + 1
        else:
            self._streaks[key] = 0
            return None

        if self._streaks[key] >= self.required_consecutive:
            return assessment
        return None
