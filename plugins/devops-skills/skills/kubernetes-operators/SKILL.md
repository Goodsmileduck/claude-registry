---
name: kubernetes-operators
description: Designs and audits Kubernetes Operators — CRD shape, reconcile-loop correctness, finalizer and status-subresource handling, OperatorHub capability levels, framework choice. Use when building a controller for a CRD, reviewing an operator for capability gaps, or designing the API surface of a Custom Resource. Not for general pod debugging — see kubernetes-operations.
---

# Kubernetes Operators — CRD and reconcile review

For pod-level debugging (CrashLoopBackOff, ImagePullBackOff, scheduling failures) see the `kubernetes-operations` skill. For Argo-managed deployments see `argocd-operations`.

Most "operator bugs" are reconcile-loop bugs, not Kubernetes bugs: missing finalizers, blocking calls, no requeue on transient error, status drift, RBAC over-grant. The three Python scripts shipped here catch the deterministic subset before code reaches a cluster.

## When to invoke

Run the analyzers first — they're stdlib Python, fast, and surface most routine issues:

```bash
SKILL=plugins/devops-skills/skills/kubernetes-operators

python3 "$SKILL/scripts/crd_validator.py"            --crd config/crd/
python3 "$SKILL/scripts/reconcile_lint.py"           --controller controllers/
python3 "$SKILL/scripts/operator_capability_audit.py" --operator-dir .
```

All three accept `--format json`. Triage by severity: FAIL blocks merge, WARN files an issue.

## Pre-flight: is an operator the right shape?

Operators are for *stateful, lifecycle-managed* workloads. Reach for one when:

- The thing being managed has an external API (RDS, Kafka topics, GitHub repos).
- Day-2 operations are non-trivial (backup, restore, version upgrade, failover).
- A Helm chart + bash isn't enough — you need a controller that *observes and re-acts*.

Don't reach for an operator when:

| Want | Better tool |
|---|---|
| Run a workload | `Deployment` / `StatefulSet` / `Job` |
| Package + parameterize manifests | Helm chart or Kustomize |
| Sync repo → cluster | ArgoCD / Flux |
| External-resource lifecycle, no day-2 complexity | Terraform / Crossplane composition |

## Core principle: reconcile is declarative, not imperative

The wrong shape:

```
if creating:  do A
elif updating: do B
elif deleting: do C
```

The right shape:

```
desired = read(spec)
actual  = observe(world)
diff = compare(desired, actual)
for change in diff: apply(change)   # idempotently
update_status()
```

Reconcile must be safe to run twice in a row with no spec change → same result, zero side effects. If your function isn't idempotent, your operator will fight itself.

## CRD design — the non-negotiables

`crd_validator.py` checks these. The list is short on purpose; each item exists because skipping it produces a specific class of bug.

| Rule | Reason it matters |
|---|---|
| Status subresource enabled | Without it, `Status().Update()` re-triggers the spec watcher → infinite reconcile loop |
| `scope: Namespaced` unless cluster-scoped is justified | Cluster-scoped CRDs leak across tenants and complicate RBAC |
| `served: true` AND `storage: true` on exactly one version | Multiple `storage: true` is invalid; missing it breaks reads |
| OpenAPI v3 schema with typed properties | `x-kubernetes-preserve-unknown-fields: true` at the root defeats validation; users send garbage, controller crashes |
| `conditions` array in schema (for `metav1.Conditions`) | Standard way to communicate state; tooling and humans both read it |
| `additionalPrinterColumns` include Age + Status/Phase | `kubectl get` becomes useful without `-o yaml` |
| Singular + listKind defined | `kubectl get singular` works |

Beyond the validator: **version your CRD from day 1** (`v1alpha1` → `v1beta1` → `v1`) and plan a conversion webhook before you ship `v1`. Migrating storage versions later is painful.

## Reconcile loop — the non-negotiables

`reconcile_lint.py` checks Go reconcile functions for these. Regex-based, so false positives happen — read the source after a flag.

| Rule | Why |
|---|---|
| Return shape is `(ctrl.Result, error)` | controller-runtime contract |
| Errors return `RequeueAfter` or non-nil error | Otherwise transient failures wedge silently |
| Status updates use `Status().Update()`, not `Update()` | Updating spec bumps generation; users see "ghost" changes |
| No `time.Sleep` inside reconcile | Blocks the workqueue → other CRs stall. Use `RequeueAfter` |
| HTTP calls take a `context.Context` and respect it | Otherwise reconciles can't be cancelled on shutdown |
| Finalizer added before any external-resource create | Missing finalizer → user deletes CR → external resource orphans |
| Conditions set via `meta.SetStatusCondition` when CRD declares them | Hand-rolled condition munging drops `LastTransitionTime` |
| Reconcile function under ~80 lines | Extract `reconcileXxx` subroutines per concern |

## OperatorHub capability levels

`operator_capability_audit.py` scores against the OperatorHub 5-level rubric. Use it as a *progression plan*, not a checklist for one PR.

| Level | What it means | Realistic milestone |
|---|---|---|
| L1 Basic Install | CRD defined, controller deploys it | Same PR as the first reconcile |
| L2 Seamless Upgrades | PDB, conversion webhook, version skew strategy | Before first external user |
| L3 Full Lifecycle | Backup, restore, failure recovery | Before public release |
| L4 Deep Insights | `/metrics` endpoint, Prometheus rules, alerts | After L3 in production for ~1 quarter |
| L5 Auto Pilot | Auto-scaling, auto-tuning, anomaly detection | Mature operators only |

Aim for **L3 before public release**. L4/L5 are nice-to-have unless the operator manages something high-stakes.

## Framework choice

| Framework | Language | When to pick |
|---|---|---|
| **kubebuilder** | Go | Default for Go shops. Most opinionated scaffolding, aligned with SIGs |
| **controller-runtime** | Go | When kubebuilder's scaffolding is in the way (large existing codebase) |
| **operator-sdk** | Go / Helm / Ansible | Targeting OpenShift, or mixed-paradigm teams |
| **KOPF** | Python | Python shops; great for prototypes and async-heavy operators |
| **metacontroller** | Any (webhook) | Polyglot team that cannot pick a single language |
| **java-operator-sdk** | Java | JVM-only shops |

Rule of thumb: **Go shop → kubebuilder. Python shop → KOPF.** Build a one-week proof-of-concept before committing the design.

## Workflows

### Bootstrap a new operator (Go + kubebuilder)

1. Pick Group/Version/Kind: `apps.example.com/v1alpha1`, `kind=MyApp`.
2. `kubebuilder init --domain example.com --repo github.com/org/myapp-operator`.
3. `kubebuilder create api --group apps --version v1alpha1 --kind MyApp`.
4. Run `crd_validator.py` on the generated CRD. **Fix every FAIL before writing controller code.**
5. Implement reconcile — start with the simplest correct version (no finalizers, no conditions). Add complexity only when a test fails without it.
6. Run `reconcile_lint.py` on the controller.
7. Run `operator_capability_audit.py --operator-dir .` — confirm L1.
8. Test in a `kind` cluster: `kubectl apply -f config/samples/`.
9. Add status conditions + finalizers; aim for L2 in the same PR.

### Audit an existing operator

1. Run all three scripts.
2. Triage: FAIL → block release; WARN → file an issue.
3. Record current capability level in the README.
4. Plan one level advancement per quarter.

## Proactive triggers

- **CRD without status subresource** → infinite-loop bug waiting to happen. Add `subresources: {status: {}}`.
- **`time.Sleep` inside `Reconcile`** → replace with `return ctrl.Result{RequeueAfter: X}, nil`.
- **Multi-replica operator without leader election** → split-brain. Enable leader election or scale to 1.
- **Reconcile mutates `obj.Spec`** → controller is fighting users. Move to status or admission webhook.
- **No finalizer + external resource (cloud API, DB)** → orphaned resources on CR deletion. Add finalizer.
- **`x-kubernetes-preserve-unknown-fields: true` at schema root** → defeats validation. Type the schema.

## Asset templates

- `assets/crd_template.yaml` — CRD with status subresource, conditions, printer columns
- `assets/reconcile_skeleton.go` — Go reconcile function with idempotency, finalizer, condition patterns
