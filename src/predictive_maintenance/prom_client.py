"""
prom_client.py

Thin wrapper around the Prometheus HTTP API for pulling node/pod
CPU and memory utilization time series.

We deliberately keep this dependency-light (just `requests`) rather than
pulling in a full Prometheus client SDK, since the only operation we need
is range queries.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import requests

# PromQL expressions for the two signals Phase 1 forecasts.
QUERIES = {
    "cpu_pct": (
        '100 - (avg by (instance) '
        '(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
    ),
    "mem_pct": (
        '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100'
    ),
}


@dataclass
class PromConfig:
    base_url: str  # e.g. http://kube-prom-kube-prometheus-prometheus.monitoring:9090
    step_seconds: int = 30
    timeout_seconds: int = 10


class PrometheusClient:
    def __init__(self, config: PromConfig):
        self.config = config

    def range_query(self, promql: str, start_ts: float, end_ts: float) -> pd.DataFrame:
        """Runs a PromQL range query and returns a tidy DataFrame:
        columns = [timestamp, instance, value]
        """
        resp = requests.get(
            f"{self.config.base_url}/api/v1/query_range",
            params={
                "query": promql,
                "start": start_ts,
                "end": end_ts,
                "step": self.config.step_seconds,
            },
            timeout=self.config.timeout_seconds,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") != "success":
            raise RuntimeError(f"Prometheus query failed: {payload}")

        rows = []
        for series in payload["data"]["result"]:
            instance = series["metric"].get("instance", "unknown")
            for ts, val in series["values"]:
                rows.append({"timestamp": float(ts), "instance": instance, "value": float(val)})

        return pd.DataFrame(rows, columns=["timestamp", "instance", "value"])

    def fetch_signal(self, signal: str, lookback_seconds: int) -> pd.DataFrame:
        """Fetch one of QUERIES over the last `lookback_seconds`."""
        if signal not in QUERIES:
            raise ValueError(f"Unknown signal '{signal}'. Known: {list(QUERIES)}")
        now = time.time()
        return self.range_query(QUERIES[signal], now - lookback_seconds, now)

    def fetch_training_window(self, lookback_seconds: int) -> pd.DataFrame:
        """Fetch both CPU and memory signals and merge into one wide DataFrame
        indexed by (timestamp, instance): columns cpu_pct, mem_pct.
        """
        frames = []
        for signal in QUERIES:
            df = self.fetch_signal(signal, lookback_seconds)
            df = df.rename(columns={"value": signal})
            frames.append(df.set_index(["timestamp", "instance"]))
        merged = frames[0].join(frames[1:], how="outer").reset_index()
        return merged.sort_values(["instance", "timestamp"])
