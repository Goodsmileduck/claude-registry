# Authoring high-quality architecture / HLA diagrams

Method and conventions for producing clean infra/Kubernetes High-Level-Architecture
diagrams in draw.io (icons, containers, semantic edge routing). Read this when the task
is an **architecture / infra / k8s topology** diagram, not a generic flowchart.
Use `scripts/gen_arch_drawio.py` as the generator (helpers: `box/icon/note/edge/write_mxfile`).

## Table of contents
1. Workflow
2. Generate with a Python script, not hand-XML
3. Icons & stencils
4. Containers (namespaces / env / tiers)
5. Color scheme
6. Layout patterns (the part that makes it look good)
7. Edge routing recipe
8. Titles, labels & notes
9. Sizing & breathing room
10. Validate → open → iterate

## 1. Workflow
1. **Ground-truth first.** Derive every node/edge from the real source (Terraform, Helm
   charts, ApplicationSets, k8s manifests) — never from memory. Grep the repo; list the
   actual namespaces, workloads, operators, sync-waves, and who-talks-to-whom. A diagram
   that "looks right" but invents resources is worse than none.
2. **Scope with the user** (AskUserQuestion): per-env pages vs one representative; detail
   level (workloads only / + data-flow / + control-plane); show ingress routing or not;
   how much off-cluster/external to include. Lock scope before drawing.
3. Generate → validate XML → open in draw.io (MCP `open_drawio_xml`) → iterate.

## 2. Generate with a Python script, not hand-XML
Hand-written mxXML drifts: coordinates get inconsistent and edges break on edits. Instead
build a `cells` list with the helper functions and `write_mxfile()`. Edges reference cell
**IDs**, so re-layout is a coordinate change, not an edge rewrite. The user often edits the
file live in the app — **re-read before re-modifying**, and prefer regenerating to brittle
string edits.

## 3. Icons & stencils
- **Icon cell is 64×54** (consistent across the diagram). Label sits below by default.
- **Kubernetes:** `shape=mxgraph.kubernetes.icon;prIcon=<x>` — `deploy`, `sts`, `pod`, `svc`,
  `ing` (gateway/route), `crd` (operators), `cronjob`, `secret`, `ns`, `user`, `job`, `pv`, `pvc`.
- **GCP:** `shape=mxgraph.gcp2.<name>`.
- Convey *kind* via `prIcon` (deploy vs sts vs ing) and *role* via fill color (section 5).
- Put the **label above** the icon (`verticalLabelPosition=top;verticalAlign=bottom`) when an
  edge-rail runs just beneath it, so the line doesn't cross the text.

## 4. Containers (namespaces / env / tiers)
Use real parent-child rarely; for HLA, flat absolute-positioned boxes drawn *behind* the
icons read fine and keep edge anchoring simple (edges target icon IDs, not relative coords).
Container = rounded rect, light `fillColor` + saturated `strokeColor`, title top-center.
Examples: env/cluster band, per-namespace box, shared/platform tier.

## 5. Color scheme
- **Icon fills (role):** Deployment `#326CE5`, StatefulSet/datastore `#1A53B0`, gateway/route
  `#EA8600`, operators/control-plane `#0F9D58`, ArgoCD/GitOps `#EF7B4D`, actor/external `#5F6368`.
- **Edge colors (semantic — always add a legend):** routing `#EA8600` (solid), data flow
  `#9AA0A6` (solid), GitOps/operator control `#0F9D58` (**dashed**).
- **Containers:** per-env or per-tier stroke (e.g. dev `#4284F3`, shared `#F9AB00`, platform `#5F6368`).

## 6. Layout patterns (the part that makes it look good)
- **Comb-bus for a hub.** When one node talks to many (e.g. `mantis` → all shared services),
  put those targets in a **single column adjacent to the hub** and run each edge as its own
  vertical in the gap between them, staggered ~16px apart. Reads as a tidy comb, not a
  spaghetti fan. Keep the inter-box gap ≥80px so the verticals fit.
- **Ladder for parallel same-direction edges.** Stack their horizontal segments at distinct
  y, **≥13px apart**. Closer than that reads as one thick line.
- **Rail for long control-plane edges.** Route operator→workload / GitOps edges along a
  dedicated channel (a top rail above the boxes, or a side gap), *not* through other
  containers. Stagger entry points so arrowheads fan in cleanly.
- Group same-direction verticals into wide gap channels; don't let them cross icon bodies.

## 7. Edge routing recipe
draw.io has **no edge collision avoidance** — you place the channels.
- **Always set explicit anchors** `exitX/exitY` + `entryX/entryY` (0..1). Never target a box
  center (the router then enters at top-center, straight over the title). Common anchors:
  right `(1,0.5)`, left `(0,0.5)`, top `(0.5,0)`, bottom `(0.5,1)`.
- **Add waypoints** (`<Array as="points">`) to force the channel — see `edge(..., pts=[...])`.
- `edgeStyle=orthogonalEdgeStyle;rounded=1`, `labelBackgroundColor=#FFFFFF` so labels stay
  legible where lines cross. Keep the final straight segment ≥20px for the arrowhead.
- Every edge cell needs a real `<mxGeometry relative="1" .../>` child (never self-closed).

## 8. Titles, labels & notes
- **Short, centered container titles** (e.g. `clinic-ab (namespace)`). A long left-aligned
  title sticks into the vertical edge-lanes beside it.
- **Push qualifiers into small dashed `note()`s** ("one per clinic — dev 2 / stg 1 / prod 0",
  "Terraform-created", env caveats) instead of cramming the title.
- **Keep notes and the legend OUT of edge channels.** Park them in genuinely clear space:
  top-right corner, a bottom strip below all boxes, or pinned to a container's bottom.
- Always include a **legend** mapping edge colors/dash and icon kinds.

## 9. Sizing & breathing room
Do **not** pack containers to exact icon math — it looks cramped and leaves no room for
in-box notes. Leave ~40px slack at the bottom of each container, and grow the outer
band to contain everything. (Concrete example from a 2-col, 4-row workload namespace: it
wanted height ≈600, not the ~560 the icon rows imply; a single-column 8-row datastore
namespace wanted ≈790.) Pin per-box notes to the container bottom inside that slack.

## 10. Validate → open → iterate
1. `write_mxfile(path, cells)`.
2. Validate: `python3 -c "import xml.dom.minidom as m; m.parse('file.drawio')"`.
3. Open via MCP `open_drawio_xml` (pass the inner `<mxGraphModel>…</mxGraphModel>`).
4. Iterate on the user's visual feedback. Common asks and the fix:
   - *"arrows cover the title"* → anchors not centers + shorten/centre titles (§7, §8).
   - *"lines too close"* → widen gap + ladder spacing ≥13px (§6).
   - *"X covers Y"* → move the note/legend to clear space (§8); move the label above the icon (§3).
