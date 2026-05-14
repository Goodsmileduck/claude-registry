---
name: kubernetes-operations
description: Debugs Kubernetes pods and controllers — FailedCreate, ImagePullBackOff, init-container failures, probe flapping, missing service endpoints, GKE NEG readiness. Use when a pod is not Running, a Deployment/StatefulSet shows FailedCreate, image pulls fail, or services lack endpoints.
---

# Kubernetes — pod debug decision tree

For ArgoCD-managed resources, also check the `argocd-operations` skill: direct mutations are reverted by `selfHeal` within ~3 minutes.

## When to invoke

The pod's `STATUS` column tells you which branch to take. Always start with:

```bash
kubectl config current-context                  # confirm cluster/env BEFORE anything
kubectl describe pod <pod> -n <ns> | tail -40   # events at the bottom
kubectl logs <pod> -n <ns> [-c <container>] [--previous]
kubectl get events -n <ns> --sort-by=.lastTimestamp | tail -20
```

The `Events:` section at the end of `describe` is the single highest-signal source. Read it before anything else.

## Pre-flight: is this resource Argo-managed?

Before any `kubectl edit`/`patch`/`apply -f` fix, check whether the resource is GitOps-owned:

```bash
kubectl get <kind> <name> -n <ns> -o jsonpath='{.metadata.labels}{"\n"}{.metadata.annotations}{"\n"}'
# managed-by indicators: argocd.argoproj.io/tracking-id, meta.helm.sh/release-name, app.kubernetes.io/managed-by
```

If managed: fix the source (chart/values/kustomization), not the cluster. See the `argocd-operations` skill.

## Branch 1 — Pod never created (`FailedCreate` on the controller)

The pod doesn't exist yet; the ReplicaSet/StatefulSet/Job can't create it.

```bash
# Look at the controller's events, not the pod's (the pod isn't there)
kubectl describe rs <rs-name> -n <ns> | tail -30
kubectl describe statefulset <ss> -n <ns> | tail -30
```

| Event message contains | Cause | Fix |
|---|---|---|
| `forbidden: violates PodSecurity` | Pod Security Admission rejecting the spec at the namespace's PSA level (`restricted`, `baseline`) | Either relax the namespace label `pod-security.kubernetes.io/enforce` or fix the pod spec (drop capabilities, `runAsNonRoot`, etc.) |
| `exceeded quota` / `forbidden: exceeded quota` | ResourceQuota in the namespace | `kubectl describe resourcequota -n <ns>` to see what's exhausted |
| `admission webhook "..." denied the request` | A ValidatingWebhookConfiguration rejected the pod | `kubectl get validatingwebhookconfigurations`, inspect the named webhook's policy; the webhook's controller logs explain why |
| `serviceaccount "X" not found` | SA referenced in pod spec doesn't exist in this namespace | Create the SA, or fix the spec; common with Helm chart values mismatch |
| `persistentvolumeclaim "X" not found` (StatefulSet) | The PVC template name doesn't match what was provisioned, or volumeClaimTemplate changed | StatefulSet PVCs are immutable; delete and recreate, or revert the template |
| `error looking up service account ... no token` | ServiceAccount exists but no token Secret (IRSA / GKE WI setups) | Check Workload Identity bindings (GCP) or service-account token projection |

## Branch 2 — Pod is `Pending`

Pod was created but never scheduled. Look at scheduler events:

```bash
kubectl describe pod <pod> -n <ns> | grep -A 10 Events
```

| Event reason | Cause | Diagnostic |
|---|---|---|
| `FailedScheduling: 0/N nodes are available: insufficient cpu/memory` | No node has free capacity | `kubectl describe nodes \| grep -E "Name:\|Allocatable\|Allocated"`; consider cluster autoscaler logs |
| `FailedScheduling: ... node(s) didn't match Pod's node affinity/selector` | Affinity / nodeSelector doesn't match any node | Compare pod's `affinity`/`nodeSelector` against `kubectl get nodes --show-labels` |
| `FailedScheduling: ... node(s) had untolerated taint` | Tainted nodes (e.g. GKE Autopilot system pools, spot-only pools) | Add matching `tolerations` to the pod, or schedule to a different pool |
| `FailedScheduling: ... volume node affinity conflict` | The PV is in zone A, no node in zone A has capacity | Common with regional GKE + zonal PD; need a node in the PV's zone |
| `FailedScheduling: ... topology spread constraint(s) not satisfied` | `topologySpreadConstraints` can't be honored | Inspect the constraint's `maxSkew`/`whenUnsatisfiable`; consider `ScheduleAnyway` |

## Branch 3 — Image pull failures (`ImagePullBackOff`, `ErrImagePull`)

Container status `waiting.reason` reveals the specific failure:

```bash
kubectl get pod <pod> -n <ns> -o jsonpath='{.status.containerStatuses[*].state.waiting}{"\n"}'
```

| Reason | Meaning | Fix |
|---|---|---|
| `ErrImagePull` | First pull failed (registry auth, image absent, network) | Read the next event for the underlying message |
| `ImagePullBackOff` | Repeated `ErrImagePull` — kubelet is backing off | Same as `ErrImagePull`; the backoff just means it's been failing a while |
| `ImageInspectError` | Image manifest fetched but inspection failed (often signature/policy verification) | Check sigstore / image policy controllers (Kyverno, Gatekeeper, Connaisseur) |
| Event: `manifest unknown` / `not found` | Tag or digest doesn't exist in the registry | Verify the tag with `docker manifest inspect <ref>` |
| Event: `unauthorized` / `denied` | imagePullSecret missing, expired, or wrong | Inspect: `kubectl get sa <sa> -n <ns> -o yaml \| grep imagePullSecrets`; verify secret with `kubectl get secret <s> -n <ns> -o jsonpath='{.data.\.dockerconfigjson}' \| base64 -d` |
| Event: `toomanyrequests` (Docker Hub) | Anonymous rate limit hit (100/6h) | Use authenticated pulls or a registry mirror |
| Event: `dial tcp: i/o timeout` | Network egress to registry blocked | Check NetworkPolicies, NAT, firewall, private cluster master-auth |

For private GCR/Artifact Registry on GKE: the node's SA needs `roles/artifactregistry.reader` on the AR repo, or use Workload Identity bound to a GSA that has it. Anonymous pulls from `gcr.io/google-containers/*` style public mirrors don't need auth.

## Branch 4 — Init container failing

The pod status shows `Init:CrashLoopBackOff`, `Init:Error`, or `Init:0/N`. Containers run sequentially; one failure blocks the rest.

```bash
# Identify which init container by index
kubectl get pod <pod> -n <ns> -o jsonpath='{.status.initContainerStatuses[*].name}{"\n"}'
# Then read its logs
kubectl logs <pod> -n <ns> -c <init-container-name>
kubectl logs <pod> -n <ns> -c <init-container-name> --previous   # if it crashed
```

| State | Meaning |
|---|---|
| `Init:0/3` Pending for long time | Init container hasn't started — image pull failure (see branch 3) or volume mount failure |
| `Init:CrashLoopBackOff` | Init container ran and exited non-zero, repeatedly — read its logs |
| `Init:Error` | Most recent run exited non-zero, no backoff yet — read logs with `--previous` after the next attempt |

Common init-container roles and their failure modes:
- **Wait-for-DB / wait-for-service** scripts → DNS or network failure; check `kubectl get svc -n <ns>`, run `nslookup <svc>` from a debug pod
- **Volume permission fixers** (`chown`) → SecurityContext.fsGroup mismatch or readOnly volume
- **Secret materializers** (Vault Agent, External Secrets job-style) → the upstream secret source isn't ready or unauthorized

## Branch 5 — Main container `CrashLoopBackOff`

The pod started but the container exits repeatedly.

```bash
kubectl logs <pod> -n <ns> --previous       # logs from the crashed instance
kubectl get pod <pod> -n <ns> -o jsonpath='{.status.containerStatuses[*].lastState.terminated}{"\n"}' | jq .
```

The `terminated` block shows `exitCode`, `reason`, and `message`:

| `exitCode` / `reason` | Likely cause |
|---|---|
| `137` + reason `OOMKilled` | Hit container memory limit — raise `resources.limits.memory` or fix leak |
| `139` (SIGSEGV) | Segfault — application bug |
| `143` (SIGTERM) | Graceful termination, but exiting fast enough to look like a crash — check probe behavior (branch 6) |
| `1` + app-level log | Read the logs; application config or startup error |
| reason `Error` exit `255` | Often crashloop right at PID 1 — entrypoint script bug |

## Branch 6 — Probe-induced flapping (`Running` but restarting)

Pod restarts but logs look fine. Suspect liveness probe killing a healthy container.

```bash
kubectl describe pod <pod> -n <ns> | grep -A 5 "Liveness\|Readiness\|Startup"
kubectl get events -n <ns> --field-selector involvedObject.name=<pod> | grep -i probe
```

Probe rules of thumb (the trap that keeps showing up):

- **Startup probe** exists to protect slow-starting containers from liveness — set this for anything that takes >10s to be ready. Without it, slow startups get killed before they can answer the liveness probe.
- **Liveness probe** kills the container on failure. Use it only for "deadlocked process" cases. Default to *no* liveness probe if you're not sure — readiness alone is safer.
- **Readiness probe** controls service traffic only (the container stays alive, just leaves the endpoint set). Use it freely.
- A single HTTP endpoint serving as both liveness and readiness is fine, but make sure it doesn't depend on downstream services — otherwise a temporary DB blip kills your pods.

Common misconfigurations:
- `initialDelaySeconds` too small + no startup probe → liveness kills during startup → crashloop that looks application-side
- `periodSeconds` smaller than the endpoint's typical latency → false negatives
- Probe scheme `HTTPS` against a container serving plain HTTP (or vice versa)

## Branch 7 — Pod `Running` and `Ready`, but service has no endpoints (GKE NEG case)

The pod is healthy in every way, but `kubectl get endpoints <svc>` is empty, OR the GKE container-native LB shows the backend as unhealthy.

```bash
kubectl get pod <pod> -n <ns> -o yaml | grep -A 3 "readinessGates\|conditions:"
kubectl get svc <svc> -n <ns> -o yaml | grep -A 5 "annotations:\|selector:"
kubectl get pod <pod> -n <ns> -o jsonpath='{.metadata.annotations.cloud\.google\.com/neg-status}{"\n"}'
```

| Check | What it should show |
|---|---|
| Service `selector` matches pod labels | `kubectl get pod <pod> -n <ns> --show-labels`; compare to svc selector |
| Pod has `cloud.google.com/neg-status` annotation | Indicates GKE picked it up for a Network Endpoint Group; absence means standalone NEG isn't configured |
| Pod has `readinessGates: cloud.google.com/load-balancer-neg-ready` | The pod won't be Ready until the LB health check passes — required for proper rolling updates with container-native LB |
| BackendConfig referenced by service annotation `cloud.google.com/backend-config` | `kubectl get backendconfig -n <ns>` — the health-check path/port must reach the pod |

The most common GKE NEG failure mode: BackendConfig health check path is `/` but the app serves on `/healthz`, so the LB marks the backend unhealthy even though Kubernetes thinks the pod is Ready. Add a `readinessGate` so the pod's Ready status reflects the LB's view, not just Kubernetes'.

## When you've exhausted the tree

If `describe pod`, `logs`, `--previous`, events, and the controller's events all look clean and the pod still misbehaves:

- Check NetworkPolicies in the namespace (`kubectl get netpol -n <ns>`) — egress to DNS, kube-api, or other services may be silently blocked.
- Check resource pressure on the node (`kubectl describe node <node> | grep -E "Conditions:\|MemoryPressure\|DiskPressure\|PIDPressure"`).
- Check container runtime logs on the node (last resort; requires node access).
- Pull a debug copy of the pod: `kubectl debug <pod> -n <ns> --image=busybox --target=<container>` to inspect the namespace from inside the same network/PID context.
