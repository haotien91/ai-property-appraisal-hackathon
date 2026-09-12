from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties


def _font():
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ]
    for p in candidates:
        if Path(p).exists():
            return FontProperties(fname=p)
    return None


def _plot_line(ax, geom, **kwargs):
    if geom.geom_type == "LineString":
        x, y = geom.xy
        ax.plot(x, y, **kwargs)
    else:
        for g in geom.geoms:
            _plot_line(ax, g, **kwargs)


def _plot_poly_boundary(ax, geom, **kwargs):
    if geom.geom_type == "Polygon":
        x, y = geom.exterior.xy
        ax.plot(x, y, **kwargs)
    elif geom.geom_type == "MultiPolygon":
        for g in geom.geoms:
            _plot_poly_boundary(ax, g, **kwargs)


def render_preview(path, roi, roads, zoning, final_geom, anchor, case) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fp = _font()

    fig, ax = plt.subplots(figsize=(8, 8), dpi=160)

    # Zoning context.
    if zoning is not None and len(zoning):
        zoning.plot(ax=ax, facecolor="#f1e6d7", edgecolor="#d4b99a", alpha=0.55, linewidth=0.8)

    # Roads and labels.
    for name, geom in roads.items():
        _plot_line(ax, geom, color="#4a4a4a", linewidth=1.4, alpha=0.9)
        pt = geom.interpolate(0.5, normalized=True)
        ax.text(pt.x, pt.y, name, fontsize=8, fontproperties=fp,
                bbox={"facecolor":"white","alpha":0.75,"edgecolor":"none","pad":1.5})

    # Final brown boundary.
    _plot_poly_boundary(ax, final_geom, color="#8b5a2b", linewidth=3.0)
    ax.scatter([anchor.x], [anchor.y], s=28, marker="o", color="#b22222", zorder=5)
    ax.text(anchor.x, anchor.y, f"  {case.section.code}/{case.parcel}", fontsize=9,
            fontproperties=fp, va="bottom")

    minx, miny, maxx, maxy = roi.bounds
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(
        f"{case.section.name} {case.section.code} / {case.parcel} — {case.constraints.zone}",
        fontproperties=fp,
    )
    ax.set_xlabel("TWD97 X (m)")
    ax.set_ylabel("TWD97 Y (m)")
    ax.grid(True, linewidth=0.3, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def render_boundary_map(path, roi, roads, zoning, final_geom, anchor, case) -> None:
    """Render a presentation-oriented boundary map with the zoning constraint visible.

    Unlike the generic debug preview, this map makes the requested zoning class an
    explicit visual constraint and highlights only the final clipped boundary.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fp = _font()

    fig, ax = plt.subplots(figsize=(8, 8), dpi=180)

    # Requested zoning class is the admissible area.
    if zoning is not None and len(zoning):
        zoning.plot(
            ax=ax,
            facecolor="#fff4a8",
            edgecolor="#e4b800",
            alpha=0.65,
            linewidth=1.6,
        )

    for name, geom in roads.items():
        _plot_line(ax, geom, color="#5f5f5f", linewidth=1.4, alpha=0.95)
        pt = geom.interpolate(0.5, normalized=True)
        ax.text(
            pt.x,
            pt.y,
            name,
            fontsize=8,
            fontproperties=fp,
            bbox={"facecolor": "white", "alpha": 0.82, "edgecolor": "#bdbdbd", "pad": 1.4},
        )

    _plot_poly_boundary(ax, final_geom, color="#6f3f2b", linewidth=4.2)
    ax.scatter([anchor.x], [anchor.y], s=35, marker="o", color="#d52222", zorder=6)
    ax.text(
        anchor.x,
        anchor.y,
        f"  {case.section.code}/{case.parcel}",
        fontsize=9,
        fontproperties=fp,
        va="bottom",
    )

    minx, miny, maxx, maxy = roi.bounds
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(
        f"圖資邊界圖 — {case.section.name} {case.section.code}/{case.parcel}\n"
        f"使用分區限制：{case.constraints.zone}",
        fontproperties=fp,
    )
    ax.text(
        0.01,
        0.01,
        f"咖啡色框 = 道路條件 ∩ {case.constraints.zone}",
        transform=ax.transAxes,
        fontsize=8,
        fontproperties=fp,
        bbox={"facecolor": "white", "alpha": 0.88, "edgecolor": "#aaaaaa", "pad": 3},
    )
    ax.set_xlabel("TWD97 X (m)")
    ax.set_ylabel("TWD97 Y (m)")
    ax.grid(True, linewidth=0.25, alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
