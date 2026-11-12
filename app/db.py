"""저장소: Supabase(배포) 또는 로컬 SQLite(시크릿이 없을 때). 두 저장소는 같은 메서드를 갖는다.

날짜는 'YYYY-MM-DD' 문자열로 주고받는다.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "sql" / "schema.sql"

CALL_COLS = ["call_date", "market", "category", "type", "cnt"]
RET_COLS = ["ret_date", "kind", "reason", "cnt"]
MON_COLS = ["year", "month", "reason", "cnt"]
CLAIM_COLS = ["id", "claim_date", "brand", "product", "claim_type", "qty", "market", "direction", "detail",
              "ai_report", "ai_model", "status", "created_at", "updated_at"]
UPLOAD_COLS = ["id", "year", "month", "filename", "days", "call_rows", "uploaded_at"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(df: pd.DataFrame) -> list[dict]:
    return df.astype(object).where(pd.notna(df), None).to_dict("records")


class MissingSchema(RuntimeError):
    pass


# ---------------------------------------------------------------- SQLite
class SqliteStore:
    kind = "로컬 SQLite"

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        sql = SCHEMA.read_text(encoding="utf-8")
        sql = "\n".join(l for l in sql.splitlines() if "row level security" not in l)
        sql = (sql.replace("bigint generated always as identity primary key", "integer primary key autoincrement")
                  .replace("timestamptz", "text").replace("now()", "CURRENT_TIMESTAMP"))
        with self._con() as con:
            con.executescript(sql)

    def _con(self):
        return sqlite3.connect(self.path)

    def _q(self, sql: str, args=()) -> pd.DataFrame:
        with self._con() as con:
            return pd.read_sql_query(sql, con, params=args)

    def _exec(self, sql: str, args=()):
        with self._con() as con:
            cur = con.execute(sql, args)
            return cur.lastrowid

    def _insert(self, table: str, rows: list[dict]):
        if not rows:
            return
        cols = list(rows[0])
        sql = f"insert or replace into {table} ({', '.join(cols)}) values ({', '.join('?' * len(cols))})"
        with self._con() as con:
            con.executemany(sql, [tuple(r[c] for c in cols) for r in rows])

    # ---- 업로드 반영: 파일에 든 날짜는 통째로 갈아끼운다
    def replace_days(self, days: list[str], calls: pd.DataFrame, rets: pd.DataFrame):
        marks = ", ".join("?" * len(days))
        with self._con() as con:
            con.execute(f"delete from cc_calls where call_date in ({marks})", days)
            con.execute(f"delete from cc_returns where ret_date in ({marks})", days)
        self._insert("cc_calls", _clean(calls[CALL_COLS]))
        self._insert("cc_returns", _clean(rets[RET_COLS]))

    def save_monthly(self, df: pd.DataFrame):
        self._insert("cc_monthly_returns", _clean(df[MON_COLS]))

    def log_upload(self, row: dict):
        self._insert("cc_uploads", [{**row, "uploaded_at": _now()}])

    # ---- 읽기
    def calls(self, d1: str, d2: str) -> pd.DataFrame:
        return self._q("select * from cc_calls where call_date between ? and ?", (d1, d2))

    def returns(self, d1: str, d2: str) -> pd.DataFrame:
        return self._q("select * from cc_returns where ret_date between ? and ?", (d1, d2))

    def monthly(self, year: int) -> pd.DataFrame:
        return self._q("select * from cc_monthly_returns where year = ?", (year,))

    def call_dates(self) -> list[str]:
        return self._q("select distinct call_date from cc_calls order by call_date")["call_date"].tolist()

    def uploads(self) -> pd.DataFrame:
        return self._q("select * from cc_uploads order by uploaded_at desc limit 50")

    # ---- 클레임
    def claims(self, d1: str | None = None, d2: str | None = None) -> pd.DataFrame:
        if d1:
            return self._q("select * from cc_claims where claim_date between ? and ? order by claim_date desc, id desc",
                           (d1, d2 or d1))
        return self._q("select * from cc_claims order by claim_date desc, id desc")

    def add_claim(self, row: dict) -> int:
        cols = list(row)
        return self._exec(f"insert into cc_claims ({', '.join(cols)}, created_at, updated_at) "
                          f"values ({', '.join('?' * len(cols))}, ?, ?)",
                          (*row.values(), _now(), _now()))

    def update_claim(self, cid: int, fields: dict):
        sets = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"update cc_claims set {sets}, updated_at = ? where id = ?", (*fields.values(), _now(), cid))

    def delete_claim(self, cid: int):
        self._exec("delete from cc_claims where id = ?", (cid,))


# ---------------------------------------------------------------- Supabase
class SupabaseStore(SqliteStore):
    kind = "Supabase"
    PAGE = 1000

    def __init__(self, url: str, key: str):
        from supabase import create_client
        self.sb = create_client(url, key)
        try:
            self.sb.table("cc_calls").select("call_date").limit(1).execute()
        except Exception as e:  # noqa: BLE001
            if "cc_calls" in str(e) or "PGRST205" in str(e) or "does not exist" in str(e):
                raise MissingSchema(str(e)) from e
            raise

    def _t(self, name):
        return self.sb.table(name)

    def _all(self, q, cols: list[str]) -> pd.DataFrame:
        rows, start = [], 0
        while True:
            res = q.range(start, start + self.PAGE - 1).execute()
            rows += res.data
            if len(res.data) < self.PAGE:
                break
            start += self.PAGE
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=cols)

    def _chunks(self, table: str, rows: list[dict]):
        for i in range(0, len(rows), 500):
            self._t(table).upsert(rows[i:i + 500]).execute()

    def replace_days(self, days, calls, rets):
        self._t("cc_calls").delete().in_("call_date", days).execute()
        self._t("cc_returns").delete().in_("ret_date", days).execute()
        self._chunks("cc_calls", _clean(calls[CALL_COLS]))
        self._chunks("cc_returns", _clean(rets[RET_COLS]))

    def save_monthly(self, df):
        if len(df):
            self._chunks("cc_monthly_returns", _clean(df[MON_COLS]))

    def log_upload(self, row):
        self._t("cc_uploads").insert(row).execute()

    def calls(self, d1, d2):
        return self._all(self._t("cc_calls").select("*").gte("call_date", d1).lte("call_date", d2)
                         .order("call_date").order("market").order("type"), CALL_COLS)

    def returns(self, d1, d2):
        return self._all(self._t("cc_returns").select("*").gte("ret_date", d1).lte("ret_date", d2)
                         .order("ret_date").order("kind").order("reason"), RET_COLS)

    def monthly(self, year):
        return self._all(self._t("cc_monthly_returns").select("*").eq("year", year)
                         .order("month").order("reason"), MON_COLS)

    def call_dates(self):
        df = self._all(self._t("cc_calls").select("call_date").order("call_date"), ["call_date"])
        return sorted(set(df["call_date"])) if len(df) else []

    def uploads(self):
        res = self._t("cc_uploads").select("*").order("uploaded_at", desc=True).limit(50).execute()
        return pd.DataFrame(res.data) if res.data else pd.DataFrame(columns=UPLOAD_COLS)

    def claims(self, d1=None, d2=None):
        q = self._t("cc_claims").select("*")
        if d1:
            q = q.gte("claim_date", d1).lte("claim_date", d2 or d1)
        return self._all(q.order("claim_date", desc=True).order("id", desc=True), CLAIM_COLS)

    def add_claim(self, row):
        res = self._t("cc_claims").insert(row).execute()
        return res.data[0]["id"]

    def update_claim(self, cid, fields):
        self._t("cc_claims").update({**fields, "updated_at": _now()}).eq("id", cid).execute()

    def delete_claim(self, cid):
        self._t("cc_claims").delete().eq("id", cid).execute()


def open_store(secrets) -> SqliteStore:
    try:
        url, key = secrets.get("SUPABASE_URL"), secrets.get("SUPABASE_KEY")
    except Exception:  # noqa: BLE001 — secrets.toml 이 없으면 Streamlit 이 예외를 낸다
        url = key = None
    if url and key:
        return SupabaseStore(url, key)
    return SqliteStore(ROOT / "data" / "callcenter.db")
