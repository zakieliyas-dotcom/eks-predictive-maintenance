"""
service.py

Entry point for the predictive-maintenance pod. Runs a poll loop:

  every EVAL_INTERVAL_SECONDS:
    1. pull recent CPU/mem history per instance from Prometheus
    2. fit forecaster, get RiskAssessment per (instance, signal)
    3. Phase 1: email via SES if risk_level in {warning, critical}
    4. Phase 2 (flagged off): attempt remediation if risk_level == critical

Configuration is via environment variables (see k8s/deployment.yaml and
k8s/configmap.yaml) so the same image works in-cluster without code
changes - standard 12-factor practice for a containerized workload.
"""
from __future__ import annotations

import logging
import os
import time

import pandas as pd

from .alerting import AlerterConfig, SesAlerter
from .forecaster import UtilizationForecaster
from .prom_client import PromConfig, PrometheusClient
from .remediation import KubernetesRemediator, RemediationConfig
from .risk_tracker import DebouncedRiskTracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("predictive_maintenance.service")


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def build_components():
    prom = PrometheusClient(
        PromConfig(
            base_url=env("PROMETHEUS_URL", "http://kube-prom-kube-prometheus-prometheus.monitoring:9090"),
            step_seconds=int(env("STEP_SECONDS", "30")),
        )
    )
    forecaster = UtilizationForecaster(
        threshold_pct=float(env("THRESHOLD_PCT", "90")),
        warn_lead_minutes=float(env("WARN_LEAD_MINUTES", "30")),
        critical_lead_minutes=float(env("CRITICAL_LEAD_MINUTES", "10")),
    )
    alerter = SesAlerter(
        AlerterConfig(
            sender=env("ALERT_SENDER", "predictive-maintenance-alerts@yourdomain.com"),
            recipients=env("ALERT_RECIPIENTS", "oncall@yourdomain.com").split(","),
            cooldown_seconds=int(env("ALERT_COOLDOWN_SECONDS", "900")),
            aws_region=env("AWS_REGION", "us-east-1"),
        )
    )
    remediator = None
    if env("ENABLE_REMEDIATION_MODULE", "false").lower() == "true":
        remediator = KubernetesRemediator(
            RemediationConfig(
                namespace=env("TARGET_NAMESPACE", "default"),
                hpa_name=env("TARGET_HPA_NAME", "web-app"),
            )
        )
    tracker = DebouncedRiskTracker(
        required_consecutive=int(env("REQUIRED_CONSECUTIVE_CYCLES", "3"))
    )
    return prom, forecaster, alerter, remediator, tracker


def run_once(prom, forecaster, alerter, remediator, tracker, lookback_seconds: int, step_seconds: int):
    df = prom.fetch_training_window(lookback_seconds)
    if df.empty:
        logger.warning("No metrics returned from Prometheus this cycle.")
        return

    for instance, group in df.groupby("instance"):
        group = group.sort_values("timestamp").set_index("timestamp")
        for signal in ("cpu_pct", "mem_pct"):
            series = group[signal]
            assessment = forecaster.assess(
                instance=instance, signal=signal, series=series, step_seconds=step_seconds
            )
            if assessment is None:
                continue

            logger.info(
                "instance=%s signal=%s current=%.1f%% risk=%s eta_min=%s",
                assessment.instance, assessment.signal, assessment.current_value,
                assessment.risk_level, assessment.minutes_to_threshold,
            )

            confirmed = tracker.observe(assessment)
            if confirmed is None:
                continue  # not sustained yet - avoid alerting on a single noisy reading

            sent = alerter.maybe_alert(confirmed)
            if sent:
                logger.info("Alert email sent for %s/%s", instance, signal)

            if remediator is not None:
                action = remediator.handle(confirmed)
                if action:
                    logger.warning("Remediation action taken: %s", action)


def main():
    prom, forecaster, alerter, remediator, tracker = build_components()
    lookback_seconds = int(env("LOOKBACK_SECONDS", "3600"))  # 1h of history to fit trend on
    step_seconds = int(env("STEP_SECONDS", "30"))
    interval_seconds = int(env("EVAL_INTERVAL_SECONDS", "60"))

    logger.info("Predictive maintenance service starting. Eval interval=%ss", interval_seconds)
    while True:
        try:
            run_once(prom, forecaster, alerter, remediator, tracker, lookback_seconds, step_seconds)
        except Exception:
            logger.exception("Evaluation cycle failed; will retry next interval.")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
