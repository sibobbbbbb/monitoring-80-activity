"""Bullet chart ternormalisasi: deviasi -1 (batas LSL) sampai +1 (batas USL)."""
import pandas as pd
import plotly.graph_objects as go

from app.timeutil import WIB

AXIS_LIMIT = 1.5  # nilai di luar +-1.5 dipotong di sumbu; angka sebenarnya ada di hover

ZONE_COLORS = {"OK": "#2e9e5b", "WARNING": "#e0a100", "NG": "#d64545"}

# (x0, x1, zona, opacity) latar belakang; sama untuk semua baris karena sudah ternormalisasi
_BANDS = [
    (-AXIS_LIMIT, -1.0, "NG", 0.16),
    (-1.0, -0.8, "WARNING", 0.20),
    (-0.8, 0.8, "OK", 0.12),
    (0.8, 1.0, "WARNING", 0.20),
    (1.0, AXIS_LIMIT, "NG", 0.16),
]


def _label(r) -> str:
    when = pd.Timestamp(r.measured_at).tz_convert(WIB).strftime("%d/%m %H:%M")
    return f"{r.machine_id} · {r.unit_id} · {r.item_ukur} · {when}"


def bullet_chart(df: pd.DataFrame, max_rows: int = 20) -> go.Figure:
    """df: hasil app.queries.priority_rows (sudah urut ratio tertinggi)."""
    d = df.head(max_rows).iloc[::-1]  # ratio tertinggi di atas
    dev = d["deviation"].astype(float)
    ratio = d["ratio"].astype(float)
    labels = [_label(r) for r in d.itertuples()]

    fig = go.Figure()
    for x0, x1, zone, opacity in _BANDS:
        fig.add_vrect(x0=x0, x1=x1, fillcolor=ZONE_COLORS[zone], opacity=opacity,
                      line_width=0, layer="below")
    for x, dash in ((0.0, "solid"), (-0.8, "dot"), (0.8, "dot"), (-1.0, "dash"), (1.0, "dash")):
        fig.add_vline(x=x, line_width=1, line_dash=dash, line_color="rgba(128,128,128,0.7)")

    fig.add_trace(go.Bar(
        x=dev.clip(-AXIS_LIMIT, AXIS_LIMIT),
        y=labels,
        orientation="h",
        width=0.42,
        marker_color=[ZONE_COLORS[z] for z in d["zone"]],
        text=[f"{r:.0%}" for r in ratio],
        textposition="outside",
        cliponaxis=False,
        customdata=list(zip(ratio, d["zone"], d["value"].astype(float))),
        hovertemplate=("%{y}<br>Rasio: %{customdata[0]:.1%} (%{customdata[1]})"
                       "<br>Nilai: %{customdata[2]}<extra></extra>"),
    ))

    for x, name in ((-1.25, "NG"), (-0.9, "WARNING"), (0.0, "OK"), (0.9, "WARNING"), (1.25, "NG")):
        fig.add_annotation(x=x, y=1.0, xref="x", yref="paper", text=name, showarrow=False,
                           yanchor="bottom", font=dict(size=11, color=ZONE_COLORS[name]))

    ticks = [-1.5, -1.0, -0.8, 0.0, 0.8, 1.0, 1.5]
    fig.update_layout(
        height=max(320, 30 * len(d) + 150),
        margin=dict(l=10, r=30, t=40, b=50),
        showlegend=False,
        bargap=0.35,
        xaxis=dict(range=[-AXIS_LIMIT, AXIS_LIMIT], tickvals=ticks,
                   ticktext=[f"{t:+.1f}" if t else "0" for t in ticks],
                   title="Deviasi ternormalisasi (− sisi LSL … + sisi USL); ±1 = batas toleransi",
                   zeroline=False),
        yaxis=dict(automargin=True),
    )
    return fig
