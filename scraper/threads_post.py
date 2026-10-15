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


def today_kst() -> dt.date:
    return dt.datetime.now(KST).date()


def first_line(s: str) -> str:
    for ln in (s or "").splitlines():
        if ln.strip():
            return re.sub(r"\s+", " ", ln.strip())
    return ""


def tg(msg: str) -> None:
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    print("[알림]", msg.replace("\n", " | "))
    if not (tok and chat):
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": msg,
                                   "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=15)
    except Exception as e:
        print("  텔레그램 전송 실패:", e)


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


def run_post(dry: bool, force: bool) -> int:
    token, uid = os.environ.get("THREADS_ACCESS_TOKEN"), os.environ.get("THREADS_USER_ID")
    items, today = load_schedule(), today_kst()
    if not (token and uid):
        if not dry:
            print("[!] THREADS_ACCESS_TOKEN / THREADS_USER_ID 가 없습니다. 게시를 건너뜁니다.")
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
    for it in blocked:
        tg(f"⚠️ 스레드 예약 글 보류: {label(it)} ({it['date']})\n"
           f"본문이나 첫 답글에 [링크] 빈칸이 남아 있어요. data/threads_schedule.json 을 채워 커밋해 주세요.")

    # 같은 날 두 번 올리지 않기 (수동 실행 + 정기 실행이 겹쳐도 안전)
    if not force and posted:
        sched_lines = {first_line(it["text"]) for it in items}
        for fl, p in posted.items():
            ts = p.get("timestamp", "")
            try:
                d = dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z").astimezone(KST).date()
            except ValueError:
                continue
            if d == today and fl in sched_lines:
                print("오늘 이미 예약 글 하나를 올렸습니다. 내일 다시 실행됩니다.")
                return 0

    if not ready:
        print("올릴 글이 없습니다.")
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
        print("\n(--dry-run: 실제로 올리지 않았습니다)")
        return 0

    if img and not image_ok(img):
        tg(f"❌ 스레드 게시 중단: {label(it)}\n이미지 주소를 열 수 없어요: {img}\n"
           f"threads/ 폴더 이미지가 커밋·배포됐는지 확인해 주세요.")
        return 1

    try:
        pid = T.publish_post(uid, token, it["text"], img)
    except T.ThreadsError as e:
        tg(f"❌ 스레드 게시 실패: {label(it)}\n{e}")
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
    tg(f"✅ 스레드 게시 완료: {label(it)}\n{link}\n{reply_note}{nxt_txt}")
    return 0


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
    a = ap.parse_args()
    return run_refresh() if a.refresh else run_post(a.dry_run, a.force)


if __name__ == "__main__":
    sys.exit(main())
