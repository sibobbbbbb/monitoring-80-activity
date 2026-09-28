"""Chart tren per item cek: XChart per mesin (satu garis per mesin) dengan garis batas USL, LSL, dan 80%.

Dipilih nilai XChart (satuan asli), bukan rasio: engineer membaca chart ini seperti control chart FEXQMS,
dan garis batas berada di nilai sebenarnya. Perbandingan lintas item ada pada badge dan urutan grid
(rasio), yang selalu ternormalisasi.

Keterbacaan: garis tren tipis dan lembut; titik WARNING/NG diberi warna isi zona (bukan cincin, yang
menumpuk bila banyak titik ditandai); titik terakhir tiap mesin ditonjolkan karena itulah yang menentukan
status; zona WARNING dan NG diberi latar tipis sehingga posisi terhadap batas terbaca sekilas.
"""
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go

from app.timeutil import WIB
from core.rules import WARNING_THRESHOLD

ZONE_COLORS = {"OK": "#2e9e5b", "WARNING": "#e0a100", "NG": "#d64545"}
# Warna mesin sengaja menghindari merah/kuning/hijau, yang dicadangkan untuk batas dan zona.
MACHINE_PALETTE = ["#4c78a8", "#8e6bbf", "#1aa7a7", "#d970b0", "#9c6b4e", "#7b8794", "#2b4a8b", "#b8a1e3"]
LIMIT_COLOR, WARNING_COLOR = ZONE_COLORS["NG"], ZONE_COLORS["WARNING"]

MARKER_SIZE = 6      # titik OK: warna mesin
FLAG_SIZE = 9        # titik WARNING/NG: warna zona
LAST_SIZE = 12       # titik terakhir tiap mesin: outline putih
LINE_WIDTH = 1.1
LINE_ALPHA = 0.55    # garis dibuat lembut agar titik yang menonjol
BAND_ALPHA = {"WARNING": 0.10, "NG": 0.07}


def machine_colors(machines: Iterable[str]) -> dict[str, str]:
    """Warna konsisten per mesin di seluruh grid."""
    return {m: MACHINE_PALETTE[i % len(MACHINE_PALETTE)] for i, m in enumerate(sorted(set(machines)))}


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def _num(v) -> Decimal | None:
    return None if v is None or (not isinstance(v, (str, Decimal)) and pd.isna(v)) else Decimal(str(v))


@dataclass(frozen=True)
class Limits:
    usl: float | None
    lsl: float | None
    uwl: float | None   # batas warning atas: ref + 80% * (usl - ref)
    lwl: float | None   # batas warning bawah: ref - 80% * (ref - lsl)

    def values(self) -> list[float]:
        return [v for v in (self.usl, self.lsl, self.uwl, self.lwl) if v is not None]


def _float(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


def limits_of(nominal, usl, lsl) -> Limits:
    """Garis batas dari standar. ref = nominal, atau 0 bila tidak ada (sama seperti core.rules)."""
    ref, hi, lo = _num(nominal) or Decimal(0), _num(usl), _num(lsl)
    uwl = ref + WARNING_THRESHOLD * (hi - ref) if hi is not None and hi > ref else None
    lwl = ref - WARNING_THRESHOLD * (ref - lo) if lo is not None and lo < ref else None
    return Limits(_float(hi), _float(lo), _float(uwl), _float(lwl))


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


def _bands(fig: go.Figure, lim: Limits, bottom: float, top: float) -> None:
    """Latar tipis: WARNING (antara batas 80% dan batas), NG (di luar batas). Sisi tanpa batas 80% dilewati."""
    def band(y0, y1, zone):
        fig.add_hrect(y0=y0, y1=y1, fillcolor=ZONE_COLORS[zone], opacity=BAND_ALPHA[zone], line_width=0, layer="below")

    if lim.usl is not None and lim.uwl is not None:
        band(lim.uwl, lim.usl, "WARNING")
        band(lim.usl, top, "NG")
    if lim.lsl is not None and lim.lwl is not None:
        band(lim.lsl, lim.lwl, "WARNING")
        band(bottom, lim.lsl, "NG")


def item_chart(rows: pd.DataFrame, colors: dict[str, str], height: int = 300) -> go.Figure:
    """rows: deret satu item cek (semua mesin) dari app.queries.series_rows."""
    d = rows.copy()
    d["x"] = d["measured_at"].dt.tz_convert(WIB).dt.tz_localize(None)
    d["y"] = d["value"].astype(float)
    d["r"] = d["ratio"].astype(float)
    d = d.sort_values("x")
    machines = sorted(d["machine"].unique())
    multi = len(machines) > 1

    fig = go.Figure()
    for m in machines:
        g = d[d["machine"] == m]
        base = colors.get(m, MACHINE_PALETTE[0])
        is_last = [False] * (len(g) - 1) + [True]
        is_flagged = [z != "OK" for z in g["zone"]]
        fig.add_trace(go.Scatter(
            x=g["x"], y=g["y"], mode="lines+markers", name=m, showlegend=False, legendgroup=m,
            line=dict(color=_rgba(base, LINE_ALPHA), width=LINE_WIDTH),
            marker=dict(
                color=[ZONE_COLORS[z] if z != "OK" else base for z in g["zone"]],
                size=[LAST_SIZE if last else FLAG_SIZE if flagged else MARKER_SIZE
                      for flagged, last in zip(is_flagged, is_last, strict=True)],
                line=dict(color=["rgba(255,255,255,0.95)" if last else "rgba(0,0,0,0.35)" for last in is_last],
                          width=[2 if last else 0.6 for last in is_last]),
            ),
            customdata=g["r"],
            hovertemplate=f"{m}<br>%{{x|%d %b %H:%M}}<br>XChart: %{{y}}<br>Rasio: %{{customdata:.1%}}<extra></extra>",
        ))
        if multi:
            # Titik berwarna zona, jadi legenda butuh trace sendiri; legendgroup yang sama membuat kliknya ikut
            # menyembunyikan garis data.
            fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines", name=m, showlegend=True, legendgroup=m,
                                     line=dict(color=base, width=3)))

    # Batas per mesin (dari baris terbaru). Sama untuk semua mesin -> satu set garis berlabel + latar zona;
    # berbeda -> garis per mesin dengan warna mesin (tanpa latar, karena zonanya berbeda-beda).
    spec = d.groupby("machine").tail(1).set_index("machine")
    per_machine = {m: limits_of(spec.at[m, "nominal"], spec.at[m, "usl"], spec.at[m, "lsl"]) for m in machines}
    distinct = set(per_machine.values())
    all_limits = [v for lim in distinct for v in lim.values()]

    lo, hi = min([d["y"].min(), *all_limits]), max([d["y"].max(), *all_limits])
    pad = (hi - lo) * 0.08 or 1.0
    bottom, top = lo - pad, hi + pad

    if len(distinct) == 1:
        lim = next(iter(distinct))
        _bands(fig, lim, bottom, top)
        _draw(fig, lim, LIMIT_COLOR, WARNING_COLOR, 1.3, label=True)
    else:
        for m, lim in per_machine.items():
            _draw(fig, lim, colors.get(m, LIMIT_COLOR), colors.get(m, WARNING_COLOR), 0.9, label=False)

    fig.update_layout(
        height=height, showlegend=multi, hovermode="closest",
        margin=dict(l=8, r=62, t=26 if multi else 8, b=8),
        legend=dict(orientation="h", x=0, y=1.0, yanchor="bottom", font=dict(size=10)),
        xaxis=dict(tickformat="%d %b", showgrid=False, nticks=6),
        yaxis=dict(range=[bottom, top], automargin=True, gridcolor="rgba(128,128,128,0.18)", zeroline=False),
    )
    return fig
