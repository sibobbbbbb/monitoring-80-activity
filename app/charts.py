"""Chart tren per item cek: XChart per mesin (satu garis per mesin) dengan garis batas USL, LSL, dan 80%.

Dipilih nilai XChart (satuan asli), bukan rasio: engineer membaca chart ini seperti control chart FEXQMS,
dan garis batas berada di nilai sebenarnya. Perbandingan lintas item ada pada badge dan urutan grid
(rasio), yang selalu ternormalisasi.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional

import pandas as pd
import plotly.graph_objects as go

from app.timeutil import WIB
from core.rules import WARNING_THRESHOLD

ZONE_COLORS = {"OK": "#2e9e5b", "WARNING": "#e0a100", "NG": "#d64545"}
# Warna mesin sengaja menghindari merah/kuning/hijau, yang dicadangkan untuk batas dan zona.
MACHINE_PALETTE = ["#4c78a8", "#8e6bbf", "#1aa7a7", "#d970b0", "#9c6b4e", "#7b8794", "#2b4a8b", "#b8a1e3"]
LIMIT_COLOR, WARNING_COLOR = ZONE_COLORS["NG"], ZONE_COLORS["WARNING"]


def machine_colors(machines: Iterable[str]) -> dict[str, str]:
    """Warna konsisten per mesin di seluruh grid."""
    return {m: MACHINE_PALETTE[i % len(MACHINE_PALETTE)] for i, m in enumerate(sorted(set(machines)))}


def _num(v) -> Optional[Decimal]:
    return None if v is None or (not isinstance(v, (str, Decimal)) and pd.isna(v)) else Decimal(str(v))


@dataclass(frozen=True)
class Limits:
    usl: Optional[float]
    lsl: Optional[float]
    uwl: Optional[float]   # batas warning atas: ref + 80% * (usl - ref)
    lwl: Optional[float]   # batas warning bawah: ref - 80% * (ref - lsl)

    def values(self) -> list[float]:
        return [v for v in (self.usl, self.lsl, self.uwl, self.lwl) if v is not None]


def limits_of(nominal, usl, lsl) -> Limits:
    """Garis batas dari standar. ref = nominal, atau 0 bila tidak ada (sama seperti core.rules)."""
    ref, hi, lo = _num(nominal) or Decimal(0), _num(usl), _num(lsl)
    uwl = ref + WARNING_THRESHOLD * (hi - ref) if hi is not None and hi > ref else None
    lwl = ref - WARNING_THRESHOLD * (ref - lo) if lo is not None and lo < ref else None
    f = lambda v: None if v is None else float(v)  # noqa: E731
    return Limits(f(hi), f(lo), f(uwl), f(lwl))


def _draw(fig: go.Figure, lim: Limits, color_limit: str, color_warn: str, width: float, label: bool) -> None:
    lines = [(lim.usl, "USL", color_limit, "solid"), (lim.lsl, "LSL", color_limit, "solid"),
             (lim.uwl, "80%", color_warn, "dash"), (lim.lwl, "80%", color_warn, "dash")]
    for y, name, color, dash in lines:
        if y is None:
            continue
        fig.add_hline(y=y, line=dict(color=color, width=width, dash=dash))
        if label:
            fig.add_annotation(xref="paper", x=1, y=y, yref="y", text=f"{name} {y:g}", showarrow=False,
                               xanchor="left", xshift=4, font=dict(size=9, color=color))


def item_chart(rows: pd.DataFrame, colors: dict[str, str], height: int = 270) -> go.Figure:
    """rows: deret satu item cek (semua mesin) dari app.queries.series_rows."""
    d = rows.copy()
    d["x"] = d["measured_at"].dt.tz_convert(WIB).dt.tz_localize(None)   # sumbu waktu dalam WIB
    d["y"] = d["value"].astype(float)
    d["r"] = d["ratio"].astype(float)
    d = d.sort_values("x")
    machines = sorted(d["machine"].unique())

    fig = go.Figure()
    for m in machines:
        g = d[d["machine"] == m]
        fig.add_trace(go.Scatter(
            x=g["x"], y=g["y"], mode="lines+markers", name=m,
            line=dict(color=colors.get(m, MACHINE_PALETTE[0]), width=1.6), marker=dict(size=4),
            customdata=g["r"],
            hovertemplate=f"{m}<br>%{{x|%d %b %H:%M}}<br>XChart: %{{y}}<br>Rasio: %{{customdata:.1%}}<extra></extra>",
        ))
    flagged = d[d["zone"] != "OK"]                     # cincin merah/kuning di titik WARNING/NG
    if not flagged.empty:
        fig.add_trace(go.Scatter(
            x=flagged["x"], y=flagged["y"], mode="markers", showlegend=False, hoverinfo="skip",
            marker=dict(symbol="circle-open", size=10, color="rgba(0,0,0,0)",
                        line=dict(width=2, color=[ZONE_COLORS[z] for z in flagged["zone"]]))))

    # Batas per mesin (dari baris terbaru). Sama untuk semua mesin -> satu set garis berlabel;
    # berbeda -> garis per mesin dengan warna mesin.
    spec = d.groupby("machine").tail(1).set_index("machine")
    per_machine = {m: limits_of(spec.at[m, "nominal"], spec.at[m, "usl"], spec.at[m, "lsl"]) for m in machines}
    distinct = set(per_machine.values())
    all_limits: list[float] = []
    if len(distinct) == 1:
        lim = next(iter(distinct))
        _draw(fig, lim, LIMIT_COLOR, WARNING_COLOR, 1.3, label=True)
        all_limits = lim.values()
    else:
        for m, lim in per_machine.items():
            _draw(fig, lim, colors.get(m, LIMIT_COLOR), colors.get(m, WARNING_COLOR), 0.9, label=False)
            all_limits += lim.values()

    lo, hi = min([d["y"].min(), *all_limits]), max([d["y"].max(), *all_limits])
    pad = (hi - lo) * 0.08 or 1.0
    fig.update_layout(
        height=height, showlegend=len(machines) > 1, hovermode="closest",
        margin=dict(l=8, r=62, t=26 if len(machines) > 1 else 8, b=8),
        legend=dict(orientation="h", x=0, y=1.0, yanchor="bottom", font=dict(size=10)),
        xaxis=dict(tickformat="%d %b", showgrid=False),
        yaxis=dict(range=[lo - pad, hi + pad], automargin=True),
    )
    return fig
