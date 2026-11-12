"""콜센터 일마감 — 일일 유형별 콜, 월별 반품 누계, 브랜드 클레임 기록과 AI 처리 방향 리포트."""
from __future__ import annotations

import io
import re
from datetime import date, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import charts as C
import theme as T
from ai import AiError, analyze, get_api_key
from db import MissingSchema, open_store
from parser import BRANDS, CLAIM_TYPES, RETURN_REASONS, ScmFormatError, guess_month, parse_scm

HERE = Path(__file__).resolve().parent
WD = "월화수목금토일"
GROUP_ORDER = ["배송", "상품", "단순문의", "취소/반품/교환", *BRANDS]
# 차트 색은 4가지만: 대분류 7개를 4묶음으로
COLOR_GROUP = {"배송": "배송", "상품": "상품", "취소/반품/교환": "취소/반품/교환",
               "단순문의": "단순문의, 클레임", **{b: "단순문의, 클레임" for b in BRANDS}}
COLOR_ORDER = ["배송", "상품", "취소/반품/교환", "단순문의, 클레임"]
REASON_TOP = ["고객변심", "상품불만", "오매핑"]  # 나머지 사유는 기타로 묶어 4색
CRITICAL = ["오패킹", "오매핑", "출고지연", "배송일정", "이물/혼입", "벌레/곰팡이"]  # 일일 마감에서 따로 보는 유형
LEVEL1 = {"배송": "배송", "상품": "상품", "단순문의": "단순문의", "취소/반품/교환": "취소/반품/교환",
          **{b: "브랜드 클레임" for b in BRANDS}}

st.set_page_config(page_title="콜센터 일마감", layout="wide", initial_sidebar_state="collapsed")
try:
    alt.theme.register("uv", enable=True)(T.altair_theme)
except AttributeError:  # altair < 5.5
    alt.themes.register("uv", T.altair_theme)
    alt.themes.enable("uv")


# ---------------------------------------------------------------- 스타일
def _css() -> str:
    """tokens + base + style 를 하나로. 외부 @import 는 다른 규칙보다 앞에 와야 적용된다."""
    imports, parts = [], []
    for f in ("tokens.css", "base.css", "style.css"):
        txt = (HERE / f).read_text(encoding="utf-8")
        txt = re.sub(r'@import url\("\./[^"]+"\);\n?', "", txt)
        imports += re.findall(r"@import url\([^)]+\);", txt)
        parts.append(re.sub(r"@import url\([^)]+\);\n?", "", txt))
    return "\n".join(imports + parts)


st.markdown(f"<style>{_css()}</style>", unsafe_allow_html=True)


def empty(title: str, body: str):
    st.markdown(f'<div class="empty"><h2>{title}</h2><p>{body}</p></div>', unsafe_allow_html=True)


# ---------------------------------------------------------------- 접근 제한
def _gate():
    try:
        pw = st.secrets.get("APP_PASSWORD")
    except Exception:  # noqa: BLE001
        pw = None
    if not pw or st.session_state.get("authed"):
        return
    empty("콜센터 일마감", "사내용입니다. 비밀번호를 입력하세요.")
    with st.form("login"):
        v = st.text_input("비밀번호", type="password")
        if st.form_submit_button("들어가기", type="primary"):
            if v == pw:
                st.session_state.authed = True
                st.rerun()
            st.error("비밀번호가 맞지 않습니다.")
    st.stop()


_gate()


@st.cache_resource(show_spinner="저장소에 연결하는 중입니다.")
def _store():
    return open_store(st.secrets)


try:
    S = _store()
except MissingSchema:
    empty("Supabase 에 테이블이 아직 없습니다",
          "Supabase 대시보드 > SQL Editor 에 아래 스키마를 붙여넣고 Run 한 뒤 새로고침하세요.")
    st.code((HERE.parent / "sql" / "schema.sql").read_text(encoding="utf-8"), language="sql")
    st.stop()
except Exception as e:  # noqa: BLE001
    empty("저장소에 연결하지 못했습니다", "Secrets 의 SUPABASE_URL, SUPABASE_KEY 를 확인하세요.")
    st.exception(e)
    st.stop()


# ---------------------------------------------------------------- 읽기(캐시)
@st.cache_data(ttl=300, show_spinner=False)
def q_calls(d1: str, d2: str) -> pd.DataFrame:
    df = S.calls(d1, d2)
    df["cnt"] = pd.to_numeric(df["cnt"], errors="coerce").fillna(0).astype(int)
    return df


@st.cache_data(ttl=300, show_spinner=False)
def q_returns(d1: str, d2: str) -> pd.DataFrame:
    df = S.returns(d1, d2)
    df["cnt"] = pd.to_numeric(df["cnt"], errors="coerce").fillna(0).astype(int)
    return df


@st.cache_data(ttl=300, show_spinner=False)
def q_monthly(year: int) -> pd.DataFrame:
    return S.monthly(year)


@st.cache_data(ttl=300, show_spinner=False)
def q_dates() -> list[str]:
    return [str(d)[:10] for d in S.call_dates()]


@st.cache_data(ttl=60, show_spinner=False)
def q_claims() -> pd.DataFrame:
    return S.claims()


def refresh():
    st.cache_data.clear()


def month_range(y: int, m: int) -> tuple[str, str]:
    d1 = date(y, m, 1)
    d2 = (d1.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return d1.isoformat(), d2.isoformat()


def dlabel(s: str) -> str:
    d = date.fromisoformat(s)
    return f"{d.month}월 {d.day}일 ({WD[d.weekday()]})"


def monthly_returns(year: int) -> pd.DataFrame:
    """월 x 사유 반품 건수. 일자 기록이 있는 달은 일자 합, 없는 달은 월별반품누계 시트 값."""
    sheet = q_monthly(year)
    daily = q_returns(f"{year}-01-01", f"{year}-12-31")
    daily = daily[(daily["kind"] == "반품") & (daily["reason"] != "")].copy()
    rows = []
    if len(daily):
        daily["month"] = pd.to_datetime(daily["ret_date"]).dt.month
        g = daily.groupby(["month", "reason"])["cnt"].sum().reset_index()
        g["src"] = "일자 기록"
        rows.append(g)
    have = set(rows[0]["month"]) if rows else set()
    if len(sheet):
        s = sheet[~sheet["month"].isin(have)][["month", "reason", "cnt"]].copy()
        s["src"] = "월별반품누계 시트"
        rows.append(s)
    if not rows:
        return pd.DataFrame(columns=["month", "reason", "cnt", "src"])
    out = pd.concat(rows, ignore_index=True)
    out["cnt"] = pd.to_numeric(out["cnt"], errors="coerce").fillna(0).astype(int)
    out["month"] = out["month"].astype(int)
    return out


def stat_row(items: list[tuple[str, str, str]]) -> str:
    cells = "".join(f"<div><dt>{a}</dt><dd>{b}<small>{c}</small></dd></div>" for a, b, c in items)
    return f'<dl class="stat-row">{cells}</dl>'


def head(subject: str, takeaway: str, note: str | None = None):
    """보고용 머리: 결론 문장을 제목으로, 무엇을 그린 차트인지는 아래 작은 줄로."""
    sub = subject + (f", {note}" if note else "")
    st.markdown(f'<h2 class="section-head">{takeaway}</h2><p class="section-note">{sub}</p>',
                unsafe_allow_html=True)


def ig(word: str) -> str:
    """받침에 맞춰 '이/가'를 붙인다."""
    c = word[-1] if word else ""
    has = "가" <= c <= "힣" and (ord(c) - 0xAC00) % 28 != 0
    return word + ("이" if has else "가")


def fmt(n) -> str:
    return f"{int(n):,}"


def signed(n) -> str:
    n = int(n)
    return f"+{n:,}" if n > 0 else f"{n:,}"


def to_xlsx(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name[:31])
    return buf.getvalue()


# ---------------------------------------------------------------- 상단
DATES = q_dates()
st.markdown(
    f'<header class="topbar"><h1>콜센터 일마감</h1>'
    f'<p>저장소 {S.kind}, 기록 {len(DATES)}일'
    + (f", 최근 {dlabel(DATES[-1])}" if DATES else "") + "</p></header>",
    unsafe_allow_html=True)

tab_day, tab_month, tab_ret, tab_claim, tab_up = st.tabs(
    ["일일 마감", "월간 추이", "반품 누계", "브랜드 클레임", "데이터 올리기"])


# ================================================================ 일일 마감
with tab_day:
    if not DATES:
        empty("아직 올라온 마감 자료가 없습니다",
              "데이터 올리기 탭에서 그 달 SCM 엑셀(예: 11월 SCM.xlsx)을 올리면 일자 시트가 모두 반영됩니다.")
    else:
        qd = st.query_params.get("d")
        idx = DATES.index(qd) if qd in DATES else len(DATES) - 1
        c1, _ = st.columns([1, 3])
        day = c1.selectbox("마감일", DATES, index=idx, format_func=dlabel, key="day_pick")
        if day != qd:
            st.query_params["d"] = day
        d = date.fromisoformat(day)
        m1, m2 = month_range(d.year, d.month)
        mon = q_calls(m1, m2)
        cur = mon[mon["call_date"].astype(str).str[:10] == day]
        prev_days = [x for x in DATES if x < day]
        prev = mon[mon["call_date"].astype(str).str[:10] == prev_days[-1]] if prev_days else cur.iloc[0:0]
        mdays = sorted({str(x)[:10] for x in mon["call_date"] if str(x)[:10] <= day})
        mtd = mon[mon["call_date"].astype(str).str[:10] <= day]["cnt"].sum()
        total = cur["cnt"].sum()
        by_grp = cur.groupby("category")["cnt"].sum()
        top = cur.groupby("type")["cnt"].sum().sort_values(ascending=False)

        st.markdown(
            f'<section class="stat-hero"><p class="fig">{fmt(total)}<small>건</small></p>'
            f'<p class="qual">{dlabel(day)} 문의 합계. 가장 많은 유형은 '
            + ", ".join(f"{t} {fmt(n)}건" for t, n in top.head(3).items()) + ".</p></section>",
            unsafe_allow_html=True)
        st.markdown(stat_row([
            ("전 영업일 대비", signed(total - prev["cnt"].sum()) if prev_days else "-",
             f"건, {dlabel(prev_days[-1])}" if prev_days else "첫 기록"),
            (f"{d.month}월 누계", fmt(mtd), f"건, {len(mdays)}일"),
            ("일평균", fmt(round(mtd / max(len(mdays), 1))), "건"),
            ("취소/반품/교환", fmt(by_grp.get("취소/반품/교환", 0)), f"건, 클레임 {fmt(sum(by_grp.get(b, 0) for b in BRANDS))}건"),
        ]), unsafe_allow_html=True)

        # 1) 크리티컬 유형: 반원 그래프 + 전 영업일 대비
        tt = cur.groupby("type")["cnt"].sum().reset_index()
        pt = prev.groupby("type")["cnt"].sum()
        crit = tt[tt["type"].isin(CRITICAL)]
        crit_n = int(crit["cnt"].sum())
        crit_prev = int(pt.reindex(CRITICAL, fill_value=0).sum())
        if crit_n:
            c1st = crit.sort_values("cnt", ascending=False).iloc[0]
            head("크리티컬 유형: " + ", ".join(CRITICAL),
                 f"크리티컬 유형 {fmt(crit_n)}건, 전체의 {crit_n / max(total, 1) * 100:.0f}%이고 "
                 f"최다는 {c1st['type']} {fmt(c1st['cnt'])}건입니다.")
        else:
            head("크리티컬 유형: " + ", ".join(CRITICAL), "크리티컬 유형 문의가 없었습니다.")
        cc1, cc2 = st.columns([3, 2], gap="large")
        with cc1:
            st.altair_chart(C.half_donut(crit, "type", "cnt", CRITICAL, f"{crit_n:,}건",
                                         f"전체 {fmt(total)}건 중 {crit_n / max(total, 1) * 100:.0f}%"),
                            use_container_width=True, theme=None)
            st.markdown(C.half_legend(crit, "type", "cnt", CRITICAL), unsafe_allow_html=True)
        with cc2:
            cur_c = tt.set_index("type")["cnt"].reindex(CRITICAL, fill_value=0)
            prev_c = pt.reindex(CRITICAL, fill_value=0)
            rows = sorted(CRITICAL, key=lambda k: -cur_c[k])
            items = "".join(
                f'<div><dt>{k}</dt><dd>{fmt(cur_c[k])}<small>건</small>'
                f'<span class="{"up" if cur_c[k] > prev_c[k] else "flat"}">'
                f'{signed(cur_c[k] - prev_c[k]) if prev_days else "-"}</span></dd></div>' for k in rows)
            st.markdown(f'<dl class="crit-list"><div class="crit-head"><dt>유형</dt><dd>오늘<span>전 영업일 대비</span></dd></div>'
                        f'{items}<div class="crit-sum"><dt>합계</dt><dd>{fmt(crit_n)}<small>건</small>'
                        f'<span>{signed(crit_n - crit_prev) if prev_days else "-"}</span></dd></div></dl>',
                        unsafe_allow_html=True)

        # 2) 그 외 유형: 수평 막대
        other = tt[~tt["type"].isin(CRITICAL) & (tt["cnt"] > 0)].sort_values("cnt", ascending=False)
        if len(other):
            o3 = other.head(3)
            head(f"그 외 유형 {len(other)}개, {fmt(other['cnt'].sum())}건",
                 f"{', '.join(o3['type'])} 순으로 많고, 세 유형이 전체의 "
                 f"{o3['cnt'].sum() / max(total, 1) * 100:.0f}%입니다.")
            st.altair_chart(C.hbar(other, "type", "cnt", total=int(total), row=28),
                            use_container_width=True, theme=None)

        # 2) 대분류 구성 | 반품 처리
        g = (cur.assign(묶음=cur["category"].map(LEVEL1)).groupby("묶음")["cnt"].sum().reset_index())
        r = q_returns(day, day)
        col_a, col_b = st.columns(2, gap="large")
        with col_a:
            gt = g.sort_values("cnt", ascending=False)
            head("대분류 구성", f"{gt.iloc[0]['묶음']} 문의가 {fmt(gt.iloc[0]['cnt'])}건으로 가장 많습니다.")
            st.altair_chart(C.hbar(gt, "묶음", "cnt", highlight=1, row=40), use_container_width=True, theme=None)
        with col_b:
            if len(r):
                rr = r.assign(항목=r.apply(lambda x: x["kind"] if not x["reason"] else f"반품 {x['reason']}", axis=1))
                rr = rr.groupby("항목")["cnt"].sum().reset_index()
                ret_n = int(r[r["kind"] == "반품"]["cnt"].sum())
                top_r = rr.sort_values("cnt", ascending=False).iloc[0]
                head("반품 처리건수",
                     f"처리 {fmt(rr['cnt'].sum())}건 중 반품 {fmt(ret_n)}건, 최다는 {top_r['항목']} {fmt(top_r['cnt'])}건입니다.")
                st.altair_chart(C.hbar(rr, "항목", "cnt", highlight=1, row=32), use_container_width=True, theme=None)
            else:
                head("반품 처리건수", "이 날짜에는 반품 처리표가 없습니다.")

        # 3) 마켓 순위
        mk = cur.groupby("market")["cnt"].sum().reset_index().sort_values("cnt", ascending=False)
        top_m = mk.head(3)
        head("마켓별 문의",
             f"{', '.join(top_m['market'])} 세 곳이 {top_m['cnt'].sum() / max(total, 1) * 100:.0f}%를 차지합니다.")
        st.altair_chart(C.hbar(mk, "market", "cnt", row=28), use_container_width=True, theme=None)
        with st.expander("마켓 x 대분류 표 보기"):
            pv = cur.pivot_table(index="market", columns="category", values="cnt", aggfunc="sum", fill_value=0)
            pv = pv[pv.sum().sort_values(ascending=False).index]
            pv["합계"] = pv.sum(axis=1)
            pv = pv.sort_values("합계", ascending=False)
            pv.loc["합계"] = pv.sum()
            st.dataframe(pv.rename_axis(index="마켓", columns=None), use_container_width=True)


# ================================================================ 월간 추이
with tab_month:
    if not DATES:
        empty("월간 추이를 그릴 자료가 없습니다", "데이터 올리기 탭에서 SCM 엑셀을 올리세요.")
    else:
        months = sorted({s[:7] for s in DATES}, reverse=True)
        c1, c2, _ = st.columns([1, 1, 2])
        ym = c1.selectbox("월", months, format_func=lambda s: f"{s[:4]}년 {int(s[5:])}월", key="m_pick")
        view = c2.selectbox("묶음", ["대분류", "세부유형"], key="m_view")
        y, m = int(ym[:4]), int(ym[5:])
        mon = q_calls(*month_range(y, m)).copy()
        mon["day"] = mon["call_date"].astype(str).str[:10]
        daily = (mon.assign(묶음=mon["category"].map(COLOR_GROUP))
                 .groupby(["day", "묶음"])["cnt"].sum().reset_index())
        tot = mon.groupby("day")["cnt"].sum()

        st.markdown(stat_row([
            ("월 누계", fmt(tot.sum()), f"건, {len(tot)}일"),
            ("일평균", fmt(round(tot.mean())), "건"),
            ("최다일", fmt(tot.max()), dlabel(tot.idxmax())),
            ("최소일", fmt(tot.min()), dlabel(tot.idxmin())),
        ]), unsafe_allow_html=True)

        daily["일"] = daily["day"].map(lambda s: f"{int(s[8:])}일")
        order = [f"{int(s[8:])}일" for s in sorted(tot.index)]
        above = int((tot > tot.mean()).sum())
        head("일자별 문의 건수, 대분류 누적",
             f"하루 평균 {fmt(round(tot.mean()))}건, 최다는 {dlabel(tot.idxmax())} {fmt(tot.max())}건입니다.",
             f"평균을 넘은 날 {above}일")
        st.altair_chart(C.stacked_cols(daily, "일", order, "묶음", COLOR_ORDER, avg=float(tot.mean())),
                        use_container_width=True, theme=None)

        tm = mon.groupby("type")["cnt"].sum().reset_index().sort_values("cnt", ascending=False)
        t3 = tm.head(3)
        head(f"{m}월 문의 유형 상위 10",
             f"{m}월 누계에서는 {', '.join(t3['type'])} 순으로 많습니다.",
             f"세 유형 합계 {fmt(t3['cnt'].sum())}건, {t3['cnt'].sum() / max(tot.sum(), 1) * 100:.0f}%")
        st.altair_chart(C.hbar(tm.head(10), "type", "cnt", total=int(tot.sum())), use_container_width=True, theme=None)

        key = "category" if view == "대분류" else "type"
        pv = mon.pivot_table(index=key, columns="day", values="cnt", aggfunc="sum", fill_value=0)
        pv.columns = [f"{int(c[8:])}일" for c in pv.columns]
        pv["합계"] = pv.sum(axis=1)
        if key == "category":
            pv = pv.reindex([g for g in GROUP_ORDER if g in pv.index])
        else:
            pv = pv.sort_values("합계", ascending=False)
        pv.loc["합계"] = pv.sum()
        pv = pv.rename_axis(index=view, columns=None)
        with st.expander(f"{view} x 일자 표 보기"):
            st.dataframe(pv, use_container_width=True)

        mk = mon.pivot_table(index="market", columns="category", values="cnt", aggfunc="sum", fill_value=0)
        mk = mk[[g for g in GROUP_ORDER if g in mk.columns]]
        mk["합계"] = mk.sum(axis=1)
        mk = mk.sort_values("합계", ascending=False).rename_axis(index="마켓", columns=None)
        st.download_button("월 집계 엑셀 받기", to_xlsx({f"{view}x일자": pv, "마켓x대분류": mk}),
                           file_name=f"콜센터_{y}{m:02d}_집계.xlsx")


# ================================================================ 반품 누계
with tab_ret:
    years = sorted({int(s[:4]) for s in DATES} | {date.today().year}, reverse=True)
    c1, _ = st.columns([1, 3])
    yr = c1.selectbox("연도", years, key="r_year")
    mr = monthly_returns(yr)
    if mr.empty:
        empty(f"{yr}년 반품 기록이 없습니다",
              "SCM 엑셀을 올리면 월별반품누계 시트(지난 달)와 일자 시트의 반품 처리표(이번 달)가 함께 들어옵니다.")
    else:
        pm = mr.pivot_table(index="reason", columns="month", values="cnt", aggfunc="sum", fill_value=0)
        pm = pm.reindex([r for r in RETURN_REASONS if r in pm.index] + [r for r in pm.index if r not in RETURN_REASONS])
        mtot = pm.sum()
        last_m = int(mtot.index.max())
        prev_m = last_m - 1 if last_m - 1 in mtot.index else None
        st.markdown(
            f'<section class="stat-hero"><p class="fig">{fmt(mtot.sum())}<small>건</small></p>'
            f'<p class="qual">{yr}년 반품 누계 ({int(mtot.index.min())}~{last_m}월). '
            f'가장 많은 사유는 {pm.sum(axis=1).idxmax()} {fmt(pm.sum(axis=1).max())}건.</p></section>',
            unsafe_allow_html=True)
        src_last = mr[mr["month"] == last_m]["src"].iloc[0]
        full_m = mtot.drop(last_m) if src_last == "일자 기록" and len(mtot) > 1 else mtot
        st.markdown(stat_row([
            (f"{last_m}월", fmt(mtot[last_m]), "건, 진행 중" if src_last == "일자 기록" else "건"),
            (f"{prev_m}월" if prev_m else "전월", fmt(mtot[prev_m]) if prev_m else "-", "건"),
            ("월평균", fmt(round(full_m.mean())), f"건, 마감된 {len(full_m)}개월"),
            ("고객변심 비중", f"{pm.loc['고객변심'].sum() / max(mtot.sum(), 1) * 100:.0f}" if "고객변심" in pm.index else "-", "%"),
        ]), unsafe_allow_html=True)

        mr["월"] = mr["month"].map(lambda x: f"{x}월")
        mr["사유"] = mr["reason"].where(mr["reason"].isin(REASON_TOP), "기타")
        mc = mr.groupby(["월", "사유"])["cnt"].sum().reset_index()
        full = full_m  # 진행 중인 달은 평균에서 뺀다
        peak = int(full.idxmax())
        head("월별 반품 건수, 사유 누적",
             f"월평균 {fmt(round(full.mean()))}건, {peak}월이 {fmt(full[peak])}건으로 가장 많았습니다.",
             "상위 3개 사유 외는 기타로 묶음. 일자 기록이 있는 달은 처리표 합, 없는 달은 월별반품누계 시트 값")
        st.altair_chart(C.stacked_cols(mc, "월", [f"{i}월" for i in range(1, 13)], "사유", [*REASON_TOP, "기타"],
                                       avg=float(full.mean()),
                                       partial=f"{last_m}월" if src_last == "일자 기록" else None),
                        use_container_width=True, theme=None)

        rs = pm.sum(axis=1).reset_index()
        rs.columns = ["사유", "cnt"]
        r1 = rs.sort_values("cnt", ascending=False).iloc[0]
        head(f"{yr}년 사유별 누계",
             f"{ig(r1['사유'])} {fmt(r1['cnt'])}건으로 {r1['cnt'] / max(mtot.sum(), 1) * 100:.0f}%를 차지합니다.",
             "오매핑, 오패킹, 오출고 합계 "
             f"{fmt(rs[rs['사유'].isin(['오매핑', '오패킹', '오출고'])]['cnt'].sum())}건")
        st.altair_chart(C.hbar(rs, "사유", "cnt", highlight=1, row=34), use_container_width=True, theme=None)

        tbl = pm.copy()
        tbl.columns = [f"{c}월" for c in tbl.columns]
        tbl["누계"] = tbl.sum(axis=1)
        tbl = tbl.sort_values("누계", ascending=False)
        tbl.loc["합계"] = tbl.sum()
        tbl = tbl.rename_axis(index="사유", columns=None)
        with st.expander("사유 x 월 표 보기"):
            st.dataframe(tbl, use_container_width=True)
        st.download_button("월별 반품 누계 엑셀 받기", to_xlsx({"월별반품누계": tbl}), file_name=f"반품누계_{yr}.xlsx")

        cur_months = sorted({s[:7] for s in DATES if s.startswith(str(yr))})
        if cur_months:
            ym = cur_months[-1]
            rd = q_returns(*month_range(int(ym[:4]), int(ym[5:])))
            if len(rd):
                rd = rd.assign(항목=rd.apply(lambda x: x["kind"] if not x["reason"] else f"반품 {x['reason']}", axis=1),
                               일=rd["ret_date"].astype(str).str[8:10].astype(int).map(lambda v: f"{v}일"))
                pd_ = rd.pivot_table(index="항목", columns="일", values="cnt", aggfunc="sum", fill_value=0, sort=False)
                pd_ = pd_[sorted(pd_.columns, key=lambda s: int(s[:-1]))]
                pd_["합계"] = pd_.sum(axis=1)
                pd_ = pd_.sort_values("합계", ascending=False)
                pd_ = pd_.rename_axis(columns=None)
                st.markdown(f'<h2 class="section-head">{int(ym[5:])}월 일자별 처리건수</h2>', unsafe_allow_html=True)
                st.dataframe(pd_, use_container_width=True)


# ================================================================ 브랜드 클레임
def claim_context(claim: dict) -> str:
    """AI 에 붙일 회사 데이터 요약(텍스트)."""
    d = date.fromisoformat(claim["claim_date"])
    mdays = [x for x in DATES if x[:7] == claim["claim_date"][:7]]
    lines = [f"## 집계 기준: {d.month}월 콜센터 기록은 {len(mdays)}일치"
             + (f"({mdays[0]} ~ {mdays[-1]}, 영업일 기준)" if mdays else "(없음)")
             + ". 이번 달 수치는 월 중간 누계입니다."]
    allc = q_claims()
    if len(allc):
        same = allc[(allc["brand"] == claim["brand"]) &
                    (allc["product"].str.replace(" ", "") == claim["product"].replace(" ", ""))]
        same = same[same["id"] != claim.get("id", -1)].head(20)
        lines.append(f"## 같은 제품의 과거 클레임 기록 ({len(same)}건)")
        for _, r in same.iterrows():
            lines.append(f"- {str(r['claim_date'])[:10]} {r['claim_type']} 수량 {r['qty'] if pd.notna(r['qty']) else '-'}"
                         f", 방향성: {r['direction'] or '-'}, 상태: {r['status']}")
        bt = allc[(allc["brand"] == claim["brand"]) & (allc["id"] != claim.get("id", -1))]
        bt = bt[pd.to_datetime(bt["claim_date"]) >= pd.Timestamp(d - timedelta(days=90))]
        if len(bt):
            lines.append(f"## {claim['brand']} 최근 90일 클레임 기록(유형별)")
            for t, n in bt.groupby("claim_type").size().items():
                lines.append(f"- {t}: {n}건")
    else:
        lines.append("## 과거 클레임 기록: 없음(이 앱에 처음 기록되는 건)")

    for back in range(3):
        y, m = d.year, d.month - back
        while m <= 0:
            y, m = y - 1, m + 12
        c = q_calls(*month_range(y, m))
        c = c[c["category"].isin(BRANDS)]
        if back == 0 or len(c):
            lines.append(f"## {y}년 {m}월 콜센터 브랜드 클레임 문의(브랜드 x 유형)")
            if len(c):
                for (b, t), n in c.groupby(["category", "type"])["cnt"].sum().items():
                    lines.append(f"- {b} {t}: {n}건")
            else:
                lines.append("- 기록 없음")
    mr = monthly_returns(d.year)
    if len(mr):
        lines.append(f"## {d.year}년 월별 반품 사유 건수")
        pm = mr.pivot_table(index="reason", columns="month", values="cnt", aggfunc="sum", fill_value=0)
        for reason, row in pm.iterrows():
            lines.append(f"- {reason}: " + ", ".join(f"{k}월 {v}" for k, v in row.items()))
    return "\n".join(lines)


def run_ai(claim: dict) -> bool:
    key = get_api_key(st.secrets)
    if not key:
        st.error("ANTHROPIC_API_KEY 가 설정되지 않아 AI 분석을 할 수 없습니다. 기록은 저장됐습니다.")
        return False
    with st.spinner("Claude 가 이력과 통계를 읽고 방향을 정리하는 중입니다. 30초에서 1분쯤 걸립니다."):
        try:
            report, model = analyze(claim, claim_context(claim), key)
        except AiError as e:
            st.error(str(e))
            return False
    S.update_claim(int(claim["id"]), {"ai_report": report, "ai_model": model})
    refresh()
    return True


with tab_claim:
    months = sorted({s[:7] for s in DATES}, reverse=True) or [date.today().strftime("%Y-%m")]
    c1, _ = st.columns([1, 3])
    ym = c1.selectbox("월", months, format_func=lambda s: f"{s[:4]}년 {int(s[5:])}월", key="c_month")
    y, m = int(ym[:4]), int(ym[5:])
    cm = q_calls(*month_range(y, m))
    cb = cm[cm["category"].isin(BRANDS)]

    if cb.empty:
        head("브랜드별 클레임 문의", f"{m}월에는 브랜드 클레임 문의가 없습니다.")
    else:
        ctot = int(cb["cnt"].sum())
        bsum = cb.groupby("category")["cnt"].sum().reindex(BRANDS, fill_value=0)
        tsum = cb.groupby("type")["cnt"].sum().sort_values(ascending=False)
        st.markdown(stat_row([
            (f"{m}월 클레임 문의", fmt(ctot), "건"),
            *[(b, fmt(bsum[b]), f"건, {bsum[b] / max(ctot, 1) * 100:.0f}%") for b in bsum.sort_values(ascending=False).index],
        ]), unsafe_allow_html=True)
        head("클레임 유형별 건수, 브랜드 구성",
             f"{tsum.index[0]} {fmt(tsum.iloc[0])}건이 가장 많고, {ig(bsum.idxmax())} 전체의 "
             f"{bsum.max() / max(ctot, 1) * 100:.0f}%입니다.",
             "일자 시트의 브랜드 칸(마켓 합)")
        bt = cb.groupby(["type", "category"])["cnt"].sum().reset_index()
        st.altair_chart(C.stacked_hbar(bt, "type", "category", BRANDS, [T.ACCENT, T.INK, T.AMBER]),
                        use_container_width=True, theme=None)
        bp = cb.pivot_table(index="type", columns="category", values="cnt", aggfunc="sum", fill_value=0)
        bp = bp.reindex(index=CLAIM_TYPES, columns=BRANDS, fill_value=0)
        bp["합계"] = bp.sum(axis=1)
        bp = bp.sort_values("합계", ascending=False)
        bp = bp[bsum.sort_values(ascending=False).index.tolist() + ["합계"]]
        bp.loc["합계"] = bp.sum()
        with st.expander("유형 x 브랜드 표 보기"):
            st.dataframe(bp.rename_axis(index="유형", columns=None), use_container_width=True)

    st.markdown('<h2 class="section-head">클레임 기록과 AI 처리 방향</h2>'
                '<p class="section-note">제품명, 유형, 수량, 생각한 방향성을 적고 분석을 누르면 과거 같은 제품 기록과 '
                '이번 달 문의·반품 통계를 함께 읽어 처리 방향 리포트를 만듭니다. 기록과 리포트는 Supabase 에 쌓입니다.</p>',
                unsafe_allow_html=True)
    with st.form("claim_form", clear_on_submit=False):
        f1, f2, f3, f4 = st.columns([1, 1, 2, 1])
        cdate = f1.date_input("접수일", value=date.fromisoformat(DATES[-1]) if DATES else date.today())
        brand = f2.selectbox("브랜드", BRANDS)
        product = f3.text_input("제품명", placeholder="예: 더리얼 크런치 닭고기 1kg")
        qty = f4.number_input("수량", min_value=0, step=1, value=1)
        g1, g2 = st.columns([1, 1])
        ctype = g1.selectbox("불만 유형", CLAIM_TYPES)
        market = g2.text_input("접수 채널", placeholder="예: 스마트스토어, 자사몰 챗봇")
        detail = st.text_area("상세 내용", placeholder="고객 진술, 제조일자·로트, 사진 유무, 반려동물 상태 등", height=90)
        direction = st.text_area("처리 방향성", placeholder="예: 동일 로트 회수 요청, 제품 재발송, 품질팀 샘플 분석 의뢰", height=90)
        b1, b2, _ = st.columns([1, 1, 3])
        do_ai = b1.form_submit_button("저장하고 AI 분석", type="primary")
        do_save = b2.form_submit_button("기록만 저장")
    if do_ai or do_save:
        if not product.strip():
            st.error("제품명을 적어 주세요.")
        else:
            row = {"claim_date": cdate.isoformat(), "brand": brand, "product": product.strip(), "claim_type": ctype,
                   "qty": int(qty), "market": market.strip() or None, "detail": detail.strip() or None,
                   "direction": direction.strip() or None, "status": "접수"}
            cid = S.add_claim(row)
            refresh()
            st.session_state.claim_sel = int(cid)
            if do_ai and run_ai({**row, "id": int(cid)}):
                st.success("기록하고 리포트를 만들었습니다. 아래 목록에서 바로 열려 있습니다.")
            elif do_save:
                st.success("기록했습니다.")

    allc = q_claims()
    if allc.empty:
        st.markdown('<p class="section-note">아직 기록된 클레임이 없습니다.</p>', unsafe_allow_html=True)
    else:
        allc = allc.copy()
        allc["claim_date"] = allc["claim_date"].astype(str).str[:10]
        lst = allc[allc["claim_date"].str[:7] == ym]
        st.markdown(f'<h2 class="section-head">{m}월 클레임 기록 {len(lst)}건</h2>', unsafe_allow_html=True)
        if lst.empty:
            st.markdown('<p class="section-note">이 달 기록이 없습니다. 다른 달을 고르거나 위에서 새로 기록하세요.</p>',
                        unsafe_allow_html=True)
        else:
            show = lst.assign(리포트=lst["ai_report"].notna().map({True: "있음", False: "없음"}))[
                ["id", "claim_date", "brand", "product", "claim_type", "qty", "status", "리포트"]]
            st.dataframe(show.rename(columns={"id": "번호", "claim_date": "접수일", "brand": "브랜드", "product": "제품명",
                                              "claim_type": "유형", "qty": "수량", "status": "상태"}),
                         hide_index=True, use_container_width=True)
            ids = lst["id"].astype(int).tolist()
            pre = st.session_state.get("claim_sel")
            sel = st.selectbox("열어 볼 기록", ids, index=ids.index(pre) if pre in ids else 0,
                               format_func=lambda i: "{} {} {} {}".format(*lst[lst["id"] == i][["id", "claim_date", "product", "claim_type"]].iloc[0]))
            rec = lst[lst["id"] == sel].iloc[0].to_dict()
            st.markdown(f"**{rec['brand']} {rec['product']}**, {rec['claim_type']}, 수량 {rec['qty'] if pd.notna(rec['qty']) else '-'}"
                        f", {rec['market'] or '채널 미기입'}")
            if rec.get("detail"):
                st.markdown(f"상세: {rec['detail']}")
            st.markdown(f"방향성: {rec['direction'] or '미기입'}")
            a1, a2, a3, _ = st.columns([1, 1, 1, 2], vertical_alignment="bottom")
            stv = a1.selectbox("상태", ["접수", "진행", "완료"], index=["접수", "진행", "완료"].index(rec["status"])
                               if rec["status"] in ("접수", "진행", "완료") else 0, key=f"st_{sel}")
            if stv != rec["status"]:
                S.update_claim(int(sel), {"status": stv})
                refresh()
                st.rerun()
            if a2.button("AI 분석 다시" if rec.get("ai_report") else "AI 분석", key=f"ai_{sel}"):
                if run_ai(rec):
                    st.rerun()
            if a3.button("기록 삭제", key=f"del_{sel}"):
                st.session_state.confirm_del = int(sel)
            if st.session_state.get("confirm_del") == int(sel):
                st.warning("이 기록과 리포트를 지웁니다. 되돌릴 수 없습니다.")
                k1, k2, _ = st.columns([1, 1, 3])
                if k1.button("지우기", type="primary", key=f"delok_{sel}"):
                    S.delete_claim(int(sel))
                    st.session_state.pop("confirm_del", None)
                    refresh()
                    st.rerun()
                if k2.button("취소", key=f"delno_{sel}"):
                    st.session_state.pop("confirm_del", None)
                    st.rerun()
            if rec.get("ai_report"):
                with st.container(key=f"report_{sel}"):
                    st.markdown(rec["ai_report"])
                head = (f"# 클레임 분석 리포트\n\n- 접수일: {rec['claim_date']}\n- 브랜드: {rec['brand']}\n"
                        f"- 제품명: {rec['product']}\n- 유형: {rec['claim_type']}\n- 수량: {rec['qty']}\n"
                        f"- 담당자 방향성: {rec['direction'] or '-'}\n- 모델: {rec.get('ai_model') or '-'}\n\n")
                st.download_button("리포트 받기 (.md)", head + rec["ai_report"],
                                   file_name=f"클레임리포트_{rec['claim_date']}_{rec['id']}.md", key=f"dl_{sel}")
            else:
                st.markdown('<p class="section-note">아직 리포트가 없습니다. AI 분석을 누르세요.</p>',
                            unsafe_allow_html=True)


# ================================================================ 데이터 올리기
with tab_up:
    st.markdown('<h2 class="section-head">SCM 월 파일 올리기</h2>'
                '<p class="section-note">매일 갱신되는 그 달 SCM 엑셀을 통째로 올리면 됩니다. 파일 안의 일자 시트(2, 3, 4 …)는 '
                '날짜별로 덮어쓰고, 월별반품누계 시트 값도 함께 저장합니다. 같은 파일을 여러 번 올려도 중복되지 않습니다.</p>',
                unsafe_allow_html=True)
    up = st.file_uploader("SCM 엑셀", type=["xlsx", "xlsm"])
    if up is not None:
        u1, u2, _ = st.columns([1, 1, 2])
        gm = guess_month(up.name) or date.today().month
        uy = u1.number_input("연도", min_value=2020, max_value=2100, value=date.today().year, step=1)
        um = u2.selectbox("월", list(range(1, 13)), index=gm - 1, format_func=lambda x: f"{x}월")
        try:
            p = parse_scm(up.getvalue(), int(uy), int(um))
        except ScmFormatError as e:
            st.error(str(e))
            p = None
        if p is not None:
            dsum = p.calls.groupby("call_date")["cnt"].sum()
            rsum = p.returns[p.returns["kind"] == "반품"].groupby("ret_date")["cnt"].sum()
            prev = pd.DataFrame({"문의 합계": dsum, "반품 처리": rsum}).fillna(0).astype(int)
            prev.index = [dlabel(i) for i in prev.index]
            have = set(q_dates())
            new = [x.isoformat() for x in p.days if x.isoformat() not in have]
            st.markdown(f"일자 시트 {len(p.days)}개를 읽었습니다. 새 날짜 {len(new)}일, 덮어쓸 날짜 {len(p.days) - len(new)}일. "
                        f"월별반품누계 {p.monthly_returns['month'].nunique() if len(p.monthly_returns) else 0}개월.")
            for w in p.warnings:
                st.warning(w)
            st.dataframe(prev, use_container_width=False)
            if st.button("저장소에 반영", type="primary"):
                days = [x.isoformat() for x in p.days]
                with st.spinner("저장하는 중입니다."):
                    S.replace_days(days, p.calls, p.returns)
                    S.save_monthly(p.monthly_returns)
                    S.log_upload({"year": p.year, "month": p.month, "filename": up.name,
                                  "days": ",".join(x[8:] for x in days), "call_rows": len(p.calls)})
                refresh()
                st.query_params["d"] = days[-1]
                st.success(f"{len(days)}일치를 반영했습니다. 일일 마감 탭에서 {dlabel(days[-1])} 을 볼 수 있습니다.")

    ups = S.uploads()
    if len(ups):
        st.markdown('<h2 class="section-head">올린 기록</h2>', unsafe_allow_html=True)
        ups = ups.copy()
        ups["uploaded_at"] = pd.to_datetime(ups["uploaded_at"], utc=True).dt.tz_convert("Asia/Seoul").dt.strftime("%Y-%m-%d %H:%M")
        st.dataframe(ups[["uploaded_at", "year", "month", "filename", "days", "call_rows"]].rename(
            columns={"uploaded_at": "올린 시각", "year": "연도", "month": "월", "filename": "파일",
                     "days": "일자", "call_rows": "행 수"}), hide_index=True, use_container_width=True)
