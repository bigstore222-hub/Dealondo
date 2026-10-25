"""
threads_post.py — 스레드(@deal_ondo) 예약 글 자동 게시

GitHub Actions(.github/workflows/threads.yml)가 하루 한 번 실행한다.
  1) data/threads_schedule.json 에서 '게시일이 오늘 이전인데 아직 안 올라간 글' 중 가장 오래된 1개를 고른다
     (밀린 글이 있으면 하루 1개씩 자연스럽게 순연된다 — 한 번에 몰아 올리지 않는다)
  2) 이미지가 있으면 GitHub Pages 주소로 첨부해 게시 → 첫 답글을 바로 단다
  3) 결과와 '다음 예정 글' 미리보기를 텔레그램(TELEGRAM_CHAT_ID)으로 보낸다

'이미 올렸는지'는 상태 파일 대신 내 스레드 최근 글을 조회해 본문 첫 줄로 판단한다.
→ 리포에 커밋할 것이 없어서 딜보드 워크플로와 푸시 충돌이 없다.

본문/답글에 [노션 공개 링크] 같은 빈칸이 남아 있으면 그 글은 올리지 않고 알림만 보낸다.

실행:
  python threads_post.py            # 게시
  python threads_post.py --dry-run  # 무엇이 올라갈지 보기만
  python threads_post.py --refresh  # 장기 토큰 60일 연장 (주 1회)

환경변수: THREADS_ACCESS_TOKEN, THREADS_USER_ID (필수)
          TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (선택, 결과 알림)
          THREADS_IMAGE_BASE (선택, 기본 GitHub Pages 주소)
          GH_PAT (선택, 토큰 연장 시 GitHub 시크릿 자동 갱신용)
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, re, subprocess, sys, urllib.parse, urllib.request
from pathlib import Path

import threads_api as T

ROOT = Path(__file__).resolve().parent.parent
SCHEDULE = ROOT / "data" / "threads_schedule.json"
IMAGE_BASE = os.environ.get("THREADS_IMAGE_BASE",
                            "https://bigstore222-hub.github.io/Dealondo/").rstrip("/") + "/"
KST = dt.timezone(dt.timedelta(hours=9))
PLACEHOLDER = re.compile(r"\[[^\]\n]*(링크|주소)[^\]\n]*\]")


MAX_PER_DAY = 2
STATUS_JSON = ROOT / "threads_status.json"   # Dealondo Threads 워크플로가 커밋 → monitor.html


def save_status(result: str, detail: str, it: dict | None, link: str = "") -> None:
    # 수동 실행(창 모드가 아님)에서는 결과가 무엇이든 텔레그램으로도 알린다.
    # 초록색 '성공'인데 글이 안 올라간 경우의 이유를 휴대폰에서 바로 보기 위함.
    if not QUIET.get("window") and result != "게시 완료":
        tg(f"ℹ️ 스레드 수동 실행 결과: {result}" + (f" — {label(it)}" if it else "")
           + (f"\n{detail}" if detail else ""))
    _save_status(result, detail, it, link)


def _save_status(result: str, detail: str, it: dict | None, link: str = "") -> None:
    """
    마지막 게시 시도 결과를 threads_status.json 에 남긴다.
    Dealondo Threads 워크플로가 이 파일을 커밋하므로, Actions 로그를 열지 않아도
    공개 주소(threads_status.json, monitor.html)에서 게시가 왜 됐는지/안 됐는지 확인할 수 있다.
    """
    try:
        mon = json.loads(STATUS_JSON.read_text(encoding="utf-8"))
    except Exception:
        mon = {}
    entry = {"at": dt.datetime.now(KST).isoformat(timespec="seconds"), "result": result,
             "detail": detail, "item": label(it) if it else "", "date": it["date"] if it else "",
             "link": link}
    hist = [entry] + [h for h in (mon.get("history") or [])][:19]
    try:
        STATUS_JSON.write_text(json.dumps({"last": entry, "history": hist}, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    except Exception as e:
        print("  상태 기록 실패:", e)


def topic_of(it: dict) -> str | None:
    """
    주제 태그. 10/22 기본 태그(#해외직구)를 붙이기 시작한 뒤 조회수가 떨어져, 기본값은 '태그 없음'으로 되돌렸다.
    일정 파일의 글에 "topic" 을 적은 글에만 붙는다(비교 실험용). 전체를 다시 켜려면 THREADS_TOPIC_TAG 환경변수.
    """
    return it.get("topic") or os.environ.get("THREADS_TOPIC_TAG") or None


def today_kst() -> dt.date:
    return dt.datetime.now(KST).date()


def first_line(s: str) -> str:
    for ln in (s or "").splitlines():
        if ln.strip():
            return re.sub(r"\s+", " ", ln.strip())
    return ""


def tg(msg: str) -> None:
    # 시크릿 값에 공백·줄바꿈이 섞여 있으면 URL 오류로 발송이 실패한다(10/17 실제 발생).
    # 토큰·chat id에는 원래 공백이 없으므로 notify.py 와 같이 모든 공백 문자를 제거한다.
    tok = re.sub(r"\s", "", os.environ.get("TELEGRAM_BOT_TOKEN") or "")
    chat = re.sub(r"\s", "", os.environ.get("TELEGRAM_CHAT_ID") or "")
    print("[알림]", msg.replace("\n", " | "))
    if not (tok and chat):
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": msg,
                                   "disable_web_page_preview": "true"}).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=15) as r:
            if not json.loads(r.read()).get("ok"):
                print("  텔레그램 응답 ok=false")
    except Exception as e:
        print("  텔레그램 전송 실패:", type(e).__name__, str(e)[:150])


def load_schedule() -> list[dict]:
    items = json.loads(SCHEDULE.read_text(encoding="utf-8"))
    for it in items:
        it["_date"] = dt.date.fromisoformat(it["date"])
    return sorted(items, key=lambda x: x["_date"])


def posted_index(uid: str, token: str) -> dict[str, dict]:
    """최근 내 글의 첫 줄 → 글 정보."""
    out = {}
    for p in T.my_posts(uid, token, limit=60):
        fl = first_line(p.get("text", ""))
        if fl:
            out[fl] = p
    return out


def image_ok(url: str) -> bool:
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200 and r.headers.get("Content-Type", "").startswith("image/")
    except Exception:
        return False


def label(it: dict) -> str:
    return f"#{it['no']} {it['title']}"


# 딜보드 워크플로(20분마다)에 얹어 돌릴 때: 게시 창(window~until) 안에서만 시도한다.
# GitHub 예약 실행은 수십 분 밀리거나 가끔 건너뛰므로, 20분마다 도는 실행 중
# '게시 시각 이후 첫 실행'이 올리게 해서 하루 한 번을 확실히 보장한다.
QUIET = {"on": False}        # 창 모드에서 같은 알림이 20분마다 반복되지 않게


def _hm(s: str) -> dt.time:
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def in_window(start: str, until: str) -> tuple[bool, bool]:
    """(지금 창 안인가, 창의 첫 20분인가)"""
    now = dt.datetime.now(KST)
    a = dt.datetime.combine(now.date(), _hm(start), KST)
    b = dt.datetime.combine(now.date(), _hm(until), KST)
    return a <= now < b, a <= now < a + dt.timedelta(minutes=20)


def fail_tg(msg: str) -> None:
    """실패 알림 — 창 모드에선 첫 시도와 마지막 시도에서만 보낸다(20분마다 반복 방지)."""
    if QUIET["on"] and not QUIET.get("last"):
        print("[알림 생략 — 재시도 중]", msg.replace("\n", " | "))
        return
    tg(msg + ("\n(오늘 마지막 자동 시도였어요. Actions에서 post로 직접 실행해 주세요.)" if QUIET.get("last") else ""))


def run_post(dry: bool, force: bool) -> int:
    token = re.sub(r"\s", "", os.environ.get("THREADS_ACCESS_TOKEN") or "")
    uid = re.sub(r"\s", "", os.environ.get("THREADS_USER_ID") or "")
    items, today = load_schedule(), today_kst()
    if not (token and uid):
        if not dry:
            print("[!] THREADS_ACCESS_TOKEN / THREADS_USER_ID 가 없습니다. 게시를 건너뜁니다.")
            save_status("설정 누락", "GitHub Secrets에 THREADS_ACCESS_TOKEN / THREADS_USER_ID 가 없음", None)
            return 0
        posted = {}
    else:
        posted = posted_index(uid, token)

    is_posted = lambda it: first_line(it["text"]) in posted
    due = [it for it in items if it["_date"] <= today and not is_posted(it)]
    blocked = [it for it in due if PLACEHOLDER.search(it["text"] + "\n" + (it.get("reply") or ""))]
    ready = [it for it in due if it not in blocked]

    print(f"오늘(KST) {today} · 예약 {len(items)}개 · 게시됨 {sum(map(is_posted, items))}개 · "
          f"올릴 차례 {len(ready)}개 · 빈칸 때문에 보류 {len(blocked)}개")
    for it in ([] if QUIET["on"] else blocked):
        tg(f"⚠️ 스레드 예약 글 보류: {label(it)} ({it['date']})\n"
           f"본문이나 첫 답글에 [링크] 빈칸이 남아 있어요. data/threads_schedule.json 을 채워 커밋해 주세요.")

    # 하루 게시 상한. 예전엔 '하루 1개'였는데, 밀린 글을 늦게(자정 넘어) 올리면
    # 그날 예정 글이 막히는 문제가 있었다(10/16 실제 발생). 그래서 하루 2개까지 허용해
    # 밀린 글 1개 + 그날 글 1개가 같은 날 나갈 수 있게 한다. 중복 게시는 '본문 첫 줄'로 막는다.
    sched_lines = {first_line(it["text"]) for it in items}
    posted_today = 0
    for fl, p in posted.items():
        try:
            d = dt.datetime.strptime(p.get("timestamp", ""), "%Y-%m-%dT%H:%M:%S%z").astimezone(KST).date()
        except ValueError:
            continue
        if d == today and fl in sched_lines:
            posted_today += 1
    if not force and posted_today >= MAX_PER_DAY:
        print(f"오늘 이미 예약 글 {posted_today}개를 올렸습니다(하루 상한 {MAX_PER_DAY}). 내일 이어서 올립니다.")
        save_status("상한 도달", f"오늘 {posted_today}개 게시됨", ready[0] if ready else None)
        return 0

    if not ready:
        print("올릴 글이 없습니다.")
        save_status("올릴 글 없음", "", None)
        if token and uid and not dry:
            collect_insights(uid, token)
            if not QUIET["on"] or QUIET.get("first"):
                gap_alert(items, today)
        return 0
    it = ready[0]
    img = (IMAGE_BASE + it["image"]) if it.get("image") else None
    late = (today - it["_date"]).days

    print(f"\n▶ 게시 대상: {label(it)} (예정일 {it['date']}{f', {late}일 밀림' if late else ''})")
    print(f"  이미지: {img or '없음'}")
    print("  본문 ─────────\n" + it["text"])
    if it.get("reply"):
        print("  첫 답글 ───────\n" + it["reply"])
    if dry:
        print("\n(--dry-run: 실제로 올리지 않았습니다. 올리려면 Run workflow 에서 post 를 고르세요)")
        return 0

    if img and not image_ok(img):
        save_status("실패", f"이미지 주소를 열 수 없음: {img}", it)
        fail_tg(f"❌ 스레드 게시 중단: {label(it)}\n이미지 주소를 열 수 없어요: {img}\n"
           f"threads/ 폴더 이미지가 커밋·배포됐는지 확인해 주세요.")
        return 1

    try:
        pid = T.publish_post(uid, token, it["text"], img, topic_tag=topic_of(it))
    except T.ThreadsError as e:
        save_status("실패", str(e)[:300], it)
        fail_tg(f"❌ 스레드 게시 실패: {label(it)}\n{e}")
        return 1
    link = T.permalink(pid, token)
    reply_note = ""
    if it.get("reply"):
        try:
            T.publish_post(uid, token, it["reply"], reply_to=pid)
            reply_note = "첫 답글 완료"
        except T.ThreadsError as e:
            reply_note = f"첫 답글 실패 — 직접 달아 주세요 ({e})"

    nxt = [x for x in items if x is not it and not is_posted(x) and x not in blocked]
    nxt_txt = ""
    if nxt:
        n = nxt[0]
        when = "내일" if n["_date"] <= today + dt.timedelta(days=1) else n["date"]
        nxt_txt = (f"\n\n📝 다음 글({when}): {label(n)}\n{n['text'][:180]}"
                   f"{'…' if len(n['text']) > 180 else ''}\n고칠 곳이 있으면 data/threads_schedule.json 수정 후 커밋")
    save_status("게시 완료", reply_note, it, link)
    try:
        gap_alert(items, today + dt.timedelta(days=1))
        collect_insights(uid, token)
    except Exception as e:
        print("[성과] 수집 실패:", e)
    tg(f"✅ 스레드 게시 완료: {label(it)}\n{link}\n{reply_note}{nxt_txt}")
    return 0


INSIGHT_METRICS = "views,likes,replies,reposts,quotes,shares"


def collect_insights(uid: str, token: str, limit: int = 20) -> None:
    """
    최근 글의 조회수·좋아요·답글·리포스트·공유를 threads_status.json 'insights' 에 남긴다(monitor.html 표).
    무엇이 조회수를 올리고 내리는지(이미지·태그·주제·시간) 감이 아니라 숫자로 보기 위함.
    토큰에 threads_manage_insights 권한이 없으면 그 사실만 기록한다.
    """
    rows, err = [], ""
    try:
        posts = [p for p in T.my_posts(uid, token, limit=limit) if p.get("text")]
    except T.ThreadsError as e:
        posts, err = [], str(e)[:200]
    sched = {first_line(it["text"]): it for it in load_schedule()}
    for p in posts:
        try:
            res = T._req("GET", f"{T.VER}/{p['id']}/insights", {"metric": INSIGHT_METRICS, "access_token": token})
        except T.ThreadsError as e:
            err = str(e)[:200]
            break
        m = {}
        for d in res.get("data", []):
            v = d.get("values") or [{}]
            m[d.get("name")] = (d.get("total_value") or {}).get("value", v[0].get("value", 0))
        fl = first_line(p.get("text", ""))
        it = sched.get(fl)
        rows.append({"at": p.get("timestamp"), "first_line": fl[:60], "link": p.get("permalink"),
                     "media": p.get("media_type"), "kind": "예약 글" if it else "딜",
                     "tag": (it.get("topic") if it else None), **m})
    try:
        mon = json.loads(STATUS_JSON.read_text(encoding="utf-8"))
    except Exception:
        mon = {}
    mon["insights"] = {"at": dt.datetime.now(KST).isoformat(timespec="seconds"), "error": err, "posts": rows}
    STATUS_JSON.write_text(json.dumps(mon, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[성과] {len(rows)}개 글 수집" + (f" · 오류: {err}" if err else ""))


def gap_alert(items: list, today: dt.date) -> None:
    """앞으로 3일 안에 올릴 예약 글이 비어 있으면 미리 알린다(10/25처럼 예약이 비어 조용히 넘어가는 일 방지)."""
    days = {it["_date"] for it in items}
    empty = [today + dt.timedelta(days=i) for i in range(0, 4) if today + dt.timedelta(days=i) not in days]
    if empty:
        tg("📭 스레드 예약 글이 비어 있는 날이 있어요: " + ", ".join(d.strftime("%m/%d") for d in empty)
           + "\ndata/threads_schedule.json 에 글을 채워야 그날 글이 올라가요.")


def run_refresh() -> int:
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    if not token:
        print("토큰 없음 — 건너뜀")
        return 0
    try:
        res = T.refresh_long_lived(token)
    except T.ThreadsError as e:
        tg(f"⚠️ 스레드 토큰 연장 실패: {e}\n17_스레드설정.bat 으로 새 토큰을 등록해 주세요.")
        return 1
    new, days = res.get("access_token", ""), int(res.get("expires_in", 0)) // 86400
    exp = (dt.datetime.now(KST) + dt.timedelta(days=days)).date()
    if not new or new == token:
        print(f"토큰 연장 완료 — 만료 예정 {exp}")
        return 0
    pat = os.environ.get("GH_PAT")
    if pat:
        r = subprocess.run(["gh", "secret", "set", "THREADS_ACCESS_TOKEN", "--body", new],
                           env={**os.environ, "GH_TOKEN": pat}, capture_output=True, text=True)
        if r.returncode == 0:
            print(f"새 토큰을 GitHub 시크릿에 저장 — 만료 예정 {exp}")
            return 0
        print("시크릿 저장 실패:", r.stderr[:200])
    tg(f"⚠️ 스레드 토큰이 새로 발급됐지만 GitHub 시크릿에 자동 저장하지 못했어요.\n"
       f"기존 토큰이 만료되기 전에 17_스레드설정.bat 으로 다시 등록해 주세요.")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="오늘 이미 올렸어도 한 번 더")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--window", help="이 시각(KST, HH:MM) 이후에만 게시 시도. 예: 20:20")
    ap.add_argument("--until", default="23:00", help="게시 시도 마감 시각(KST)")
    a = ap.parse_args()
    if a.refresh:
        return run_refresh()
    # 예전 설정에서는 딜보드 워크플로(Dealondo Radar)에도 게시 단계가 붙어 있다.
    # 이제 게시는 Dealondo Threads 워크플로가 저녁에 7번 시도하며 전담한다.
    # 두 워크플로가 같은 순간에 같은 글을 올리는 일을 막기 위해 여기서는 건너뛴다.
    if a.window and os.environ.get("GITHUB_WORKFLOW") == "Dealondo Radar":
        print("[스레드] 게시는 Dealondo Threads 워크플로가 담당 — 딜보드 실행에서는 건너뜀")
        return 0
    if a.window:
        inside, first = in_window(a.window, a.until)
        if not inside:
            print(f"[스레드] 게시 창({a.window}~{a.until} KST) 밖 — 건너뜀")
            return 0
        QUIET["window"] = True
        QUIET["on"] = not first          # 보류·실패 알림은 창의 첫 실행에서만
        QUIET["last"] = in_window(a.until, a.until)[0] or (
            dt.datetime.now(KST) >= dt.datetime.combine(dt.datetime.now(KST).date(), _hm(a.until), KST) - dt.timedelta(minutes=20))
    try:
        return run_post(a.dry_run, a.force)
    except Exception as e:               # 딜보드 워크플로를 깨뜨리지 않는다
        print(f"[스레드] 오류: {type(e).__name__}: {e}")
        save_status("오류", f"{type(e).__name__}: {str(e)[:300]}", None)
        if not QUIET["on"]:
            tg(f"❌ 스레드 자동 게시 오류: {type(e).__name__}: {str(e)[:200]}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
