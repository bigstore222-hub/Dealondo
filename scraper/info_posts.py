"""
info_posts.py — 정보 글 자동 게시 (스레드 @deal_ondo)

왜 (11/18 실측, 글 46개)
  · 이미지 있는 정보 글 조회수 중간값 55 vs 딜 글 3 — 조회수를 만드는 건 정보 글이다.
  · 반응 좋았던 글을 3주 뒤 다시 올리면 또 나간다(사이즈 환산 글 8 → 151).
  · 텍스트만 있는 정보 글은 중간값 8 → 정보 글은 항상 카드 이미지와 함께.

언제 올리나 (KST, 텔레그램 접수 워크플로가 2분마다 깨울 때 확인)
  · 저녁 20:20~21:40  매일 1개
  · 점심 12:10~13:20  블프 시즌(11/18~12/1)에만 1개 — '오늘의 면세 딜 3' 데이터 글
  · 시각 지정 글       라이브러리의 at (예: 11/27 13:50 미국 동부 블프 0시) — 80분 안에

무엇을 올리나 (우선순위)
  1) data/info_library.json 에서 오늘 날짜(또는 지금 시각)로 잡힌 글
  2) 날짜 없는 상시 글(evergreen) 중 아직 안 올린 것 — 날짜가 지났는데 못 올린 상시 글 포함
  3) 반응 좋았던 글 다시 올리기 — 조회수 50 이상 · 마지막 게시 21일 이상 지난 글
  4) 딜보드 데이터 글 — 지금 딜보드에서 관부가세 없이 받는 브랜드 딜 3개
  다 없으면 그 시간은 건너뛴다(억지로 채우지 않는다).

기록: threads/inbox.json 의 "info" (시간대별 결과·게시 기록) → threads_metrics.py 가 글 종류를 구분하는 데 쓴다.
환경변수: INFO_POSTS=0 이면 전부 끔. INFO_REPOST_MIN(기본 50), INFO_REPOST_DAYS(기본 21).
"""
from __future__ import annotations
import datetime as dt, hashlib, html, json, os, re, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "data" / "info_library.json"
SCHEDULE = ROOT / "data" / "threads_schedule.json"
CARD_DIR = ROOT / "threads" / "info"
TEMPLATE = Path(__file__).with_name("info_card.html")
KST = dt.timezone(dt.timedelta(hours=9))
REPO = os.environ.get("GITHUB_REPOSITORY", "bigstore222-hub/Dealondo")
SITE = "https://bigstore222-hub.github.io/Dealondo/"
ON = (os.environ.get("INFO_POSTS") or "1").strip() not in ("0", "off", "false")
REPOST_MIN = int(os.environ.get("INFO_REPOST_MIN", "50"))
REPOST_DAYS = int(os.environ.get("INFO_REPOST_DAYS", "21"))
MAX_TRIES = 3

SLOTS = {"lunch": ((12, 10), (13, 20)), "evening": ((20, 20), (21, 40))}
TIMED_WIN_MIN = 80
SEASON = ((11, 18), (12, 1))          # 블프 시즌: 점심 데이터 글을 더한다
BF_DAY = {2026: dt.date(2026, 11, 27)}
# 예전 예약 글 중 블프 시즌에만 다시 올릴 수 있는 글(날짜가 박힌 내용)
SEASONAL_NOS = {5, 6, 15, 18, 19}
NO_REPOST_NOS = {3, 4, 7, 8, 11, 15, 16, 17, "공개"}  # 텍스트만이었거나 반응이 낮았던 글·공개 안내


# ── 공통 ──────────────────────────────────────────────────────────────────
def now_kst() -> dt.datetime:
    return dt.datetime.now(KST)


def first_line(s: str) -> str:
    for ln in (s or "").splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def in_season(d: dt.date) -> bool:
    a, b = SEASON
    return (d.month, d.day) >= a and (d.month, d.day) <= b if a <= b else False


def load_library() -> list[dict]:
    try:
        return json.loads(LIBRARY.read_text(encoding="utf-8")).get("items", [])
    except Exception:
        return []


def load_schedule() -> dict:
    try:
        return {it.get("no"): it for it in json.loads(SCHEDULE.read_text(encoding="utf-8"))}
    except Exception:
        return {}


def info_state(st: dict) -> dict:
    s = st.setdefault("info", {})
    s.setdefault("slots", {})
    s.setdefault("log", [])
    # 오래된 시간대 기록은 정리(60일)
    cut = (now_kst() - dt.timedelta(days=60)).strftime("%Y-%m-%d")
    s["slots"] = {k: v for k, v in s["slots"].items() if k[:10] >= cut or k.startswith("T:")}
    s["log"] = s["log"][-400:]
    return s


def posted_ids(st: dict) -> set[str]:
    return {x.get("id") for x in info_state(st)["log"] if x.get("link")}


# ── 언제 ──────────────────────────────────────────────────────────────────
def due(st: dict, now: dt.datetime | None = None) -> tuple[str, str] | None:
    """지금 올릴 시간대가 있으면 (시간대 키, 종류) — 이미 올렸거나 3번 실패한 시간대는 제외."""
    if not ON:
        return None
    now = now or now_kst()
    s = info_state(st)

    def open_(key: str) -> bool:
        r = s["slots"].get(key)
        if not r:
            return True
        if r.get("status") == "fail" and r.get("tries", 0) < MAX_TRIES:
            try:
                return (now - dt.datetime.fromisoformat(r["at"])).total_seconds() > 600
            except Exception:
                return True
        return False

    for it in load_library():
        if it.get("at"):
            at = dt.datetime.fromisoformat(it["at"]).replace(tzinfo=KST)
            if at <= now < at + dt.timedelta(minutes=TIMED_WIN_MIN) and open_(f"T:{it['id']}"):
                return f"T:{it['id']}", "timed"
    for name, (a, b) in SLOTS.items():
        if name == "lunch" and not in_season(now.date()):
            continue
        if dt.time(*a) <= now.time() < dt.time(*b):
            key = f"{now:%Y-%m-%d}-{name}"
            if open_(key):
                return key, name
    return None


# ── 무엇을 ────────────────────────────────────────────────────────────────
def _metrics_best() -> dict[str, dict]:
    """첫 줄 → {'views': 최고 조회수, 'last': 마지막 게시 시각} (threads/metrics.json)"""
    try:
        import threads_metrics as M
        return M.by_first_line()
    except Exception:
        return {}


def _repost_item(no, sched: dict, reply: str | None = None) -> dict | None:
    it = sched.get(no)
    if not it or not it.get("image"):
        return None
    return {"id": f"S{no}", "type": "repost", "text": it["text"], "image": it["image"],
            "reply": reply or REPLY_CALC, "label": f"다시 올리기 · {it.get('title', '')}"}


REPLY_CALC = f"관부가세까지 넣은 한국 도착가는 여기서 바로 계산돼요.\n{SITE}calc.html"


def winners(st: dict, now: dt.datetime) -> list[dict]:
    """반응 좋았던 글(조회수 REPOST_MIN 이상) 중 마지막 게시가 REPOST_DAYS 이상 지난 것, 조회수 높은 순."""
    best = _metrics_best()
    sched = load_schedule()
    lib = {it["id"]: it for it in load_library()}
    out = []
    cands = []
    for no, it in sched.items():
        if no in NO_REPOST_NOS or not it.get("image"):
            continue
        if no in SEASONAL_NOS and not (in_season(now.date()) and now.date() <= BF_DAY.get(now.year, now.date())):
            continue
        cands.append(_repost_item(no, sched))
    for it in lib.values():
        if it.get("text") and it.get("evergreen") and it.get("image"):
            cands.append({"id": it["id"], "type": "repost", "text": it["text"], "image": it["image"],
                          "reply": it.get("reply") or REPLY_CALC, "label": f"다시 올리기 · {it['id']}"})
    for c in cands:
        if not c:
            continue
        m = best.get(first_line(c["text"]))
        if not m or m.get("views", 0) < REPOST_MIN:
            continue
        last = m.get("last")
        if last and (now - last).days < REPOST_DAYS:
            continue
        out.append((m["views"], c))
    out.sort(key=lambda x: -x[0])
    return [c for _, c in out]


def library_item(it: dict) -> dict:
    if it.get("repost") is not None:
        r = _repost_item(it["repost"], load_schedule(), it.get("reply"))
        if r:
            r["id"] = it["id"]
        return r
    return {"id": it["id"], "type": "library", "text": it["text"], "image": it.get("image"),
            "card": it.get("card"), "reply": it.get("reply") or REPLY_CALC, "label": f"정보 글 {it['id']}"}


def choose(st: dict, key: str, kind: str, now: dt.datetime | None = None) -> dict | None:
    now = now or now_kst()
    done = posted_ids(st)
    lib = load_library()
    today = now.strftime("%Y-%m-%d")
    if kind == "timed":
        it = next((x for x in lib if f"T:{x['id']}" == key), None)
        return library_item(it) if it else None
    for it in lib:                                   # 1) 오늘 날짜로 잡힌 글
        if it.get("date") == today and it.get("slot", "evening") == kind and it["id"] not in done:
            r = library_item(it)
            if r:
                return r
    if kind == "lunch":                              # 점심은 데이터 글만(라이브러리를 아껴 쓴다)
        return data_post(st, now)
    for it in lib:                                   # 2) 상시 글 — 날짜 없거나, 날짜가 지났는데 못 올린 것
        if it.get("evergreen") and it["id"] not in done and (not it.get("date") or it["date"] < today) \
                and not it.get("at"):
            r = library_item(it)
            if r:
                return r
    w = winners(st, now)                             # 3) 반응 좋았던 글 다시
    if w:
        return w[0]
    return data_post(st, now)                        # 4) 딜보드 데이터 글


# ── 데이터 글: 지금 딜보드에서 관부가세 없이 받는 브랜드 딜 3개 ──────────────────────────
def _board() -> dict:
    try:
        return json.loads((ROOT / "deals.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _posted_recently(*urls, hours: float = 24) -> bool:
    import threads_deal as TD
    keys = {TD.norm(u) for u in urls if u} - {""}
    cut = now_kst() - dt.timedelta(hours=hours)
    for x in TD.recent_posts(50):
        try:
            if dt.datetime.fromisoformat(x["at"]) >= cut and TD.norm(x.get("url", "")) in keys:
                return True
        except Exception:
            pass
    return False


def pick_free_deals(n: int = 3, now: dt.datetime | None = None) -> list[dict]:
    import deal_writer as W
    import threads_deal as TD
    now = now or now_kst()
    data = _board()
    out, brands = [], set()
    cands = []
    for d in data.get("deals", []):
        url = d.get("url") or ""
        try:
            seen = dt.datetime.fromisoformat(d.get("last_seen") or d.get("detected_at"))
        except Exception:
            continue
        p, pct = d.get("price_current"), d.get("discount_pct") or 0
        if (now - seen).total_seconds() > 8 * 3600 or not p or p < 20 or not d.get("price_list") or pct < 40:
            continue
        if d.get("brand_tier") not in ("premium", "known"):
            continue
        if any(x in url for x in ("slickdeals.net", "dealnews.com", "dealsofamerica", "/search", "/sale")):
            continue
        f = W.facts(d, data.get("fx_usd_krw"))
        if not f["free"] or W.problems(f):
            continue
        if _posted_recently(url, d.get("buy_url")):
            continue
        cands.append((d, f))
    cands.sort(key=lambda x: ((x[0].get("score") or 0) + (10 if x[0].get("brand_tier") == "premium" else 0),
                              x[1]["pct"] or 0), reverse=True)
    for d, f in cands:
        b = (d.get("brand") or "").lower()
        if b in brands:
            continue
        brands.add(b)
        out.append((d, f))
        if len(out) >= n:
            break
    return out


def data_post(st: dict, now: dt.datetime | None = None) -> dict | None:
    import deal_writer as W
    now = now or now_kst()
    picks = pick_free_deals(3, now)
    if len(picks) < 3:
        return None
    bf = BF_DAY.get(now.year)
    dd = (bf - now.date()).days if bf else None
    seed = now.strftime("%Y%m%d%H")
    if dd is not None and 1 <= dd <= 12:
        head = f"블프 D-{dd}, 지금 관부가세 없이 받는 브랜드 딜 3개."
    elif dd == 0:
        head = "블프 당일, 관부가세 없이 받는 브랜드 딜 3개만 골랐어요."
    else:
        head = W.pick(["지금 미국에서 관부가세 없이 받는 브랜드 딜 3개.",
                       "면세한도 안에서 고른 오늘의 브랜드 딜 3개.",
                       "$200 아래라 세금 걱정 없는 브랜드 딜 3개."], seed)
    lines = [head, ""]
    rows = []
    for i, (d, f) in enumerate(picks, 1):
        lines.append(f"{i}. {f['name']} {W.usd(f['now'])} ({f['pct']}% 할인) · {W.won(f['krw_now'])}")
        rows.append([f["name"], f"{W.usd(f['was'])} → {W.usd(f['now'])} · {f['pct']}% 할인 · {W.won(f['krw_now'])}"])
    lines += ["", "전부 미국발 면세한도 안이라 관부가세 없이 받아요. 배대지 배송비만 더하면 돼요.",
              "가격·재고는 금방 바뀌니 결제 전에 한 번 더 확인하세요."]
    links = [f"{i}. {d.get('buy_url') or d.get('url')}" for i, (d, f) in enumerate(picks, 1)]
    reply = "\n".join(links)
    if any(d.get("buy_url") and d.get("buy_url") != d.get("url") for d, f in picks):
        reply = "일부 링크는 제휴 링크로, 구매 시 일정액의 수수료를 제공받습니다.\n" + reply
    card = {"kicker": f"오늘의 면세 딜 · {now:%m/%d}" + (f" · 블프 D-{dd}" if dd and 1 <= dd <= 12 else ""),
            "head": "관부가세 없이 받는<br><em>브랜드 딜 3</em>", "sub": "",
            "rows": rows, "note": "미국발 면세한도 $200(무선기기 $150) 안 · 가격은 수시로 바뀌어요."}
    return {"id": f"D{now:%Y%m%d%H%M}", "type": "data", "text": "\n".join(lines)[:500], "reply": reply[:500],
            "card": card, "image": None, "label": "딜보드 데이터 글(면세 딜 3)",
            "deals": [d.get("url") for d, f in picks], "buy": [d.get("buy_url") or d.get("url") for d, f in picks]}


# ── 카드 ──────────────────────────────────────────────────────────────────
def render_card(card: dict, out: Path) -> None:
    esc = lambda s: html.escape(str(s or ""))
    head = card.get("head") or ""
    plain = re.sub(r"<[^>]+>", "", head.replace("<br>", "\n"))
    longest = max((len(x) for x in plain.split("\n")), default=8)
    hsize = 118 if longest <= 7 else 104 if longest <= 9 else 92 if longest <= 11 else 80
    rows = card.get("rows") or []
    long_row = max((len(r[0]) + len(r[1] if len(r) > 1 else "") for r in rows), default=0)
    tsize, ssize, rpad = (40, 30, 34) if long_row < 38 else (36, 28, 30)
    rows_html = "".join(
        f'<div class="row"><div class="n">{i}</div><div><div class="t">{esc(r[0])}</div>'
        f'<div class="s">{esc(r[1] if len(r) > 1 else "")}</div></div></div>' for i, r in enumerate(rows, 1))
    # head 는 우리가 쓴 <br>·<em> 만 허용
    safe_head = re.sub(r"<(?!/?(br|em)\b)[^>]*>", "", head)
    h = (TEMPLATE.read_text(encoding="utf-8")
         .replace("{{HSIZE}}", str(hsize)).replace("{{TSIZE}}", str(tsize)).replace("{{SSIZE}}", str(ssize))
         .replace("{{RPAD}}", str(rpad))
         .replace("{{KICKER}}", esc(card.get("kicker"))).replace("{{HEAD}}", safe_head)
         .replace("{{SUB}}", esc(card.get("sub"))).replace("{{ROWS}}", rows_html)
         .replace("{{NOTE}}", esc(card.get("note"))))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".html")
    tmp.write_text(h, encoding="utf-8")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1080, "height": 1350})
        pg.goto(tmp.resolve().as_uri())
        pg.wait_for_timeout(400)
        pg.screenshot(path=str(out))
        b.close()
    tmp.unlink(missing_ok=True)


def _url_ok(u: str) -> bool:
    try:
        req = urllib.request.Request(u, method="GET", headers={"Range": "bytes=0-512", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status in (200, 206) and (r.headers.get("Content-Type") or "").startswith("image/")
    except Exception:
        return False


def image_url(item: dict) -> str | None:
    """올라가 있는 이미지면 raw 주소, 아니면 만들어서(카드) 커밋·푸시 후 주소."""
    import threads_deal as TD
    rel = item.get("image")
    if rel:
        u = f"https://raw.githubusercontent.com/{REPO}/main/{rel}"
        if _url_ok(u):
            return u
        path = ROOT / rel
        if not path.exists() and item.get("card"):
            render_card(item["card"], path)
        if path.exists():
            return TD.publish_image(path)
        return None
    if item.get("card"):
        path = CARD_DIR / "data" / f"{item['id']}.png"
        render_card(item["card"], path)
        return TD.publish_image(path)
    return None


# ── 게시 ──────────────────────────────────────────────────────────────────
def run(st: dict, chat: str, tg_send, dry: bool = False, now: dt.datetime | None = None) -> str | None:
    """시간대가 됐으면 한 개 올린다. 결과 메모를 돌려준다(없으면 None). tg_send(text) — 텔레그램 알림."""
    import threads_api as T
    now = now or now_kst()
    d = due(st, now)
    if not d:
        return None
    key, kind = d
    s = info_state(st)
    rec = s["slots"].setdefault(key, {"tries": 0})
    rec["at"] = now.isoformat(timespec="seconds")
    try:
        item = choose(st, key, kind, now)
    except Exception as e:
        item = None
        print("[정보 글] 고르기 실패:", repr(e))
    if not item:
        rec.update(status="skip", note="올릴 글 없음")
        print(f"[정보 글] {key} — 올릴 글 없음, 건너뜀")
        return f"{key} 건너뜀"
    rec["tries"] = rec.get("tries", 0) + 1
    rec["id"] = item["id"]
    if dry:
        rec.update(status="dry")
        tg_send(f"🧪 (dry-run) 정보 글 [{item['label']}]\n\n{item['text']}\n\n── 첫 답글 ──\n{item['reply']}")
        return f"{key} dry"
    token = re.sub(r"\s", "", os.environ.get("THREADS_ACCESS_TOKEN") or "")
    uid = re.sub(r"\s", "", os.environ.get("THREADS_USER_ID") or "")
    try:
        img = image_url(item)
        pid = T.publish_post(uid, token, item["text"], img)
        link = T.permalink(pid, token)
        note = "이미지" if img else "글만"
        if item.get("reply"):
            try:
                T.publish_post(uid, token, item["reply"], reply_to=pid)
                note += " · 첫 답글 완료"
            except T.ThreadsError as e:
                note += f" · 첫 답글 실패 ({str(e)[:80]})"
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)[:250]}"
        rec.update(status="fail", error=msg)
        print("[정보 글] 실패:", msg)
        if rec["tries"] >= MAX_TRIES:
            tg_send(f"❌ 정보 글 게시 실패 ({MAX_TRIES}번 시도) [{item['label']}]\n{msg}")
        return f"{key} 실패"
    rec.update(status="posted", link=link, error="")
    s["log"].append({"at": now.isoformat(timespec="seconds"), "id": item["id"], "type": item["type"],
                     "slot": kind, "link": link, "first_line": first_line(item["text"])[:80]})
    if item["type"] == "data":
        import threads_deal as TD
        for u in item.get("deals", []):        # 같은 상품을 24시간 안에 딜 글로 또 올리지 않게
            TD.record(u, "게시 완료", "정보 글(면세 딜 3)에 포함", link, angle="data3",
                      opener=first_line(item["text"])[:80])
    tg_send(f"📰 정보 글 올렸어요 ({item['label']})\n{link}\n{note}\n\n{item['text']}")
    print(f"[정보 글] {key} 게시 완료 — {item['label']} {link}")
    return f"{key} 게시"


# ── 일일 리포트용 요약 ─────────────────────────────────────────────────────────
SLOT_KR = {"lunch": "점심", "evening": "저녁", "timed": "시각 지정"}


def slot_summary(st: dict, day: dt.date) -> list[str]:
    """그날 정보 글 시간대 결과 — '저녁 · 게시 · L01' 같은 줄들."""
    s = info_state(st)
    out = []
    lib_at = {f"T:{x['id']}": x.get("at", "")[:10] for x in load_library() if x.get("at")}
    for k, r in sorted(s["slots"].items(), key=lambda kv: kv[1].get("at", "")):
        d = lib_at.get(k) if k.startswith("T:") else k[:10]
        if d != day.isoformat():
            continue
        name = "시각 지정" if k.startswith("T:") else SLOT_KR.get(k[11:], k[11:])
        st_kr = {"posted": "게시", "skip": "건너뜀(올릴 글 없음)", "fail": "실패", "dry": "테스트"}.get(r.get("status"), r.get("status"))
        out.append(f"{name} · {st_kr}" + (f" · {r.get('id')}" if r.get("id") else "")
                   + (f" · {r.get('error', '')[:60]}" if r.get("status") == "fail" else ""))
    return out


def today_plan(st: dict, now: dt.datetime | None = None) -> list[str]:
    """오늘 남은 정보 글 시간대와 지금 기준으로 올라갈 글(바뀔 수 있음)."""
    if not ON:
        return ["정보 글 자동 게시 꺼짐(INFO_POSTS=0)"]
    now = now or now_kst()
    out = []
    s = info_state(st)
    for it in load_library():
        if it.get("at", "")[:10] == now.strftime("%Y-%m-%d") and f"T:{it['id']}" not in s["slots"]:
            out.append(f"{it['at'][11:16]} 시각 지정 · {it['id']} {first_line(it.get('text', ''))[:30]}")
    for name, (a, b) in SLOTS.items():
        if name == "lunch" and not in_season(now.date()):
            continue
        key = f"{now:%Y-%m-%d}-{name}"
        if key in s["slots"]:
            continue
        t = now.replace(hour=a[0], minute=a[1] + 5, second=0, microsecond=0)
        try:
            item = choose(st, key, name, t)
        except Exception:
            item = None
        if item:
            out.append(f"{a[0]:02d}:{a[1]:02d} {SLOT_KR[name]} · {item['label']} — {first_line(item['text'])[:30]}")
        else:
            out.append(f"{a[0]:02d}:{a[1]:02d} {SLOT_KR[name]} · " + ("면세 딜 3개가 모이면 데이터 글(아니면 건너뜀)"
                                                                        if name == "lunch" else "올릴 글 없음"))
    return sorted(out) or ["오늘 남은 정보 글 시간대 없음"]
