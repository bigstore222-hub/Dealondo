"""
membership.py — 유료/무료 티어 발행 관리

수익 모델의 핵심: **"먼저 아는 것"에 값을 매긴다.**

4년 실측이 보여준 사실 — 좋은 딜일수록 수명이 짧다(품절 반복).
80~90% 할인 딜은 몇 시간이면 사라진다. 그래서 30분~1시간의 선행 알림에
실제 금전적 가치가 생긴다. 30만원짜리를 5만원에 사는 딜을 놓치지 않으려고
월 4,900원을 내는 건 합리적 판단이다.

동작:
    유료 채널 → 즉시 발송
    무료 채널 → FREE_DELAY_MIN 분 뒤 발송 (예약 큐에 적재)

예약 큐는 DB에 저장하므로 프로그램을 껐다 켜도 유지된다.

중요한 설계 원칙:
    **무료 구독자에게서 아무것도 빼앗지 않는다.**
    기존에 받던 딜은 계속 받는다. 단지 유료가 조금 먼저 받을 뿐이다.
    빼앗는 구조로 만들면 4년간 쌓은 신뢰가 무너진다.

환경변수:
    TELEGRAM_CHAT_ID        유료 채널 (기존 값 그대로 사용)
    TELEGRAM_FREE_CHAT_ID   무료 채널 (없으면 티어 기능 비활성)
    RADAR_FREE_DELAY_MIN    무료 지연 시간(분), 기본 60
"""
from __future__ import annotations
import json, os, sqlite3
from dataclasses import asdict
from datetime import datetime, timezone, timedelta

import notify

FREE_DELAY_MIN = int(os.environ.get("RADAR_FREE_DELAY_MIN", "60"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS delayed_queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    payload    TEXT,
    release_at TEXT,
    sent       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_dq_release ON delayed_queue(release_at, sent);
"""


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()


def tiers_enabled() -> bool:
    """무료 채널이 설정돼 있어야 티어 구분이 의미가 있다."""
    return bool(os.environ.get("TELEGRAM_FREE_CHAT_ID"))


# ---------------------------------------------------------------------------
# 발송
# ---------------------------------------------------------------------------
def _send_to(chat_id: str | None, text: str, preview: bool = True) -> bool:
    """notify.send 를 특정 채널로 보내도록 우회."""
    saved = os.environ.get("TELEGRAM_CHAT_ID")
    if chat_id:
        os.environ["TELEGRAM_CHAT_ID"] = chat_id
    try:
        return notify.send(text, disable_preview=not preview)
    finally:
        # 원래 값 복원 (없었으면 지운다)
        if saved is None:
            os.environ.pop("TELEGRAM_CHAT_ID", None)
        else:
            os.environ["TELEGRAM_CHAT_ID"] = saved


def publish(deals: list, con: sqlite3.Connection) -> tuple[int, int]:
    """
    유료 채널에 즉시 발송하고, 무료 채널용으로는 예약 큐에 넣는다.
    (즉시발송수, 예약적재수) 반환.
    """
    if not deals:
        return 0, 0

    ensure_schema(con)
    paid_id = os.environ.get("TELEGRAM_CHAT_ID")

    # 티어 미설정이면 기존처럼 단일 채널로 발송
    if not tiers_enabled():
        return notify.notify_deals(deals), 0

    # 1) 유료 채널 — 즉시
    sent = 0
    for d in deals:
        if _send_to(paid_id, notify.format_deal(d)):
            sent += 1

    # 2) 무료 채널 — 지연 예약
    release = datetime.now(timezone.utc) + timedelta(minutes=FREE_DELAY_MIN)
    rows = [(json.dumps(asdict(d), ensure_ascii=False), release.isoformat())
            for d in deals]
    con.executemany(
        "INSERT INTO delayed_queue(payload, release_at) VALUES (?,?)", rows)
    con.commit()

    print(f"[티어] 유료 {sent}건 즉시 발송 / 무료 {len(rows)}건 "
          f"{FREE_DELAY_MIN}분 뒤 예약")
    return sent, len(rows)


def flush_due(con: sqlite3.Connection) -> int:
    """
    예약 시간이 된 무료 채널 딜을 발송한다.
    스케줄러 루프가 매 사이클 호출한다.
    """
    if not tiers_enabled():
        return 0
    ensure_schema(con)

    free_id = os.environ["TELEGRAM_FREE_CHAT_ID"]
    now = datetime.now(timezone.utc).isoformat()
    rows = con.execute(
        "SELECT id, payload FROM delayed_queue WHERE sent=0 AND release_at<=? "
        "ORDER BY id LIMIT 30", (now,)).fetchall()
    if not rows:
        return 0

    import filter_engine as fe
    sent = 0
    for rid, payload in rows:
        try:
            data = json.loads(payload)
            # asdict 로 저장했으므로 Deal 로 복원
            allowed = {f for f in fe.Deal.__dataclass_fields__}
            d = fe.Deal(**{k: v for k, v in data.items() if k in allowed})
            if _send_to(free_id, notify.format_deal(d)):
                sent += 1
        except Exception as e:
            print(f"[티어] 무료 발송 실패(id={rid}): {type(e).__name__}")
        con.execute("UPDATE delayed_queue SET sent=1 WHERE id=?", (rid,))
    con.commit()

    if sent:
        print(f"[티어] 무료 채널 {sent}건 발송 (지연 {FREE_DELAY_MIN}분 경과분)")
    return sent


def pending_count(con: sqlite3.Connection) -> int:
    ensure_schema(con)
    return con.execute(
        "SELECT COUNT(*) FROM delayed_queue WHERE sent=0").fetchone()[0]


def status(con: sqlite3.Connection) -> str:
    if not tiers_enabled():
        return "[티어] 미설정 (단일 채널 발송 중) — TELEGRAM_FREE_CHAT_ID 설정 시 활성"
    return (f"[티어] 유료 즉시 / 무료 {FREE_DELAY_MIN}분 지연 · "
            f"대기 중 {pending_count(con)}건")
