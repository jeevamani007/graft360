import io
from datetime import timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from . import calculations as calc
from . import config

RED, GREEN, GREY, INK = "#B42318", "#1A7F4B", "#6B7280", "#14213D"


def _to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def scatter_png(rows: list[dict]) -> bytes:
    """dd_cfDNA_pct (X) vs cp_mL (Y), dashed lines at X=0.50 and Y=44. Invalid rows are skipped."""
    fig, ax = plt.subplots(figsize=(7, 4.8))
    valid = [r for r in rows if r["is_valid"]]
    for status, color in ((calc.HIGH, RED), (calc.LOW, GREEN)):
        pts = [r for r in valid if r["status"] == status]
        if pts:
            ax.scatter([p["dd_cfdna_pct"] for p in pts], [p["cp_ml"] for p in pts], s=70,
                       color=color, edgecolor="white", linewidth=0.8, label=status, zorder=3)
    for r in valid:
        ax.annotate(r["patient_id"], (r["dd_cfdna_pct"], r["cp_ml"]), textcoords="offset points",
                    xytext=(6, 6), fontsize=8, color=INK)
    ax.axvline(config.DD_REF_PCT, ls="--", color=GREY, lw=1.2, label=f"X = {config.DD_REF_PCT:.2f}%")
    ax.axhline(config.CP_REF, ls="--", color=GREY, lw=1.2, label=f"Y = {config.CP_REF:g} cp/mL")
    ax.set_xlabel("dd-cfDNA (%)")
    ax.set_ylabel("Plasma concentration, cp_mL (cp/mL)")
    ax.set_title("dd-cfDNA vs plasma copies", loc="left", fontsize=12, color=INK)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.grid(alpha=0.2)
    if not valid:
        ax.text(0.5, 0.5, "No valid rows to plot", transform=ax.transAxes, ha="center", color=GREY)
    ax.legend(frameon=False, fontsize=8)
    return _to_png(fig)


def trend_png(points: list[tuple]) -> bytes:
    """points = [(date, cm_score), ...]. Horizontal threshold at 0.0 splits the risk zones."""
    fig, ax = plt.subplots(figsize=(7.5, 3.8))
    if points:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        lo, hi = min(ys + [-0.5]), max(ys + [0.5])
        pad = (hi - lo) * 0.12
        ax.set_ylim(lo - pad, hi + pad)
        ax.axhspan(0, hi + pad, color=RED, alpha=0.07, zorder=0)
        ax.axhspan(lo - pad, 0, color=GREEN, alpha=0.07, zorder=0)
        ax.plot(xs, ys, color=INK, lw=1.8, marker="o", ms=6, zorder=3)
        for x, y in points:
            ax.annotate(f"{y:.2f}", (x, y), textcoords="offset points", xytext=(0, 9),
                        ha="center", fontsize=8, color=INK)
        ax.text(0.01, 0.95, "Positive: high risk", transform=ax.transAxes, color=RED, fontsize=8, va="top")
        ax.text(0.01, 0.04, "Negative: low risk", transform=ax.transAxes, color=GREEN, fontsize=8)
        span = (max(xs) - min(xs)).days
        pad = timedelta(days=15 if span == 0 else max(5, round(span * 0.08)))
        ax.set_xlim(min(xs) - pad, max(xs) + pad)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=7))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b %Y"))
        fig.autofmt_xdate()
    else:
        ax.text(0.5, 0.5, "No valid results in this period", transform=ax.transAxes, ha="center", color=GREY)
    ax.axhline(0.0, ls="--", color="black", lw=1.2)
    ax.set_ylabel("CM-Score")
    ax.set_title("CM-Score trajectory", loc="left", fontsize=12, color=INK)
    ax.grid(alpha=0.2)
    return _to_png(fig)
