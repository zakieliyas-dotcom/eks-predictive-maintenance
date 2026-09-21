# Deployment Runbook

Prerequisites: AWS CLI access with permission to create EKS clusters,
IAM roles/policies, ECR repositories, and SES identities. AWS CLI,
eksctl, kubectl, helm, and Docker installed locally (or use AWS
CloudShell, which includes AWS CLI, kubectl, and Docker by default).

## 1. Install prerequisites

```bash
brew install eksctl kubectl helm      # macOS
# or see https://eksctl.io / https://kubernetes.io/docs/tasks/tools/
```

## 2. Create the cluster

```bash
cd infra
eksctl create cluster -f eksctl-cluster.yaml
aws eks update-kubeconfig --name predictive-maintenance-demo --region us-east-2
kubectl get nodes
```

Approximate cost: ~$0.20-0.30/hr for the control plane plus two
on-demand nodes. Tear down between sessions with
`eksctl delete cluster -f eksctl-cluster.yaml`.

## 3. Verify SES identities

```bash
aws sesv2 create-email-identity --email-identity <sender-address>
aws sesv2 create-email-identity --email-identity <recipient-address>
```

Each identity requires clicking a verification link sent to that
address. New AWS accounts operate in the SES sandbox, which restricts
sending to verified addresses only.

## 4. Create the IAM policy and IRSA service account

```bash
aws iam create-policy \
  --policy-name PredictiveMaintenanceAlertPolicy \
  --policy-document file://irsa-policy.json

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

eksctl create iamserviceaccount \
  --cluster predictive-maintenance-demo \
  --namespace predictive-maintenance \
  --name predictive-maintenance-sa \
  --attach-policy-arn arn:aws:iam::$ACCOUNT_ID:policy/PredictiveMaintenanceAlertPolicy \
  --approve
```

This creates the namespace, service account, and IAM role together;
`k8s/namespace-and-sa.yaml` does not need to be applied separately
when using this command.

## 5. Install Prometheus

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install kube-prom prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace -f infra/prometheus-values.yaml
kubectl get pods -n monitoring
```

## 6. Build and push the service image

```bash
cd ../src
aws ecr create-repository --repository-name predictive-maintenance --region us-east-2

aws ecr get-login-password --region us-east-2 | \
  docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.us-east-2.amazonaws.com

docker build -t predictive-maintenance .
docker tag predictive-maintenance:latest \
  $ACCOUNT_ID.dkr.ecr.us-east-2.amazonaws.com/predictive-maintenance:latest
docker push $ACCOUNT_ID.dkr.ecr.us-east-2.amazonaws.com/predictive-maintenance:latest
```

## 7. Deploy the service

```bash
cd ../k8s
# Replace <ACCOUNT_ID> in deployment.yaml with the real account ID.
# Replace ALERT_SENDER / ALERT_RECIPIENTS in configmap.yaml with the
# verified SES addresses from step 3.
kubectl apply -f configmap.yaml
kubectl apply -f deployment.yaml
kubectl logs -n predictive-maintenance deploy/predictive-maintenance -f
```

Expected output: one log line per (instance, signal) pair every 60
seconds, each with a `risk=` level. On an idle cluster this reads
`risk=ok`.

## 8. Optional: trigger a controlled detection

```bash
kubectl apply -f live-demo-leak-generator.yaml
kubectl logs -n predictive-maintenance deploy/predictive-maintenance -f
```

This deploys a pod that steadily consumes memory, producing a real
trend for the forecaster to detect. Risk should progress from `ok`
through `watch` and `warning` over several minutes. Remove it after
testing:

```bash
kubectl delete -f live-demo-leak-generator.yaml
```

## 9. Run the offline evaluation

```bash
cd ../
python3 -m venv venv && source venv/bin/activate
pip install -r src/requirements.txt
python3 scripts/run_evaluation.py
```

Runs independently of the cluster. Produces
`docs/evaluation_results.csv` and the recall / lead-time / false-positive
metrics referenced in the report.

## 10. Tear down

```bash
cd infra
eksctl delete cluster -f eksctl-cluster.yaml
```

If `eksctl delete cluster` fails due to expired or cached credentials,
delete the underlying CloudFormation stacks directly:

```bash
aws cloudformation list-stacks --region us-east-2 --stack-status-filter CREATE_COMPLETE \
  --query "StackSummaries[?contains(StackName, 'predictive-maintenance-demo')].StackName" --output table
```

Each stack may need termination protection disabled before deletion:

```bash
aws cloudformation update-termination-protection --region us-east-2 \
  --stack-name <stack-name> --no-enable-termination-protection
aws cloudformation delete-stack --region us-east-2 --stack-name <stack-name>
```

Delete node group and addon stacks first, the cluster stack last.
