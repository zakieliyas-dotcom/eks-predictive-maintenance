# infra/

## Deployment order

1. `eksctl create cluster -f eksctl-cluster.yaml` (~15-20 min)
2. Verify SES: verify the sending domain/address in the SES console (or
   `aws sesv2 create-email-identity`) and, if your account is still in the
   SES sandbox, verify recipient addresses too.
3. Create the IAM policy and IRSA role:
   ```
   aws iam create-policy \
     --policy-name PredictiveMaintenanceAlertPolicy \
     --policy-document file://irsa-policy.json

   eksctl create iamserviceaccount \
     --cluster predictive-maintenance-demo \
     --namespace predictive-maintenance \
     --name predictive-maintenance-sa \
     --attach-policy-arn arn:aws:iam::<ACCOUNT_ID>:policy/PredictiveMaintenanceAlertPolicy \
     --approve
   ```
4. Install the observability stack (Prometheus) — see `../k8s/README.md`.
5. Deploy the predictive-maintenance service — see `../k8s/README.md`.

## Why IAM scope is deliberately narrow

`irsa-policy.json` grants only `ses:SendEmail` (Phase 1) and read-only
CloudWatch access. It does **not** grant AWS IAM permissions to modify
cluster resources.

Phase 2 auto-remediation (HPA scale-up, pod restart, cordon/drain) is
implemented instead through a scoped **Kubernetes** Role/RoleBinding
(`k8s/rbac-remediation.yaml`), not AWS IAM. This is a deliberate
defense-in-depth choice: the predictive service's blast radius if
compromised or if the model misfires is limited to the Kubernetes
namespace it's allowed to act in, not the whole AWS account. This
distinction — and the trade-off it represents — is discussed in the
report under "Challenges Encountered."
