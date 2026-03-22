# Conclave Engine — Kubernetes NetworkPolicy Manifests

This directory contains Kubernetes `NetworkPolicy` manifests that enforce network
segmentation for the Conclave Engine deployment.

---

## CNI Prerequisite

**These policies are a no-op without a CNI plugin that enforces NetworkPolicy.**

The following CNI plugins are supported:

- [Calico](https://docs.tigera.io/calico/latest/about/) (recommended for production)
- [Cilium](https://cilium.io/) (recommended for eBPF-based enforcement)
- [Weave Net](https://www.weave.works/oss/net/)

The following CNI plugins **do NOT** enforce NetworkPolicy and will silently ignore
these manifests:

- `kubenet` (default GKE, EKS without CNI add-on)
- AWS VPC CNI (without the Network Policy Controller add-on enabled)
- Flannel (no NetworkPolicy support)

Verify your CNI supports NetworkPolicy before applying these manifests:

```bash
kubectl get pods -n kube-system | grep -E 'calico|cilium|weave'
```

---

## Namespace

All policies target the `conclave` namespace. Customize via a Kustomize overlay:

```yaml
# kustomization.yaml
namespace: your-namespace
resources:
  - ../../k8s/network-policies/
```

---

## Pod Label Scheme

Policies select pods using the `app.kubernetes.io/name` label. Ensure your
Deployment manifests set this label on pod templates:

| Service      | Expected label value         |
|--------------|------------------------------|
| App (API)    | `app.kubernetes.io/name: app` |
| PgBouncer    | `app.kubernetes.io/name: pgbouncer` |
| PostgreSQL   | `app.kubernetes.io/name: postgres` |
| Redis        | `app.kubernetes.io/name: redis` |
| Prometheus   | `app.kubernetes.io/name: prometheus` |
| Grafana      | `app.kubernetes.io/name: grafana` |
| AlertManager | `app.kubernetes.io/name: alertmanager` |

---

## Applying Policies

Apply the default-deny baseline first, then the allow policies:

```bash
# 1. Apply default-deny LAST (after allow policies are in place)
#    to avoid a service outage window.
kubectl apply -f k8s/network-policies/app-policy.yaml
kubectl apply -f k8s/network-policies/pgbouncer-policy.yaml
kubectl apply -f k8s/network-policies/postgres-policy.yaml
kubectl apply -f k8s/network-policies/redis-policy.yaml
kubectl apply -f k8s/network-policies/monitoring-policy.yaml

# 2. Apply default-deny AFTER all allow policies are active.
kubectl apply -f k8s/network-policies/default-deny.yaml
```

---

## Verification

```bash
# Describe all network policies in the namespace
kubectl describe networkpolicy -n conclave

# Confirm a specific policy is loaded
kubectl get networkpolicy -n conclave

# Test connectivity from the app pod to postgres (should fail after default-deny)
kubectl exec -n conclave deploy/app -- nc -zv postgres 5432
```

---

## mTLS Relationship

These NetworkPolicies enforce **which pods may communicate** at the network layer.
They complement — but do not replace — the application-level mTLS overlay
(`docker-compose.mtls.yml`). For Kubernetes deployments:

1. NetworkPolicies restrict traffic paths (L3/L4).
2. mTLS (via cert-manager or the internal CA in `scripts/generate-mtls-certs.sh`)
   provides mutual authentication and encryption at L7.

Both layers are required for full defence-in-depth. NetworkPolicies alone do not
encrypt traffic. mTLS alone does not prevent a misconfigured pod from attempting
a connection to a service it should not reach.

See ADR-0045 for the full threat model and design rationale.

---

## Monitoring Exemption

Prometheus, Grafana, and AlertManager are exempt from the mTLS requirement
(consistent with `docker-compose.mtls.yml`). These services are read-only
observability consumers with no write path to sensitive data. The `monitoring-policy.yaml`
manifest permits scraping and dashboard access without requiring mutual TLS certificates.
