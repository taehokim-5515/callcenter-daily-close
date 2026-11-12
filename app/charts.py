"""보고용 차트: 값은 막대에 바로 적고, 핵심만 테라코타로 강조하고, 나머지는 모래색으로 물린다.

모든 차트는 theme=None + 흰 배경(theme.PAPER)으로 페이지 배경과 같게 그린다.
"""
from __future__ import annotations

import altair as alt
import pandas as pd

import theme as T

LABEL = {"font": T.FONT, "fontSize": 13, "color": T.INK}


def _share(v: int, total: int) -> str:
    return f"{v:,}건, {v / total * 100:.0f}%" if total else f"{v:,}건"


def hbar(df: pd.DataFrame, cat: str, val: str, highlight: int = 3, row: int = 30,
         share: bool = True, label_width: int = 120, total: int | None = None) -> alt.Chart:
    """가로 막대 순위. 상위 highlight 개는 강조색, 나머지는 모래색. 막대 끝에 '건수  비중'."""
    d = df[[cat, val]].copy()
    d = d[d[val] > 0].sort_values(val, ascending=False).reset_index(drop=True)
    total = int(total if total is not None else d[val].sum())  # 상위 n개만 그려도 비중은 전체 기준
    d["rank"] = d.index
    d["강조"] = d["rank"] < highlight
    d["lbl"] = [(_share(int(v), total) if share else f"{int(v):,}건") for v in d[val]]
    base = alt.Chart(d).encode(
        y=alt.Y(f"{cat}:N", sort=list(d[cat]), title=None,
                axis=alt.Axis(labelOverlap=False, labelLimit=label_width, labelFontSize=13, labelColor=T.INK,
                              domain=False, ticks=False, labelPadding=8)),
        x=alt.X(f"{val}:Q", title=None, axis=None, scale=alt.Scale(domainMax=float(d[val].max()) * 1.25)),
    )
    bars = base.mark_bar(cornerRadiusEnd=3, height=row * 0.62).encode(
        color=alt.condition("datum.강조", alt.value(T.ACCENT), alt.value(T.SAND)),
        tooltip=[alt.Tooltip(f"{cat}:N", title="항목"), alt.Tooltip(f"{val}:Q", title="건수", format=",")])
    strong = base.mark_text(align="left", dx=6, fontWeight=600, **LABEL).encode(text="lbl:N") \
        .transform_filter("datum.강조")
    plain = base.mark_text(align="left", dx=6, **{**LABEL, "color": T.MUTED}).encode(text="lbl:N") \
        .transform_filter("!datum.강조")
    return alt.layer(bars, strong, plain).properties(height=max(row * len(d), 60), width="container")


def stacked_cols(df: pd.DataFrame, x: str, x_order: list[str], color: str, domain: list[str], val: str = "cnt",
                 avg: float | None = None, partial: str | None = None, height: int = 340) -> alt.LayerChart:
    """세로 누적 막대 + 막대 위 합계 + 평균 점선. partial 은 '진행 중' 표시할 x 값.

    x 축은 넘겨받은 시간 순서(날짜, 월)를 지키고, 쌓는 순서만 큰 항목이 아래로 온다. 색 대응은 domain 그대로 둔다.
    """
    tot = df.groupby(x)[val].sum().reset_index()
    x_order = [k for k in x_order if k in set(tot[x])]  # 시간 축은 날짜·월 순서 그대로
    stack = list(df.groupby(color)[val].sum().sort_values(ascending=False).index)
    tot["lbl"] = tot[val].map(lambda v: f"{int(v):,}")
    if partial is not None:
        tot.loc[tot[x] == partial, "lbl"] = tot.loc[tot[x] == partial, "lbl"] + " 진행 중"
    xenc = alt.X(f"{x}:N", sort=x_order, title=None,
                 axis=alt.Axis(labelAngle=0, labelFontSize=13, labelColor=T.INK, domainColor=T.RULE_2, ticks=False))
    ymax = float(tot[val].max()) * 1.15
    bars = alt.Chart(df).mark_bar(cornerRadiusTopLeft=2, cornerRadiusTopRight=2).encode(
        x=xenc,
        y=alt.Y(f"{val}:Q", title=None, scale=alt.Scale(domainMax=ymax),
                axis=alt.Axis(grid=True, gridColor=T.RULE, domain=False, ticks=False, labelColor=T.MUTED, tickCount=4)),
        color=alt.Color(f"{color}:N", title=None, scale=alt.Scale(domain=stack, range=[T.SERIES[domain.index(k)] for k in stack]),
                        legend=alt.Legend(orient="top", direction="horizontal", labelFontSize=13, symbolType="square")),
        order=alt.Order("_o:Q"),
        tooltip=[alt.Tooltip(f"{x}:N", title="구분"), alt.Tooltip(f"{color}:N", title="항목"),
                 alt.Tooltip(f"{val}:Q", title="건수", format=",")],
    ).transform_calculate(_o=f"indexof({stack!r}, datum['{color}'])")
    labels = alt.Chart(tot).mark_text(dy=-8, fontWeight=600, **LABEL).encode(x=xenc, y=f"{val}:Q", text="lbl:N")
    layers = [bars, labels]
    if avg is not None:
        a = pd.DataFrame({"y": [avg], "t": [f"평균 {avg:,.0f}"]})
        layers.append(alt.Chart(a).mark_rule(strokeDash=[4, 4], color=T.MUTED).encode(y="y:Q"))
        layers.append(alt.Chart(a).mark_text(align="right", baseline="bottom", dy=-4, x="width",
                                             font=T.FONT, fontSize=12, color=T.MUTED).encode(y="y:Q", text="t:N"))
    return alt.layer(*layers).properties(height=height, width="container")


def stacked_hbar(df: pd.DataFrame, cat: str, color: str, domain: list[str], colors: list[str], val: str = "cnt",
                 row: int = 32) -> alt.LayerChart:
    """가로 누적 막대(항목별 브랜드 구성) + 끝에 합계."""
    tot = df.groupby(cat)[val].sum().reset_index().sort_values(val, ascending=False)
    tot = tot[tot[val] > 0]
    order = list(tot[cat])
    d = df[df[cat].isin(order)]
    stack = list(d.groupby(color)[val].sum().sort_values(ascending=False).index)
    yenc = alt.Y(f"{cat}:N", sort=order, title=None,
                 axis=alt.Axis(labelFontSize=13, labelColor=T.INK, domain=False, ticks=False, labelPadding=8))
    bars = alt.Chart(d).mark_bar(height=row * 0.62).encode(
        y=yenc,
        x=alt.X(f"sum({val}):Q", title=None, axis=None, scale=alt.Scale(domainMax=float(tot[val].max()) * 1.2)),
        color=alt.Color(f"{color}:N", title=None, scale=alt.Scale(domain=stack, range=[colors[domain.index(k)] for k in stack]),
                        legend=alt.Legend(orient="top", direction="horizontal", labelFontSize=13, symbolType="square")),
        order=alt.Order("_o:Q"),
        tooltip=[alt.Tooltip(f"{cat}:N", title="유형"), alt.Tooltip(f"{color}:N", title="브랜드"),
                 alt.Tooltip(f"sum({val}):Q", title="건수")],
    ).transform_calculate(_o=f"indexof({stack!r}, datum['{color}'])")
    text = alt.Chart(tot).mark_text(align="left", dx=6, fontWeight=600, **LABEL).encode(
        y=yenc, x=f"{val}:Q", text=alt.Text(f"{val}:Q", format=","))
    return alt.layer(bars, text).properties(height=max(row * len(order), 60), width="container")
