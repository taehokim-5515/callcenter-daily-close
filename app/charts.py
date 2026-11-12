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

    x 축은 넘겨받은 시간 순서(날짜, 월), 쌓는 순서와 범례는 domain 순서를 지킨다. 색 대응은 domain 그대로 둔다.
    """
    tot = df.groupby(x)[val].sum().reset_index()
    x_order = [k for k in x_order if k in set(tot[x])]  # 시간 축은 날짜·월 순서 그대로
    stack = list(domain)  # 시간 축 차트는 범례·쌓는 순서를 정해진 순서로 고정
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


# 크리티컬 유형 반원 그래프: 4색(갈색, 테라코타, 황토, 모래)과 그 연한 단계 두 개
HALF_COLORS = [T.ACCENT, T.INK, T.AMBER, "#dd8a74", T.MUTED, T.SAND]


def half_donut(df: pd.DataFrame, cat: str, val: str, domain: list[str], center: str, sub: str,
               height: int = 300) -> alt.LayerChart:  # 범례(약 50px)가 이 높이 안에 들어간다
    """반원(위쪽 반) 도넛. 조각과 범례는 많은 순, 범례에 '유형 n건'. 아래 가운데에 합계."""
    d = df.set_index(cat)[[val]].reindex(domain, fill_value=0).rename_axis(cat).reset_index()
    d = d.sort_values(val, ascending=False, kind="stable").reset_index(drop=True)
    d["_o"] = d.index
    d["범례"] = d[cat] + "  " + d[val].map(lambda v: f"{int(v):,}건")
    legend_order = list(d["범례"])
    # 크기는 처음 크기(반지름 214px) 그대로. 위에 여백을 두고 아래로 내려 그린다.
    # 폭이 그보다 좁은 화면에서만 폭에 맞춰 줄인다.
    R = "min(214, width / 2 - 8)"
    cx, cy = alt.expr("width / 2"), alt.expr("height - 4")
    arc = alt.Chart(d[d[val] > 0]).mark_arc(innerRadius=alt.expr(f"{R} * 0.58"), outerRadius=alt.expr(R), x=cx, y=cy,
                                            stroke=T.PAPER, strokeWidth=2).encode(
        theta=alt.Theta(f"{val}:Q", stack=True, scale=alt.Scale(range=[-1.5708, 1.5708])),
        color=alt.Color("범례:N", title=None, scale=alt.Scale(domain=legend_order, range=HALF_COLORS[:len(d)]),
                        legend=alt.Legend(orient="bottom", direction="horizontal", columns=3, labelFontSize=13,
                                          labelColor=T.INK, symbolType="circle", symbolSize=140, rowPadding=6,
                                          columnPadding=18, labelLimit=200, offset=16)),
        order=alt.Order("_o:Q"),
        tooltip=[alt.Tooltip(f"{cat}:N", title="유형"), alt.Tooltip(f"{val}:Q", title="건수")],
    )
    txt = pd.DataFrame({"t": [center], "s": [sub]})
    big = alt.Chart(txt).mark_text(x=cx, y=alt.expr("height - 32"), fontSize=34, font=T.FONT,
                                   color=T.INK, fontWeight=300).encode(text="t:N")
    small = alt.Chart(txt).mark_text(x=cx, y=alt.expr("height - 6"), fontSize=13, font=T.FONT,
                                     color=T.MUTED).encode(text="s:N")
    return alt.layer(arc, big, small).properties(height=height, width="container")
