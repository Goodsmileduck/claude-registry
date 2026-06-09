#!/usr/bin/env python3
"""Reusable helpers for generating high-quality draw.io architecture / HLA diagrams.

Why a generator instead of hand-writing XML: coordinates stay consistent, edges
reference cell IDs (not pixels), and re-layout is a parameter change rather than a
hand-edit. Copy this file next to a diagram, build a `cells` list with the helpers,
then write the mxfile. See references/architecture-diagrams.md for the full method.

Run `python3 gen_arch_drawio.py` to emit + XML-validate a small demo (self-test).
"""
import html

# ---- icon fills (tier / role) ----
BLUE   = "#326CE5"   # Deployment / stateless app (also k8s blue)
NAVY   = "#1A53B0"   # StatefulSet / datastore
ORANGE = "#EA8600"   # Gateway / Route / ingress
GREEN  = "#0F9D58"   # operators / control plane
CORAL  = "#EF7B4D"   # ArgoCD / GitOps driver
GREY   = "#5F6368"   # actors / external
# ---- edge colors (semantic, keep a legend) ----
E_ROUTE = "#EA8600"  # HTTP routing
E_DATA  = "#9AA0A6"  # app data flow
E_CTRL  = "#0F9D58"  # GitOps / operator control (use dashed=True)


def esc(t):
    return html.escape(t, quote=True)


def box(cells, cid, x, y, w, h, label, fill, stroke, fontsize=14, bold=True, align="center"):
    """Container / namespace / env box. Keep the title SHORT; push detail into note().
    align='center' keeps the title off the vertical edge-lanes beside it."""
    sl = "spacingLeft=10;" if align == "left" else ""
    style = (f"rounded=1;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};"
             f"verticalAlign=top;align={align};{sl}spacingTop=6;fontSize={fontsize};"
             f"fontColor=#202124;{'fontStyle=1;' if bold else ''}arcSize=4;")
    cells.append(f'<mxCell id="{cid}" value="{esc(label)}" style="{style}" vertex="1" parent="1">'
                 f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')


def icon(cells, cid, x, y, label, pricon, fill=BLUE, kind="k8s", lblpos="bottom"):
    """64x54 stencil icon with label. kind='k8s' -> mxgraph.kubernetes.icon;prIcon=<x>
    (deploy, sts, pod, svc, ing, crd, cronjob, secret, ns, user, ...).
    kind='gcp' -> shape=mxgraph.gcp2.<pricon>. lblpos='top' moves the label above the
    icon (use when an edge-rail runs just below the icon)."""
    vlp, va = ("top", "bottom") if lblpos == "top" else ("bottom", "top")
    if kind == "gcp":
        shape = f"shape=mxgraph.gcp2.{pricon}"
    else:
        shape = f"shape=mxgraph.kubernetes.icon;prIcon={pricon}"
    style = (f"sketch=0;html=1;dashed=0;whitespace=wrap;fillColor={fill};strokeColor=none;{shape};"
             f"verticalLabelPosition={vlp};verticalAlign={va};labelPosition=center;align=center;"
             f"fontSize=11;fontColor=#202124;")
    cells.append(f'<mxCell id="{cid}" value="{esc(label)}" style="{style}" vertex="1" parent="1">'
                 f'<mxGeometry x="{x}" y="{y}" width="64" height="54" as="geometry"/></mxCell>')


def note(cells, cid, x, y, w, h, label, stroke=GREY):
    """Small dashed annotation. Use for title qualifiers and boundary labels. Keep
    notes OUT of edge channels (top-right corner / bottom clear strip / box bottom)."""
    style = (f"rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor={stroke};"
             f"dashed=1;align=left;spacingLeft=6;fontSize=10;fontColor=#5F6368;arcSize=8;")
    cells.append(f'<mxCell id="{cid}" value="{esc(label)}" style="{style}" vertex="1" parent="1">'
                 f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')


def edge(cells, src, tgt, sx, sy, ex, ey, label="", color=E_DATA, dashed=False, width=1, pts=None):
    """Edge with EXPLICIT anchors (never target a box center) + optional waypoints.
    sx,sy = exit fraction on source; ex,ey = entry fraction on target (0..1).
    pts = [(x,y),...] absolute waypoints to force a clean channel. White label bg."""
    anc = (f"exitX={sx};exitY={sy};exitDx=0;exitDy=0;entryX={ex};entryY={ey};entryDx=0;entryDy=0;")
    style = (f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;jettySize=10;endArrow=block;endFill=1;"
             f"{anc}strokeColor={color};strokeWidth={width};{'dashed=1;' if dashed else ''}"
             f"fontSize=10;fontColor={color};labelBackgroundColor=#FFFFFF;")
    geo = '<mxGeometry relative="1" as="geometry">'
    if pts:
        geo += '<Array as="points">' + "".join(f'<mxPoint x="{px}" y="{py}"/>' for px, py in pts) + '</Array>'
    geo += '</mxGeometry>'
    cells.append(f'<mxCell id="e_{src}_{tgt}" value="{esc(label)}" style="{style}" edge="1" '
                 f'parent="1" source="{src}" target="{tgt}">{geo}</mxCell>')


def write_mxfile(path, cells, page_w=1680, page_h=1100, name="diagram"):
    body = "\n".join(cells)
    xml = (f'<mxfile host="app.diagrams.net" pages="1">\n  <diagram name="{name}" id="{name}">\n'
           f'    <mxGraphModel dx="1400" dy="900" grid="1" gridSize="10" guides="1" tooltips="1" '
           f'connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="{page_w}" '
           f'pageHeight="{page_h}" math="0" shadow="0">\n      <root>\n'
           f'        <mxCell id="0"/>\n        <mxCell id="1" parent="0"/>\n{body}\n'
           f'      </root>\n    </mxGraphModel>\n  </diagram>\n</mxfile>\n')
    open(path, "w").write(xml)
    return xml


if __name__ == "__main__":
    import xml.dom.minidom as M
    c = []
    box(c, "cluster", 20, 20, 720, 460, "Cluster — representative", "#F5F8FF", BLUE, 15)
    box(c, "nsA", 60, 80, 280, 360, "namespace A", "#E8F0FE", "#4284F3")
    box(c, "nsB", 400, 80, 200, 360, "namespace B", "#FFF6E5", "#F9AB00")
    icon(c, "app", 150, 140, "app\nDeploy", "deploy", BLUE)
    icon(c, "db", 470, 140, "db\nSTS", "sts", NAVY)
    note(c, "n1", 70, 390, 250, 30, "one per tenant — detail in a note, not the title", "#4284F3")
    # comb-bus style hub edge with waypoint channel + explicit anchors
    edge(c, "app", "db", 1, 0.5, 0, 0.5, "data", E_DATA, pts=[(360, 167), (360, 167)])
    out = "/tmp/_drawio_demo.drawio"
    write_mxfile(out, c, 760, 500, "demo")
    M.parse(out)
    print(f"OK: wrote + validated {out} ({len(c)} cells)")
