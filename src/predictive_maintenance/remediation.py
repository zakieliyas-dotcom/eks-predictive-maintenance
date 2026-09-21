"""
remediation.py

Phase 2, feature-flagged off by default: HPA scale-up on a critical CPU
signal, via a scoped Kubernetes RBAC role (see k8s/rbac-remediation.yaml)
rather than AWS IAM, to limit blast radius.

Left disabled for now - acting automatically on a forecast, not a
confirmed failure, needs a burn-in period to confirm a low false-positive
rate in production first. Included here to show the extension path from
predict+notify to predict+act, not because it's ready to run unattended.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from kubernetes import client as k8s_client
from kubernetes import config as k8s_config

from .forecaster import RiskAssessment

logger = logging.getLogger("predictive_maintenance.remediation")

ENABLE_AUTO_REMEDIATION = False  # flip only after Phase 1 evaluation criteria are met


@dataclass
class RemediationConfig:
    namespace: str
    hpa_name: str
    max_replica_bump: int = 2


class KubernetesRemediator:
    """Scoped actions: bump an HPA's max replicas temporarily, or restart
    a specific deployment's pods via a rollout restart. Both actions are
    reversible and scoped to a single namespace by RBAC - this module
    cannot cordon/drain nodes or touch other namespaces even if it tried,
    because the ServiceAccount's Role doesn't grant it.
    """

    def __init__(self, cfg: RemediationConfig, in_cluster: bool = True):
        self.cfg = cfg
        if in_cluster:
            k8s_config.load_incluster_config()
        else:
            k8s_config.load_kube_config()
        self.apps_v1 = k8s_client.AppsV1Api()
        self.autoscaling_v1 = k8s_client.AutoscalingV1Api()

    def handle(self, assessment: RiskAssessment) -> str | None:
        if not ENABLE_AUTO_REMEDIATION:
            logger.info(
                "Phase 2 remediation SKIPPED (feature flag off) for %s/%s risk=%s",
                assessment.instance, assessment.signal, assessment.risk_level,
            )
            return None

        if assessment.risk_level != "critical":
            return None

        if assessment.signal == "cpu_pct":
            return self._bump_hpa_max_replicas()
        return None

    def _bump_hpa_max_replicas(self) -> str:
        hpa = self.autoscaling_v1.read_namespaced_horizontal_pod_autoscaler(
            self.cfg.hpa_name, self.cfg.namespace
        )
        new_max = hpa.spec.max_replicas + self.cfg.max_replica_bump
        hpa.spec.max_replicas = new_max
        self.autoscaling_v1.replace_namespaced_horizontal_pod_autoscaler(
            self.cfg.hpa_name, self.cfg.namespace, hpa
        )
        action = f"Bumped HPA {self.cfg.hpa_name} max_replicas to {new_max}"
        logger.warning(action)
        return action
