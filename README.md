# Ethereum Devnet on Kubernetes (Kind)
A complete Kubernetes-based Ethereum development environment featuring:

- **Single-node Ethereum Devnet (Geth, 6-s blocks, persistent storage)**
- **Prefunded account: `0x62358b29b9e3e70ff51D88766e41a339D3e8FFff`**
- **Python async Load Generator (TPS, RPS, MGas/s, Latency, Failures)**
- **Prometheus + Grafana Observability Stack**
- **Helm charts for all components**
- **CI/CD: YAML & Python lint + Docker build + GHCR push**
- **One-click deploy for Kind cluster**

---

# Table of Contents
1. [Prerequisites](#prerequisites)  
2. [Repository Structure](#repository-structure)  
3. [Setup](#setup)  
   - Kind Cluster  
   - Build & Load Loadgen Image  
   - Deploy via Helm  
4. [Teardown](#teardown)  
5. [Verifying Block Production & Persistence](#verify-block-production)  
6. [Running Load Generator](#running-loadgen)  
7. [Grafana Dashboard](#grafana-dashboard)  
8. [Architecture & Design](#architecture)  
9. [Useful Commands](#useful-commands)

---

# Prerequisites

| Requirement | Version |
|------------|---------|
| Docker     | latest  |
| kind       | ≥ 0.20 |
| kubectl    | latest  |
| helm       | ≥ 3.10 |
| python3    | ≥ 3.9   |

---

# Repository Structure

```text
.
├── charts/
│   ├── geth-dev/              # Geth devnet chart (Clique, 6s, PVC)
│   ├── loadgen/               # Python load generator chart
│   └── monitoring/            # Prometheus + Grafana + Pushgateway
├── loadgen/
│   ├── main.py                # Async load generator
│   ├── requirements.txt
│   └── Dockerfile
└── scripts/
    ├── kind-up.sh             # Create Kind cluster
    ├── kind-down.sh           # Destroy cluster
    ├── deploy-all.sh          # Deploy monitoring + geth + loadgen
    └── teardown.sh            # Uninstall everything
```

---

# Setup

## 1. Create Kind Cluster

`scripts/kind-up.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-eth-devnet}"

kind create cluster --name "${CLUSTER_NAME}" <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30001
        hostPort: 30001
        protocol: TCP
      - containerPort: 31000
        hostPort: 31000
        protocol: TCP
EOF
```

Run:

```bash
chmod +x scripts/kind-up.sh
scripts/kind-up.sh
kubectl get nodes
```

---

## 2. Build & Load Loadgen Image

```bash
cd charts/loadgen
docker build -t eth-loadgen:local .
kind load docker-image eth-loadgen:local --name eth-devnet
cd ..
```

---

## 3. Deploy All Components

`scripts/deploy-all.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-eth}"
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

# Geth Devnet
helm upgrade --install geth-dev ./charts/geth-dev   --namespace "${NAMESPACE}" --set fullnameOverride="geth-dev"   --set nameOverride="geth-dev"

# Loadgen
helm upgrade --install loadgen ./charts/loadgen   --namespace "${NAMESPACE}"   --set image.repository="eth-loadgen"   --set image.tag="local"   --set image.pullPolicy="IfNotPresent"   --set env.RPC_URL="http://geth-dev.${NAMESPACE}.svc.cluster.local:8545"

# Monitoring Stack
helm upgrade --install monitoring ./charts/monitoring -n observability --create-namespace
```

Run:

```bash
chmod +x scripts/deploy-all.sh
scripts/deploy-all.sh

kubectl get pods -n eth
```

---

# Teardown

`scripts/teardown.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-eth}"

helm uninstall loadgen -n "${NAMESPACE}" || true
helm uninstall geth-dev -n "${NAMESPACE}" || true
helm uninstall monitoring -n "${NAMESPACE}" || true
```

Destroy cluster:

```bash
chmod +x scripts/kind-down.sh
scripts/kind-down.sh
```

---

# Verify Block Production

## Check Latest Block

```bash
kubectl port-forward svc/geth-dev -n eth 8545:8545
```

```bash
curl -s -X POST http://localhost:8545   -H "Content-Type: application/json"   -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' | jq -r '.result'
```

## Check timestamps between blocks

```bash
for i in 1 2 3 4 5; do
  curl -s -X POST http://localhost:8545     -H "Content-Type: application/json"     -d '{"jsonrpc":"2.0","method":"eth_getBlockByNumber","params":["latest",false],"id":1}' | jq '.result.timestamp'
  sleep 6
done
```

Expected difference ≈ **6 seconds**.

---

# Persistence Check

1. Note current block:

```bash
curl -s -X POST http://localhost:8545 -H "Content-Type: application/json"   -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' | jq -r '.result'
```

2. Restart the Geth pod:

```bash
kubectl delete pod -l app.kubernetes.io/name=geth-dev -n eth
kubectl get pods -n eth
```

3. Query block again — must **NOT** reset to zero.

---

# Running Loadgen

Loadgen auto-starts inside Kubernetes, but you can run locally too.

### Check metrics:

```bash
kubectl port-forward svc/loadgen -n eth 8000:8000
curl http://localhost:8000/metrics | head
```

Expected metrics:

- `loadgen_tx_total`
- `loadgen_rpc_requests_total`
- `loadgen_latency_seconds_bucket`
- `loadgen_mgas_total`
- `loadgen_in_flight`

---

# Grafana Dashboard

Port-forward Grafana:

```bash
kubectl port-forward svc/grafana -n eth 3000:3000
```

Open:

```
http://localhost:3000
```

Default credentials (unless overridden):

```
admin / admin
```

### Dashboard Panels include:

- TPS over time  
- RPC RPS over time  
- MGas/s  
- p50 / p95 latency  
- Failure rate  
- In-flight RPCs  

---

# Architecture

## Geth Devnet
- 6 s block period  
- Prefunded accounts  
- PVC-backed datadir  
- `--http.vhosts=*` for in-cluster RPC  
- Exposed on Service `geth-dev:8545`  

## Load Generator
- Async Python workers  
- Controlled TPS & concurrency  
- Measures:
  - TPS
  - RPS
  - Latency (histogram)
  - Failures
  - MGas/s
- Exposes Prometheus metrics on `:8000/metrics`

## Monitoring
- Prometheus (operator or standalone)  
- Grafana dashboard  
- Pushgateway

---

# Useful Commands

### Get all pods
```bash
kubectl get pods -n eth
```

### Describe Geth logs
```bash
kubectl logs -l app.kubernetes.io/name=geth-dev -n eth
```

### Exec into loadgen
```bash
kubectl exec -it deploy/loadgen -n eth -- sh
```

### Query latest block
```bash
kubectl port-forward svc/geth-dev -n eth 8545:8545
curl -X POST http://localhost:8545 ...
```

---

# End of README
