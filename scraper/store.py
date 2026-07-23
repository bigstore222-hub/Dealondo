"""
store.py — 상태 저장소 (SQLite)

두 가지 역할을 한다:

1) **중복 방지** — 이미 알림을 보낸 딜을 다시 보내지 않는다.
   스케줄러가 15분마다 도는데 같은 딜이 계속 잡히므로 이게 없으면 알림 폭탄이 된다.
   가격이 더 내려가면 '갱신'으로 보고 다시 알린다.

2) **가격 이력 축적** — 매 스캔의 가격을 스냅샷으로 쌓는다.
   스펙 3-B(가격 히스토리 25점)는 외부 서비스가 아니라 우리가 직접 쌓아야 작동한다.
   90일치가 모이면 '역대 최저가/1년 최저가/90일 최저가' 판정이 자동으로 켜진다.
"""
from __future__ import annotations
import sqlite3, os, hashlib
from datetime import datetime, timezone, timedelta

def _default_db_path() -> str:
    """
    DB는 클라우드 동기화 폴더(OneDrive/Dropbox) 밖에 두는 게 안전하다.
    동기화 중인 폴더의 SQLite는 잠금이 제대로 걸리지 않아
    'disk I/O error'가 나거나 파일이 손상될 수 있다(실제 발생 확인).
    RADAR_DB 환경변수로 위치를 직접 지정할 수 있다.
    """
    env = os.environ.get("RADAR_DB")
    if env:
        return env
    base = (os.environ.get("LOCALAPPDATA")          # Windows
            or os.environ.get("XDG_DATA_HOME")      # Linux
            or os.path.expanduser("~/.local/share"))
    return os.path.join(base, "hotdeal_radar", "radar.db")


DB_PATH = _default_db_path()

SCHEMA = """
CREATE TABLE IF NOT EXISTS notified (
    deal_key      TEXT PRIMARY KEY,
    url           TEXT,
    title         TEXT,
    best_price    REAL,
    score         INTEGER,
    urgency       TEXT,
    first_seen    TEXT,
    last_notified TEXT
);
CREATE TABLE IF NOT EXISTS price_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_key   TEXT,
    source     TEXT,
    price      REAL,
    scraped_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ph_key  ON price_history(deal_key);
CREATE INDEX IF NOT EXISTS idx_ph_time ON price_history(scraped_at);

-- 이메일에서 확보한 리테일러별 활성 프로모션 코드.
-- 크롤링으로 잡은 상품에 이 코드를 적용하면
-- '사이트 세일 공지'가 '상품 단위 실구매가'로 바뀐다.
CREATE TABLE IF NOT EXISTS promo_codes (
    retailer   TEXT,
    code       TEXT,
    percent    INTEGER,
    amount     REAL,
    min_order  REAL,
    expires    TEXT,
    kind       TEXT DEFAULT 'unknown',   -- public / welcome / personal / unknown
    seen_at    TEXT,
    PRIMARY KEY (retailer, code)
);
CREATE TABLE IF NOT EXISTS tier_runs (
    tier       TEXT PRIMARY KEY,         -- T1/T2/T3/T4
    last_run   REAL                      -- epoch seconds
);
"""

# 코드 유효기간을 모를 때 며칠까지 살아있다고 볼 것인가.
# 쇼핑 프로모션은 보통 3~7일짜리라 보수적으로 잡는다.
PROMO_TTL_DAYS = 5


def deal_key(deal) -> str:
    """상품 식별자. URL의 쿼리스트링(추적 파라미터)을 제외해 안정적으로 만든다."""
    base = (deal.url or "").split("?")[0].rstrip("/")
    if not base:
        base = f"{deal.source}:{deal.title}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


# 나중에 추가된 컬럼들.
# CREATE TABLE IF NOT EXISTS 는 이미 있는 테이블을 건드리지 않으므로,
# 기존 DB에는 새 컬럼이 안 생겨 INSERT 가 OperationalError 로 실패한다(실제 발생).
# 그래서 접속할 때마다 누락된 컬럼을 채워 넣는다.
_MIGRATIONS = [
    ("promo_codes", "kind", "TEXT DEFAULT 'unknown'"),
]


def _migrate(con: sqlite3.Connection) -> None:
    for table, column, decl in _MIGRATIONS:
        try:
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            if cols and column not in cols:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                print(f"[store] DB 갱신: {table}.{column} 추가")
        except sqlite3.Error as e:
            print(f"[store] 마이그레이션 실패({table}.{column}): {e}")
    con.commit()


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    _migrate(con)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 티어별 마지막 실행 시각 (GitHub Actions처럼 매번 새로 뜨는 환경에서
# "지금 이 티어를 돌 차례인가"를 판단하기 위해 DB에 남긴다. DB는 캐시로 유지된다.)
# ---------------------------------------------------------------------------
def get_tier_last_run(con: sqlite3.Connection) -> dict:
    try:
        rows = con.execute("SELECT tier, last_run FROM tier_runs").fetchall()
        return {t: (r or 0.0) for t, r in rows}
    except sqlite3.Error:
        return {}


def set_tier_last_run(con: sqlite3.Connection, tier: str, ts: float) -> None:
    con.execute(
        "INSERT INTO tier_runs(tier, last_run) VALUES(?,?) "
        "ON CONFLICT(tier) DO UPDATE SET last_run=excluded.last_run",
        (tier, ts))
    con.commit()


# ---------------------------------------------------------------------------
# 가격 이력
# ---------------------------------------------------------------------------
def record_prices(con: sqlite3.Connection, deals: list) -> None:
    now = _now()
    con.executemany(
        "INSERT INTO price_history(deal_key, source, price, scraped_at) VALUES (?,?,?,?)",
        [(deal_key(d), d.source, d.price_current, now)
         for d in deals if d.price_current],
    )
    con.commit()


# 이력을 '신뢰할 수 있다'고 볼 최소 조건.
# 횟수만 보면 안 된다 — 15분 간격으로 3번 찍은 이력은 사실상 한 시점의 값이라
# 모든 상품이 '역대 최저가'로 잡혀 점수가 부풀려진다(테스트 중 실제로 발생).
# 그래서 관측 횟수와 '기간'을 함께 요구한다.
MIN_OBSERVATIONS = 3
MIN_SPAN_DAYS = 3


def enrich_with_history(con: sqlite3.Connection, deals: list) -> list:
    """
    축적된 이력으로 price_alltime_low 를 채운다.
    이 값이 채워지면 filter_engine 이 콜드스타트 보정을 끄고
    정상 채점(B항목 = 가격 히스토리 25점)으로 전환된다.
    """
    for d in deals:
        k = deal_key(d)
        row = con.execute(
            "SELECT MIN(price), COUNT(*), MIN(scraped_at) FROM price_history "
            "WHERE deal_key=?", (k,)
        ).fetchone()
        if not row or row[0] is None or row[1] < MIN_OBSERVATIONS:
            continue
        try:
            span = datetime.now(timezone.utc) - datetime.fromisoformat(row[2])
        except (TypeError, ValueError):
            continue
        if span < timedelta(days=MIN_SPAN_DAYS):
            continue          # 기간이 짧으면 '최저가' 판정이 무의미
        d.price_alltime_low = row[0]
    return deals


def history_depth(con: sqlite3.Connection) -> tuple[int, int]:
    """(관측 건수, 축적 일수) — 콜드스타트 탈출 진행도 확인용."""
    n = con.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    first = con.execute("SELECT MIN(scraped_at) FROM price_history").fetchone()[0]
    if not first:
        return 0, 0
    days = (datetime.now(timezone.utc)
            - datetime.fromisoformat(first)).days
    return n, days


# ---------------------------------------------------------------------------
# 중복 방지
# ---------------------------------------------------------------------------
def filter_new(con: sqlite3.Connection, deals: list,
               repeat_after_days: int = 14,
               price_drop_pct: float = 5.0) -> list:
    """
    알림 대상만 남긴다. 다시 알리는 경우는 둘 뿐:
      - 가격이 직전 알림가보다 price_drop_pct 이상 더 내려갔을 때
      - 마지막 알림 후 repeat_after_days 가 지났을 때
    """
    out = []
    for d in deals:
        k = deal_key(d)
        row = con.execute(
            "SELECT best_price, last_notified FROM notified WHERE deal_key=?", (k,)
        ).fetchone()
        if row is None:
            out.append(d)
            continue

        prev_price, last = row
        if d.price_current and prev_price:
            drop = (1 - d.price_current / prev_price) * 100
            if drop >= price_drop_pct:
                out.append(d)
                continue
        try:
            if datetime.now(timezone.utc) - datetime.fromisoformat(last) > \
               timedelta(days=repeat_after_days):
                out.append(d)
        except (TypeError, ValueError):
            pass
    return out


def mark_notified(con: sqlite3.Connection, deals: list) -> None:
    now = _now()
    for d in deals:
        k = deal_key(d)
        row = con.execute("SELECT best_price FROM notified WHERE deal_key=?", (k,)).fetchone()
        best = d.price_current
        if row and row[0] is not None and best is not None:
            best = min(best, row[0])
        con.execute(
            """INSERT INTO notified(deal_key,url,title,best_price,score,urgency,
                                     first_seen,last_notified)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(deal_key) DO UPDATE SET
                 best_price=excluded.best_price, score=excluded.score,
                 urgency=excluded.urgency, last_notified=excluded.last_notified""",
            (k, d.url, d.title, best, d.score, d.urgency, now, now),
        )
    con.commit()


# ---------------------------------------------------------------------------
# 프로모션 코드 — 이메일에서 확보 → 크롤링 상품에 적용
# ---------------------------------------------------------------------------
def save_promo(con: sqlite3.Connection, retailer: str, pc) -> None:
    """이메일에서 뽑은 코드를 리테일러별로 저장."""
    con.execute(
        """INSERT INTO promo_codes(retailer, code, percent, amount, min_order, expires, kind, seen_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(retailer, code) DO UPDATE SET
             percent=excluded.percent, amount=excluded.amount,
             min_order=excluded.min_order, expires=excluded.expires,
             kind=excluded.kind, seen_at=excluded.seen_at""",
        (retailer, pc.code, pc.percent, pc.amount, pc.min_order, pc.expires,
         getattr(pc, "kind", "unknown"), _now()),
    )
    con.commit()


def active_promo(con: sqlite3.Connection, retailer: str):
    """
    해당 리테일러의 살아있는 코드 중 할인 폭이 가장 큰 것.
    만료일이 지났거나 확보한 지 PROMO_TTL_DAYS 넘은 건 제외한다.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=PROMO_TTL_DAYS)).isoformat()
    # 공유 가능한(public) 코드만 상품에 적용한다.
    # 신규가입·개인 전용 코드를 적용하면 구독자가 못 사는 가격을 발행하게 된다.
    rows = con.execute(
        "SELECT code, percent, amount, min_order, expires FROM promo_codes "
        "WHERE retailer=? AND seen_at>=? AND kind='public'",
        (retailer, cutoff)).fetchall()
    if not rows:
        return None
    best = max(rows, key=lambda r: (r[1] or 0, min(r[2] or 0, 50)))
    code, percent, amount, min_order, expires = best
    return {"code": code, "percent": percent, "amount": amount,
            "min_order": min_order, "expires": expires}


def apply_promos(con: sqlite3.Connection, deals: list) -> int:
    """
    크롤링으로 잡은 상품에 이메일로 확보한 코드를 적용한다.
    이게 '사이트 세일 공지'를 '상품 단위 실구매가'로 바꾸는 지점이다.

    최소 주문액 조건이 있는 코드는 그 조건을 만족하는 상품에만 적용한다.
    """
    applied = 0
    cache: dict = {}
    for d in deals:
        if d.collection_method == "email" or not d.price_current:
            continue          # 이메일 딜 자체는 이미 코드가 반영돼 있다
        if d.source not in cache:
            cache[d.source] = active_promo(con, d.source)
        pc = cache[d.source]
        if not pc:
            continue
        if pc["min_order"] and d.price_current < pc["min_order"]:
            continue          # 최소 주문액 미달
        if pc["percent"]:
            d.price_current = round(d.price_current * (1 - pc["percent"] / 100), 2)
            note = f'{pc["code"]} · {pc["percent"]}% 추가할인'
        elif pc["amount"] and pc["amount"] < d.price_current:
            d.price_current = round(d.price_current - pc["amount"], 2)
            note = f'{pc["code"]} · ${pc["amount"]:g} 추가할인'
        else:
            continue
        if pc["min_order"]:
            note += f' · ${pc["min_order"]:g} 이상'
        d.coupon_code = note
        d.coupon_stackable = True
        applied += 1
    return applied


def promo_summary(con: sqlite3.Connection) -> str:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=PROMO_TTL_DAYS)).isoformat()
    rows = con.execute(
        "SELECT retailer, code, percent, amount, kind FROM promo_codes WHERE seen_at>=? "
        "ORDER BY kind, retailer", (cutoff,)).fetchall()
    if not rows:
        return "[코드] 보유한 활성 프로모션 코드 없음 (이메일 구독 후 수집됨)"
    pub = [r for r in rows if r[4] == "public"]
    parts = []
    for r, c, p, a, k in pub[:6]:
        v = f"{p}%" if p else (f"${a:g}" if a else "")
        parts.append(f"{r.split('.')[0]}:{c}({v})")
    msg = f"[코드] 전체 {len(rows)}개 중 공유가능 {len(pub)}개"
    if parts:
        msg += " — " + ", ".join(parts)
    if len(rows) > len(pub):
        msg += f"  (신규가입·개인전용 {len(rows)-len(pub)}개는 상품 적용 제외)"
    return msg


def stats(con: sqlite3.Connection) -> str:
    n_notified = con.execute("SELECT COUNT(*) FROM notified").fetchone()[0]
    n_hist, days = history_depth(con)
    return (f"알림 이력 {n_notified}건 · 가격 관측 {n_hist}건 / {days}일 축적"
            + ("  (90일 도달 시 가격이력 점수 자동 활성)" if days < 90 else "  (가격이력 점수 활성)"))
