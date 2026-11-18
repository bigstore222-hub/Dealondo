"""
threads_metrics.py — 스레드 성과 기록 (글마다 24시간 성과 누적 · 팔로워 매일 · 월요일 주간 리포트)

왜: threads_status.json 'insights' 는 최근 글 20개를 그때그때 덮어써서, 오래된 글의 성과가 사라지고
    글마다 '올린 지 몇 시간 뒤' 숫자인지 제각각이었다(11/18 분석 때 비교가 어려웠던 이유).

기록 파일: threads/metrics.json
  posts[링크] = {at, first_line, media, kind(딜/정보/다시 올리기/데이터/기타), sub(소개 방식·글 번호),
                 v24(올린 지 22시간 이후 첫 측정값), last(마지막 측정값)}
  followers   = [{d: 날짜, n: 팔로워 수}]  — 하루 하나
  reports     = {주차: 보낸 시각}

실행 위치: 텔레그램 접수 워크플로(tg_inbox.py --serve)가 3시간마다 collect(), 월요일 오전에 weekly_report().
비용 0: 스레드 API(내 글 목록·글별 인사이트·팔로워 수)만 쓴다.
"""
from __future__ import annotations
import datetime as dt, json, os, re, statistics, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "threads" / "metrics.json"
INBOX = ROOT / "threads" / "inbox.json"
STATUS = ROOT / "threads_status.json"
SCHEDULE = ROOT / "data" / "threads_schedule.json"
KST = dt.timezone(dt.timedelta(hours=9))
EVERY_H = float(os.environ.get("METRICS_EVERY_H", "3"))
ON = (os.environ.get("THREADS_METRICS") or "1").strip() not in ("0", "off", "false")
METRICS = "views,likes,replies,reposts,quotes,shares"
TYPE_KR = {"library": "정보", "repost": "다시 올리기", "data": "데이터"}


def now_kst() -> dt.datetime:
    return dt.datetime.now(KST)


def first_line(s: str) -> str:
    for ln in (s or "").splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def load() -> dict:
    try:
        m = json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        m = {}
    m.setdefault("posts", {})
    m.setdefault("followers", [])
    m.setdefault("reports", {})
    return m


def save(m: dict) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    # 오래된 글은 정리(최근 1,500개)
    if len(m["posts"]) > 1500:
        keep = sorted(m["posts"].items(), key=lambda kv: kv[1].get("at", ""), reverse=True)[:1500]
        m["posts"] = dict(keep)
    m["followers"] = m["followers"][-400:]
    FILE.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")


def _ts(s: str) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)).astimezone(KST)
    except Exception:
        return None


# ── 글 종류 구분 ────────────────────────────────────────────────────────────
def _classifier():
    info, deals, sched = {}, {}, set()
    try:
        for x in json.loads(INBOX.read_text(encoding="utf-8")).get("info", {}).get("log", []):
            if x.get("link"):
                info[x["link"]] = x
    except Exception:
        pass
    try:
        for x in json.loads(STATUS.read_text(encoding="utf-8")).get("deals", []):
            if x.get("link") and x.get("result") == "게시 완료":
                deals.setdefault(x["link"], x)
    except Exception:
        pass
    try:
        sched = {first_line(it.get("text", "")) for it in json.loads(SCHEDULE.read_text(encoding="utf-8"))}
    except Exception:
        pass

    def kind(link: str, fl: str) -> tuple[str, str]:
        if link in info:
            x = info[link]
            return TYPE_KR.get(x.get("type"), "정보"), x.get("id", "")
        if link in deals:
            a = deals[link].get("angle") or ""
            return ("데이터" if a == "data3" else "딜"), a
        if fl in sched:
            return "정보", "예약"
        return "기타", ""
    return kind


# ── 수집 ──────────────────────────────────────────────────────────────────
def due(m: dict | None = None, now: dt.datetime | None = None) -> bool:
    if not ON:
        return False
    m = m or load()
    t = _ts(m.get("at", ""))
    return not t or ((now or now_kst()) - t).total_seconds() >= EVERY_H * 3600


def _insights(T, pid: str, token: str) -> dict:
    res = T._req("GET", f"{T.VER}/{pid}/insights", {"metric": METRICS, "access_token": token})
    out = {}
    for d in res.get("data", []):
        v = d.get("values") or [{}]
        out[d.get("name")] = (d.get("total_value") or {}).get("value", v[0].get("value", 0))
    return out


def followers(T, uid: str, token: str) -> int | None:
    try:
        res = T._req("GET", f"{T.VER}/{uid}/threads_insights", {"metric": "followers_count", "access_token": token})
        for d in res.get("data", []):
            if d.get("name") == "followers_count":
                return int((d.get("total_value") or {}).get("value", 0))
    except Exception as e:
        print("[성과] 팔로워 수 조회 실패:", str(e)[:150])
    return None


def collect(uid: str | None = None, token: str | None = None) -> str:
    import threads_api as T
    token = token or re.sub(r"\s", "", os.environ.get("THREADS_ACCESS_TOKEN") or "")
    uid = uid or re.sub(r"\s", "", os.environ.get("THREADS_USER_ID") or "")
    m = load()
    now = now_kst()
    m["at"] = now.isoformat(timespec="seconds")
    if not (token and uid):
        m["error"] = "토큰 없음"
        save(m)
        return "토큰 없음"
    kind = _classifier()
    err, n = "", 0
    try:
        posts = T.my_posts(uid, token, limit=50)
    except T.ThreadsError as e:
        posts, err = [], str(e)[:200]
    for p in posts:
        link, at = p.get("permalink") or "", _ts(p.get("timestamp", ""))
        if not link or not at:
            continue
        age = (now - at).total_seconds() / 3600
        rec = m["posts"].setdefault(link, {})
        fl = first_line(p.get("text", ""))
        k, sub = kind(link, fl)
        rec.update({"at": at.isoformat(timespec="seconds"), "first_line": fl[:80], "media": p.get("media_type"),
                    "kind": rec.get("kind") if rec.get("kind") and k == "기타" else k,
                    "sub": rec.get("sub") or sub})
        if age > 24 * 8 and rec.get("last"):
            continue                       # 일주일 넘은 글은 더 재지 않는다(API 아끼기)
        try:
            ins = _insights(T, p["id"], token)
        except T.ThreadsError as e:
            err = str(e)[:200]
            break
        snap = {"age_h": round(age, 1), **ins}
        rec["last"] = snap
        if not rec.get("v24") and age >= 22:
            rec["v24"] = snap
        n += 1
    f = followers(T, uid, token)
    if f is not None:
        day = now.strftime("%Y-%m-%d")
        if m["followers"] and m["followers"][-1].get("d") == day:
            m["followers"][-1]["n"] = f
        else:
            m["followers"].append({"d": day, "n": f})
    m["error"] = err
    save(m)
    msg = f"[성과] 글 {n}개 측정 · 팔로워 {f if f is not None else '?'}" + (f" · 오류 {err}" if err else "")
    print(msg)
    return msg


def push() -> None:
    """GitHub Actions 안에서 metrics.json 을 커밋·푸시(접수 워크플로의 저장 단계가 이 파일을 모르므로)."""
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    rel = FILE.relative_to(ROOT).as_posix()
    subprocess.run(["bash", str(ROOT / ".github/scripts/push_files.sh"),
                    f"스레드 성과 기록 {now_kst():%m-%d %H:%M}", rel], cwd=ROOT, check=False)


# ── 다른 모듈용 ────────────────────────────────────────────────────────────
def views_of(rec: dict) -> int:
    """비교 기준 조회수: 24시간 값이 있으면 그것, 없으면 마지막 측정값."""
    s = rec.get("v24") or rec.get("last") or {}
    return int(s.get("views") or 0)


def by_first_line() -> dict[str, dict]:
    """첫 줄 → {'views': 가장 잘 나온 조회수, 'last': 마지막으로 올린 시각} — 다시 올릴 글 고르기용"""
    out: dict[str, dict] = {}
    for rec in load()["posts"].values():
        fl = rec.get("first_line") or ""
        if not fl:
            continue
        v = max(views_of(rec), int((rec.get("last") or {}).get("views") or 0))
        at = _ts(rec.get("at", ""))
        o = out.setdefault(fl, {"views": 0, "last": None})
        o["views"] = max(o["views"], v)
        if at and (not o["last"] or at > o["last"]):
            o["last"] = at
    return out


# ── 주간 리포트 ────────────────────────────────────────────────────────────
def report_due(m: dict | None = None, now: dt.datetime | None = None) -> str | None:
    now = now or now_kst()
    if not ON or now.weekday() != 0 or not (9 <= now.hour < 12):
        return None
    m = m or load()
    wk = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"
    return None if wk in m["reports"] else wk


def _med(xs: list[int]) -> str:
    return f"{statistics.median(xs):g}" if xs else "-"


def report_text(now: dt.datetime | None = None, days: int = 7) -> str:
    now = now or now_kst()
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - dt.timedelta(days=days)
    m = load()
    rows = []
    for link, r in m["posts"].items():
        at = _ts(r.get("at", ""))
        if at and start <= at < end:
            rows.append((link, r, at))
    if not rows:
        return f"📊 스레드 주간 리포트 ({start:%m/%d}~{end - dt.timedelta(days=1):%m/%d})\n이번 주에 측정된 글이 없어요."
    by: dict[str, list[int]] = {}
    for _, r, _ in rows:
        by.setdefault(r.get("kind") or "기타", []).append(views_of(r))
    total = sum(views_of(r) for _, r, _ in rows)
    eng = {k: sum(int((r.get("last") or {}).get(k) or 0) for _, r, _ in rows) for k in ("likes", "reposts", "quotes", "shares")}
    react = [(link, r) for link, r, _ in rows
             if any(int((r.get("last") or {}).get(k) or 0) for k in ("likes", "reposts", "quotes", "shares"))]
    fol = [f for f in m["followers"] if start.strftime("%Y-%m-%d") <= f["d"] < end.strftime("%Y-%m-%d")]
    L = [f"📊 스레드 주간 리포트 ({start:%m/%d}~{end - dt.timedelta(days=1):%m/%d})", "",
         f"글 {len(rows)}개 · 조회수 합계 {total:,}"]
    if len(fol) >= 2:
        L.append(f"팔로워 {fol[0]['n']} → {fol[-1]['n']} ({fol[-1]['n'] - fol[0]['n']:+d})")
    elif fol:
        L.append(f"팔로워 {fol[-1]['n']}")
    L += ["", "글 종류별 조회수 중간값 (올린 지 24시간 기준)"]
    for k, xs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        L.append(f"· {k}: {_med(xs)} (글 {len(xs)}개, 최고 {max(xs)})")
    ang: dict[str, list[int]] = {}
    for _, r, _ in rows:
        if r.get("kind") == "딜":
            ang.setdefault(r.get("sub") or "이전 기록", []).append(views_of(r))
    if ang:
        names = {"speed": "속도", "code": "코드", "compare": "국내비교", "record": "최저가", "brand": "브랜드",
                 "season": "시즌", "size": "사이즈", "practical": "실용", "basic": "기본", "roundup": "모아보기"}
        L += ["", "딜 글 소개 방식별 중간값: " + " · ".join(
            f"{names.get(a, a)} {_med(v)}({len(v)})" for a, v in sorted(ang.items(), key=lambda kv: -statistics.median(kv[1])))]
    hours: dict[str, list[int]] = {}
    for _, r, at in rows:
        h = at.hour
        b = "아침(6~11시)" if 6 <= h < 11 else "점심(11~14시)" if h < 14 and h >= 11 else \
            "오후(14~18시)" if 14 <= h < 18 else "저녁(18~24시)" if h >= 18 else "새벽(0~6시)"
        hours.setdefault(b, []).append(views_of(r))
    L += ["", "올린 시간대별 중간값: " + " · ".join(f"{b} {_med(v)}({len(v)})" for b, v in hours.items())]
    top = sorted(rows, key=lambda x: -views_of(x[1]))[:3]
    L += ["", "이번 주 상위 글"]
    for i, (link, r, at) in enumerate(top, 1):
        L.append(f"{i}. [{r.get('kind')}] {views_of(r)}회 · {r.get('first_line', '')[:40]}\n   {link}")
    L += ["", f"반응: 좋아요 {eng['likes']} · 리포스트 {eng['reposts']} · 인용 {eng['quotes']} · 공유 {eng['shares']}"
               f" (반응 달린 글 {len(react)}개)"]
    info_k = [k for k in by if k in ("정보", "다시 올리기", "데이터")]
    if "딜" in by and info_k:
        im = statistics.median([v for k in info_k for v in by[k]])
        dm = statistics.median(by["딜"])
        L += ["", f"요약: 정보 계열 중간값 {im:g} vs 딜 글 {dm:g}"
                  + (" — 딜 글 수를 줄이고 상위 딜만 올리는 게 효율적이에요." if dm * 3 < im else ".")]
    return "\n".join(L)[:4000]


def weekly_report(send, now: dt.datetime | None = None) -> str | None:
    """send(text) 로 텔레그램 전송. 보낸 주차를 기록."""
    now = now or now_kst()
    m = load()
    wk = report_due(m, now)
    if not wk:
        return None
    send(report_text(now))
    m["reports"][wk] = now.isoformat(timespec="seconds")
    save(m)
    return wk
