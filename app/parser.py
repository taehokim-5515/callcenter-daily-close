"""SCM 엑셀(월 파일) 파서.

파일 구성(11월 SCM.xlsx 기준):
- 일자 시트: 이름이 숫자(2, 3, ... 31) = 그 달의 일. 2행 대분류(병합), 3행 세부유형, 4행부터 마켓별 건수,
  '합계(유형별)' 행에서 끝. 오른쪽(BA~BD 부근)에 '처리건수' 반품 처리표가 있다.
- '월별반품누계': 1~12월 반품 사유별 누계(일자 시트가 없는 지난 달 값의 출처).
요약 시트(문의유형별 건수, 반품, 브랜드별 클레임)는 일자 시트의 수식 합이라 읽지 않는다.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date

import openpyxl
import pandas as pd

BRANDS = ["더리얼", "밥이보약", "가맛시"]
CLAIM_TYPES = ["섭취거부", "구토/설사", "이물/혼입", "냄새", "색상/경도", "벌레/곰팡이", "실링/포장지"]
RETURN_REASONS = ["고객변심", "상품불만", "포장지불량", "오패킹", "오매핑", "오출고", "택배파업"]
# 일자 시트 표기 → 월별반품누계 표기
REASON_ALIAS = {"포장지파손": "포장지불량", "배송사": "택배파업", "포장지 불량": "포장지불량"}
GROUP_ALIAS = {"취,,반,교": "취소/반품/교환", "취,반,교": "취소/반품/교환"}
TYPE_ALIAS = {"실링불량": "실링/포장지", "섭취/거부": "섭취거부"}


class ScmFormatError(ValueError):
    pass


@dataclass
class Parsed:
    year: int
    month: int
    calls: pd.DataFrame          # call_date, market, category, type, cnt
    returns: pd.DataFrame        # ret_date, kind, reason, cnt
    monthly_returns: pd.DataFrame  # year, month, reason, cnt (시트 값)
    days: list[date] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _txt(v) -> str:
    if v is None:
        return ""
    return re.sub(r"\s+", "", str(v))


def _num(v) -> int:
    if v is None or v == "":
        return 0
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return 0  # '.' 같은 오타는 0 으로


def _type_name(raw) -> str:
    """'배송\\n일정' → '배송일정', '구토\\n설사' → '구토/설사' 처럼 문의유형 시트 표기에 맞춘다."""
    t = _txt(raw)
    t = TYPE_ALIAS.get(t, t)
    pairs = {"성분원료": "성분/원료", "유통기한": "유통기한", "급여보관": "급여/보관", "비교추천": "비교/추천",
             "입고품절": "입고/품절", "구성금액": "구성/금액", "칼로리나트륨": "칼로리/나트륨",
             "가입로그인": "가입/로그인", "시스템주문": "시스템/주문", "도매입점": "도매/입점",
             "건의개선": "건의/개선", "섭취거부": "섭취거부", "구토설사": "구토/설사", "이물혼입": "이물/혼입",
             "색상경도": "색상/경도", "벌레곰팡이": "벌레/곰팡이", "실링포장지": "실링/포장지",
             "배송사지연": "배송사지연", "출고지연": "출고지연"}
    return pairs.get(t, t)


def guess_month(filename: str) -> int | None:
    m = re.search(r"(\d{1,2})\s*월", filename or "")
    return int(m.group(1)) if m and 1 <= int(m.group(1)) <= 12 else None


def _day_sheet(ws, d: date, warns: list[str]):
    # 머리글 행 찾기: 'n행에 오패킹' 이 있는 줄이 세부유형 행
    hdr_row = None
    for r in range(1, 8):
        if any(_txt(ws.cell(r, c).value) == "오패킹" for c in range(2, 6)):
            hdr_row = r
            break
    if hdr_row is None:
        raise ScmFormatError(f"'{ws.title}' 시트에서 세부유형 머리글(오패킹 …)을 찾지 못했습니다.")
    grp_row = hdr_row - 1

    # 유형 열: 세부유형 머리글이 이어지는 동안. '합계(마켓별)' 열 앞에서 멈춘다
    cols, group = [], ""
    for c in range(2, 80):
        g = _txt(ws.cell(grp_row, c).value)
        if g.startswith("합계"):
            break
        if g:
            group = GROUP_ALIAS.get(g, g)
        t = ws.cell(hdr_row, c).value
        if t is None:
            if g:
                continue
            break
        cols.append((c, group, _type_name(t)))
    if not cols:
        raise ScmFormatError(f"'{ws.title}' 시트에 유형 열이 없습니다.")

    calls = []
    for r in range(hdr_row + 1, hdr_row + 60):
        market = ws.cell(r, 1).value
        mk = _txt(market)
        if not mk:
            continue
        if mk.startswith("합계"):
            break
        for c, g, t in cols:
            n = _num(ws.cell(r, c).value)
            if n:
                calls.append({"call_date": d.isoformat(), "market": str(market).strip(),
                              "category": g, "type": t, "cnt": n})

    # 반품 처리표: '처리건수' 머리글 셀을 찾아 그 아래를 읽는다
    rets, pos = [], None
    for r in range(1, 40):
        for c in range(40, 70):
            if _txt(ws.cell(r, c).value) == "처리건수":
                pos = (r, c)
                break
        if pos:
            break
    if pos is None:
        warns.append(f"{d.isoformat()}: 반품 처리표(처리건수)를 찾지 못해 건너뜁니다.")
    else:
        r0, c_cnt = pos
        kind = ""
        for r in range(r0 + 1, r0 + 20):
            k = _txt(ws.cell(r, c_cnt - 2).value)
            reason = _txt(ws.cell(r, c_cnt - 1).value)
            if k.startswith("합계") or (not k and not reason):
                break
            if k:
                kind = k
            reason = "" if reason in ("-", "") else REASON_ALIAS.get(reason, reason)
            rets.append({"ret_date": d.isoformat(), "kind": kind, "reason": reason,
                         "cnt": _num(ws.cell(r, c_cnt).value)})
    return calls, rets


def _monthly_sheet(ws, year: int) -> pd.DataFrame:
    rows = []
    hdr = None
    for r in range(1, 10):
        if _txt(ws.cell(r, 1).value) == "구분":
            hdr = r
            break
    if hdr is None:
        return pd.DataFrame(columns=["year", "month", "reason", "cnt"])
    y = re.search(r"(\d{4})", _txt(ws.cell(hdr - 1, 1).value) or "") if hdr > 1 else None
    year = int(y.group(1)) if y else year
    mcols = {}
    for c in range(2, 20):
        m = re.fullmatch(r"(\d{1,2})월", _txt(ws.cell(hdr, c).value))
        if m:
            mcols[c] = int(m.group(1))
    for r in range(hdr + 1, hdr + 15):
        reason = _txt(ws.cell(r, 1).value)
        if not reason or reason.startswith("합계"):
            break
        reason = REASON_ALIAS.get(reason, reason)
        for c, m in mcols.items():
            v = ws.cell(r, c).value
            if v is None or v == "":
                continue
            rows.append({"year": year, "month": m, "reason": reason, "cnt": _num(v)})
    return pd.DataFrame(rows, columns=["year", "month", "reason", "cnt"])


def parse_scm(data: bytes, year: int, month: int) -> Parsed:
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=False)
    except Exception as e:  # noqa: BLE001
        raise ScmFormatError(f"엑셀을 열지 못했습니다: {e}") from e

    warns: list[str] = []
    calls, rets, days = [], [], []
    for name in wb.sheetnames:
        if not re.fullmatch(r"\d{1,2}", name.strip()):
            continue
        dd = int(name)
        try:
            d = date(year, month, dd)
        except ValueError:
            warns.append(f"'{name}' 시트: {year}-{month} 에 없는 날짜라 건너뜁니다.")
            continue
        c, r = _day_sheet(wb[name], d, warns)
        if not c and not any(x["cnt"] for x in r):
            warns.append(f"{d.isoformat()}: 값이 비어 있어 건너뜁니다.")
            continue
        calls += c
        rets += r
        days.append(d)
    if not days:
        raise ScmFormatError("일자 시트(이름이 2, 3, 4 … 인 시트)가 없습니다. SCM 월 파일이 맞는지 확인하세요.")

    monthly = (_monthly_sheet(wb["월별반품누계"], year) if "월별반품누계" in wb.sheetnames
               else pd.DataFrame(columns=["year", "month", "reason", "cnt"]))
    return Parsed(year, month,
                  pd.DataFrame(calls, columns=["call_date", "market", "category", "type", "cnt"]),
                  pd.DataFrame(rets, columns=["ret_date", "kind", "reason", "cnt"]),
                  monthly, sorted(days), warns)
