"""
threads_deal.py — 타임딜 즉시 게시 (스레드 @deal_ondo)

텔레그램으로 받은 딜의 상품 링크 하나만 넣으면:
  1) 딜보드(deals.json)에서 그 상품을 찾아 가격·할인·한국 도착가를 가져온다
     (딜보드는 수집 실행 때마다 갱신되므로, 마지막으로 확인된 시각이 오래됐으면 멈춘다)
  2) 딜 카드 이미지(1080×1350)를 만들어 리포에 커밋 → 공개 주소(raw.githubusercontent.com)
  3) 글 + 첫 답글(상품 링크)을 스레드에 바로 게시
  4) 결과를 텔레그램으로 알리고 threads_status.json 에 기록

실행: GitHub → Actions → "Dealondo 타임딜 게시" → Run workflow → 상품 링크 붙여넣기
로컬: python threads_deal.py --url <상품링크> [--dry-run] [--force]
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, html, json, os, re, subprocess, sys, time, urllib.request
from pathlib import Path

import threads_api as T
import threads_post as TP

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "deals.json"
CARD_DIR = ROOT / "threads" / "deals"
TEMPLATE = Path(__file__).with_name("deal_card.html")
KST = dt.timezone(dt.timedelta(hours=9))
REPO = os.environ.get("GITHUB_REPOSITORY", "bigstore222-hub/Dealondo")
MAX_AGE_H = float(os.environ.get("DEAL_MAX_AGE_HOURS", "6"))

STORES = {"nordstromrack.com": "Nordstrom Rack", "nordstrom.com": "Nordstrom", "zappos.com": "Zappos",
          "6pm.com": "6pm", "amazon.com": "Amazon", "woot.com": "Woot", "bestbuy.com": "Best Buy",
          "macys.com": "Macy's", "ebay.com": "eBay", "walmart.com": "Walmart", "target.com": "Target",
          "carhartt.com": "Carhartt", "adidas.com": "adidas", "nike.com": "Nike",
          "saksoff5th.com": "Saks OFF 5TH", "bloomingdales.com": "Bloomingdale's"}
# 질문은 상품 종류에 맞춰 고른다(어느 글에나 붙는 질문은 자동 게시물처럼 보여 답글이 안 달린다)
QUESTIONS = {
    "clothes": ["이 가격이면 사이즈 모험해볼 만할까요?", "이 가격이면 한 사이즈 크게 사두시겠어요?"],
    "shoes": ["신발은 사이즈 실패가 무서운데, 이 가격이면 도전하시겠어요?", "이 가격이면 반 사이즈 고민 없이 사시겠어요?"],
    "bags": ["이 가격이면 선물용으로 하나 더 사두시겠어요?", "이 가격이면 데일리로 막 들어도 괜찮겠죠?"],
    "electronics": ["국내 가격이랑 비교하면 어떠세요?", "이 가격이면 지금 바꾸시겠어요?"],
    "etc": ["이 가격이면 장바구니 담으시겠어요?", "이 가격, 직구해볼 만할까요?"],
}
BF_WINDOW = ((11, 20), (12, 2))     # 이 기간엔 주제 태그를 '블랙프라이데이'로


def norm(u: str) -> str:
    u = (u or "").strip().split("#")[0].split("?")[0].rstrip("/").lower()
    return re.sub(r"^https?://(www\.)?", "", u)


def find_deal(url: str) -> dict | None:
    data = json.loads(BOARD.read_text(encoding="utf-8"))
    want = norm(url)
    ids = re.findall(r"\d{5,}", want)
    for d in data.get("deals", []):
        for key in ("url", "buy_url"):
            have = norm(d.get(key, ""))
            if have and (have == want or (ids and ids[-1] in have and have.split("/")[0] == want.split("/")[0])):
                return d
    return None


def store_name(d: dict) -> str:
    if d.get("store_name"):
        return d["store_name"]
    src = (d.get("source") or "").split(":")[-1]
    return STORES.get(src, src or "미국 쇼핑몰")


def money(v: float) -> str:
    return f"${v:,.2f}".replace(".00", "")


def duty_line(d: dict) -> str:
    p = d.get("price_current") or 0
    # 무선 통신 기능이 있는 전자기기만 미국발이어도 $150 (목록통관 배제). SSD 등 나머지는 $200.
    import product_page
    limit = 150 if product_page.WIRELESS.search(f"{d.get('brand') or ''} {d.get('title') or ''}") else 200
    if p and p <= limit:
        return f"${limit} 이하라 관부가세 없음 (미국발 기준)"
    krw = d.get("krw_effective")
    return f"관부가세 포함 예상 약 ₩{krw:,}" if krw else "면세한도 초과 — 관부가세 확인 필요"


def topic_tag(now: dt.datetime | None = None) -> str | None:
    """
    주제 태그. 10/22 기본 태그를 켠 뒤 조회수가 떨어져 기본은 '태그 없음'.
    THREADS_TOPIC_TAG=auto 면 평소 #해외직구 · 블프 기간 #블랙프라이데이, 다른 값이면 그 태그.
    """
    v = (os.environ.get("THREADS_TOPIC_TAG") or "").strip()
    if not v:
        return None
    if v != "auto":
        return v
    n = now or dt.datetime.now(KST)
    (m1, d1), (m2, d2) = BF_WINDOW
    return "블랙프라이데이" if (m1, d1) <= (n.month, n.day) <= (m2, d2) else "해외직구"


def build_text(d: dict, seen_kst: dt.datetime) -> tuple[str, str]:
    """
    첫 줄 = 한국어로 '무엇인지' + 가격 변화 (피드에서 멈추게 하는 줄)
    본문은 짧게, 세부(관부가세·확인 시각)는 카드 이미지에 맡긴다.
    """
    import kr_names as K
    now, was, pct = d["price_current"], d.get("price_list"), d.get("discount_pct")
    brand, title = (d.get("brand") or "").strip(), (d.get("title") or "").strip()
    head = K.headline_name(brand, title)
    g = K.gender(title, d.get("url") or "", d.get("gender") or "")
    lines = [f"{head}, {money(was)} → {money(now)}" if was else f"{head} {money(now)}", ""]
    lines.append(f"{store_name(d)}에서 {round(pct)}% 할인 중이에요." if pct else f"{store_name(d)} 판매가예요.")
    if g:
        lines[-1] += f" ({g})"
    duty = duty_line(d)
    if "없음" in duty:
        lim = "150" if "$150" in duty else "200"
        lines.append(f"${lim} 이하라 관부가세 없고, 배대지 배송비만 더하면 돼요.")
    else:
        lines.append(duty.replace("관부가세 포함 예상", "관부가세 포함하면") + ", 배대지 배송비는 별도예요.")
    kr, krw = d.get("kr_lowest_price"), d.get("krw_effective")
    if kr and krw and kr > krw:
        lines.append(f"국내 최저가는 ₩{int(kr):,}(네이버 쇼핑)이에요.")
    if d.get("coupon_code"):
        lines.append(f"코드 {d['coupon_code']} 적용 기준이에요.")
    lines.append("")
    lines.append("이 할인 폭이면 사이즈가 몇 시간 안에 빠져요." if pct and pct >= 60 and K.kind(title) in ("clothes", "shoes")
                 else ("할인 폭이 큰 딜이라 재고가 금방 빠질 수 있어요." if pct and pct >= 60 else "가격·재고는 금방 바뀌어요."))
    qs = QUESTIONS[K.kind(title)]
    lines.append(qs[int(hashlib.md5(title.encode()).hexdigest(), 16) % len(qs)])
    text = "\n".join(lines)
    reply = (f"상품 링크: {d.get('buy_url') or d['url']}\n"
             "이런 딜을 매일 골라 올려요. 팔로우해두면 놓치지 않아요.")
    if d.get("buy_url") and d["buy_url"] != d["url"]:
        reply = "이 링크는 제휴 링크로, 구매 시 일정액의 수수료를 제공받습니다.\n" + reply
    return text[:500], reply[:500]


def product_image(d: dict) -> str | None:
    """딜보드의 상품 사진 주소. 썸네일 크기로 저장된 경우 피드에 맞는 크기로 바꾼다."""
    u = (d.get("image") or "").strip()
    if not u.startswith("http"):
        return None
    if "nordstrommedia.com" in u:          # 노드스트롬·랙: 480px 썸네일 → 800×1200
        u = u.split("?")[0] + "?h=1200&w=800&dpr=1"
    return u


def image_ok(url: str) -> bool:
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-1023"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status in (200, 206) and (r.headers.get("Content-Type") or "").startswith("image/")
    except Exception:
        return False


def post_deal(uid: str, token: str, text: str, card_url: str, product_img: str | None) -> tuple[str, str]:
    """
    상품 사진 + 딜 카드를 넘겨보는 이미지로 게시(주제 태그 포함).
    상품 사진을 쓸 수 없거나 캐러셀이 거절되면 카드 한 장으로 올린다. → (게시물 ID, 메모)
    """
    tag = topic_tag()
    if product_img and image_ok(product_img):
        try:
            return T.publish_post(uid, token, text, topic_tag=tag, images=[product_img, card_url]), \
                "사진+카드" + (f" · #{tag}" if tag else "")
        except T.ThreadsError as e:
            print("  [타임딜] 캐러셀 실패 → 카드 한 장으로:", e)
    return T.publish_post(uid, token, text, card_url, topic_tag=tag), "카드" + (f" · #{tag}" if tag else "")


def render_card(d: dict, seen_kst: dt.datetime, out: Path) -> None:
    esc = lambda s: html.escape(str(s or ""))
    rows = [("관부가세", duty_line(d).split(" (")[0].replace("이하라 관부가세 없음", "이하 · 없음")),
            ("배대지 배송비", "별도"), ("확인 시각", f"{seen_kst:%m/%d %H:%M}")]
    if d.get("kr_lowest_price"):
        rows.insert(1, ("국내 최저가", f"₩{int(d['kr_lowest_price']):,}"))
    h = TEMPLATE.read_text(encoding="utf-8")
    h = (h.replace("{{KICKER}}", esc(f"오늘의 딜 · {store_name(d).upper()}"))
          .replace("{{BRAND}}", esc(d.get("brand") or ""))
          .replace("{{NAME}}", esc(d.get("title") or ""))
          .replace("{{NOW}}", esc(money(d["price_current"])))
          .replace("{{WAS}}", esc(money(d["price_list"])) if d.get("price_list") else "")
          .replace("{{OFF}}", esc(f"{round(d['discount_pct'])}% OFF") if d.get("discount_pct") else "")
          .replace("{{ROWS}}", "".join(f'<div class="row"><span>{esc(a)}</span><b>{esc(b)}</b></div>' for a, b in rows)))
    tmp = out.with_suffix(".html")
    tmp.write_text(h, encoding="utf-8")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1080, "height": 1350})
        pg.goto(tmp.resolve().as_uri())
        pg.wait_for_timeout(500)
        pg.screenshot(path=str(out))
        b.close()
    tmp.unlink(missing_ok=True)


def publish_image(path: Path) -> str:
    """이미지를 커밋·푸시하고, 바로 열리는 raw 주소를 돌려준다(Pages 배포를 기다리지 않음)."""
    rel = path.relative_to(ROOT).as_posix()
    subprocess.run(["bash", str(ROOT / ".github/scripts/push_files.sh"),
                    f"타임딜 카드 {path.stem}", rel], cwd=ROOT, check=False)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    url = f"https://raw.githubusercontent.com/{REPO}/{sha}/{rel}"
    for _ in range(10):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=10) as r:
                if r.status == 200:
                    return url
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError(f"카드 이미지 주소가 열리지 않음: {url}")


def already_posted(url: str) -> bool:
    try:
        st = json.loads(TP.STATUS_JSON.read_text(encoding="utf-8"))
        return norm(url) in {norm(x.get("url", "")) for x in st.get("deals", [])
                             if x.get("result") == "게시 완료"}
    except Exception:
        return False


def record(url: str, result: str, detail: str, link: str = "") -> None:
    try:
        st = json.loads(TP.STATUS_JSON.read_text(encoding="utf-8"))
    except Exception:
        st = {}
    st.setdefault("deals", []).insert(0, {"at": dt.datetime.now(KST).isoformat(timespec="seconds"),
                                          "url": url, "result": result, "detail": detail, "link": link})
    st["deals"] = st["deals"][:50]
    TP.STATUS_JSON.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="확인 시각이 오래됐거나 이미 올린 딜이어도 게시")
    a = ap.parse_args()
    url = a.url.strip()

    d = find_deal(url)
    if not d:
        msg = "딜보드에 없는 상품이에요. 딜온도가 잡은 딜의 링크를 넣어주세요(가격을 확인할 수 없어 올리지 않았어요)."
        print("[타임딜]", msg); TP.tg(f"⚠️ 타임딜 게시 중단\n{msg}\n{url}"); record(url, "중단", msg)
        return 0
    seen = dt.datetime.fromisoformat(d.get("last_seen") or d.get("detected_at")).astimezone(KST)
    age_h = (dt.datetime.now(KST) - seen).total_seconds() / 3600
    if age_h > MAX_AGE_H and not a.force:
        msg = (f"마지막으로 확인된 지 {age_h:.1f}시간 지났어요(기준 {MAX_AGE_H:g}시간). 이미 품절됐을 수 있어 올리지 않았어요. "
               f"상품 페이지에서 확인 후 force 를 켜고 다시 실행하세요.")
        print("[타임딜]", msg); TP.tg(f"⚠️ 타임딜 게시 중단: {d.get('title')}\n{msg}"); record(url, "중단", msg)
        return 0
    if already_posted(url) and not a.force:
        msg = "이미 스레드에 올린 딜이에요."
        print("[타임딜]", msg); TP.tg(f"ℹ️ 타임딜: {d.get('title')}\n{msg}")
        return 0

    text, reply = build_text(d, seen)
    print("본문 ─────────\n" + text + "\n첫 답글 ───────\n" + reply)
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    card = CARD_DIR / (f"{seen:%Y%m%d}-" + hashlib.md5(norm(url).encode()).hexdigest()[:8] + ".png")
    render_card(d, seen, card)
    print("카드:", card)
    if a.dry_run:
        print("(--dry-run: 올리지 않았습니다)")
        return 0

    token = re.sub(r"\s", "", os.environ.get("THREADS_ACCESS_TOKEN") or "")
    uid = re.sub(r"\s", "", os.environ.get("THREADS_USER_ID") or "")
    try:
        img = publish_image(card)
        pid, how = post_deal(uid, token, text, img, product_image(d))
        link = T.permalink(pid, token)
        try:
            T.publish_post(uid, token, reply, reply_to=pid)
            note = f"{how} · 첫 답글 완료"
        except T.ThreadsError as e:
            note = f"{how} · 첫 답글 실패 ({e})"
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)[:250]}"
        print("[타임딜] 실패:", msg); TP.tg(f"❌ 타임딜 게시 실패: {d.get('title')}\n{msg}"); record(url, "실패", msg)
        return 1
    record(url, "게시 완료", note, link)
    TP.tg(f"✅ 타임딜 게시 완료: {d.get('brand','')} {d.get('title','')}\n{link}\n{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
