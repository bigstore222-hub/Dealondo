"""
tg_inbox.py — 텔레그램으로 상품 링크를 보내면 스레드 글을 써서 미리보기를 보내고,
               [✅ 스레드에 올리기]를 누르면 바로 @deal_ondo 에 게시한다.

사용법 (딜온도 텔레그램 봇과의 대화창에서)
  · 상품 페이지 주소를 보낸다                      → 글·카드 미리보기가 온다
  · 딜온도 알림 메시지를 전달(forward)하거나,
    알림에 답장으로 아무 말이나 보낸다             → 그 알림의 상품으로 미리보기
  · 미리보기에서 ✅ 누르면 게시, ❌ 누르면 취소
  · 미리보기 글에 답장으로
      "19.99 495"   → 판매가·정가를 고쳐서 다시 미리보기
      그 밖의 글     → 그 글을 본문으로 바꿔서 다시 미리보기

가격을 읽는 순서
  1) 딜온도 딜보드(deals.json)에 잡힌 상품 → 한국 도착가·국내 최저가까지 그대로
  2) 보낸 메시지(전달한 알림)에 적힌 가격 "$19.99 $495"
  3) 상품 페이지(Shopify 공개 JSON, 페이지 속 구조화 데이터)
  셋 다 안 되면 "가격을 답장으로 알려주세요"라고 묻는다(그 답장 한 줄로 미리보기가 온다).

실행
  python tg_inbox.py --peek               새 메시지가 있는지만 확인 (GitHub 출력 has_work)
  python tg_inbox.py --serve --minutes 9  메시지 처리. 승인 대기 중이면 그동안 계속 기다린다
  python tg_inbox.py --serve --dry-run    스레드에 올리지 않고 미리보기까지만

보안: TELEGRAM_CHAT_ID(딜온도 알림을 받는 본인 대화) 또는 TELEGRAM_OWNER_ID 에서 온 것만 처리한다.
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request, uuid
from pathlib import Path

import threads_api as T
import threads_post as TP
import threads_deal as TD
import product_page as PP

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "threads" / "inbox.json"
KST = dt.timezone(dt.timedelta(hours=9))
DRAFT_TTL_H = 48
IDLE_SEC = 75            # 승인 대기 중인 미리보기가 없으면 이만큼 조용할 때 종료

TOKEN = re.sub(r"\s", "", os.environ.get("TELEGRAM_BOT_TOKEN") or "")
ALLOWED = {re.sub(r"\s", "", os.environ.get(k) or "") for k in ("TELEGRAM_CHAT_ID", "TELEGRAM_OWNER_ID")} - {""}
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
PRICE_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{1,2})?)")
NUMS_ONLY = re.compile(r"^\s*\$?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:[/→>\-~ ,]+\s*\$?\s*([\d,]+(?:\.\d{1,2})?))?\s*$")
HELP = ("딜온도 스레드 접수함이에요.\n\n"
        "· 상품 페이지 주소를 보내면 스레드 글·카드 미리보기를 보내드려요.\n"
        "· 딜온도 알림을 전달하거나, 알림에 답장으로 아무 말이나 보내도 돼요.\n"
        "· 미리보기에서 ✅ 누르면 바로 게시, ❌ 누르면 취소.\n"
        "· 미리보기에 답장으로 \"19.99 495\" → 판매가·정가 수정, 다른 글 → 본문 교체.")


# ── 텔레그램 ────────────────────────────────────────────────────────────────
class TgError(RuntimeError):
    pass


def tg(method: str, timeout: int = 20, **params):
    body = json.dumps({k: v for k, v in params.items() if v is not None}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}", body,
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read())
    except urllib.error.HTTPError as e:
        j = json.loads(e.read() or b"{}")
    if not j.get("ok"):
        raise TgError(f"{method}: {j.get('error_code')} {j.get('description')}")
    return j["result"]


def tg_safe(method: str, **params):
    try:
        return tg(method, **params)
    except Exception as e:
        print("  [텔레그램]", e)
        return None


def send_photo(chat: str, path: Path, caption: str = "", reply_to: int | None = None):
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in (("chat_id", chat), ("caption", caption), ("reply_to_message_id", reply_to)):
        if v not in (None, ""):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="{path.name}"\r\n'
                 f'Content-Type: image/png\r\n\r\n'.encode() + path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendPhoto", b"".join(parts),
                                 {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["result"]


def _get_updates(offset: int | None, wait: int) -> list:
    body = {"timeout": wait, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        body["offset"] = offset
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/getUpdates", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=wait + 20) as r:
            j = json.loads(r.read())
    except urllib.error.HTTPError as e:
        j = json.loads(e.read() or b"{}")
    if not j.get("ok"):
        # 409 = 웹훅이 걸려 있거나 다른 곳에서 동시에 받는 중
        raise TgError(f"getUpdates: {j.get('error_code')} {j.get('description')}")
    return j["result"]


def chat_of(u: dict) -> str:
    if "callback_query" in u:
        return str(((u["callback_query"].get("message") or {}).get("chat") or {}).get("id", ""))
    return str(((u.get("message") or {}).get("chat") or {}).get("id", ""))


# ── 상태 파일 (threads/inbox.json — 워크플로가 커밋해 다음 실행으로 넘긴다) ────────────
def load_state() -> dict:
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        st = {}
    st.setdefault("drafts", {})
    st.setdefault("done_updates", [])
    cut = dt.datetime.now(KST) - dt.timedelta(hours=DRAFT_TTL_H)
    st["drafts"] = {k: v for k, v in st["drafts"].items() if dt.datetime.fromisoformat(v["created"]) > cut}
    st["done_updates"] = st["done_updates"][-200:]
    return st


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


# ── 메시지에서 상품 정보 뽑기 ──────────────────────────────────────────────────
def _u16(text: str, off: int, ln: int) -> str:
    b = text.encode("utf-16-le")
    return b[off * 2:(off + ln) * 2].decode("utf-16-le", "ignore")


def urls_in(msg: dict) -> list[str]:
    text = msg.get("text") or msg.get("caption") or ""
    out = []
    for e in (msg.get("entities") or msg.get("caption_entities") or []):
        if e.get("type") == "text_link" and e.get("url"):
            out.append(e["url"])
        elif e.get("type") == "url":
            out.append(_u16(text, e["offset"], e["length"]))
    out += URL_RE.findall(text)
    seen, res = set(), []
    for u in out:
        u = u.rstrip(".,")
        if not u.lower().startswith("http"):
            u = "https://" + u
        if u not in seen and "t.me/" not in u and "threads.com" not in u:
            seen.add(u); res.append(u)
    return res


def parse_alert(msg: dict) -> dict:
    """딜온도 알림(또는 아무 메시지)에 적힌 상품명·가격을 읽는다."""
    text = msg.get("text") or msg.get("caption") or ""
    out: dict = {}
    at = (msg.get("forward_origin") or {}).get("date") or msg.get("forward_date") or msg.get("date")
    if at:
        out["at"] = at
    nums = [PP._num(x) for x in PRICE_RE.findall(text)]
    nums = [x for x in nums if x]
    if nums:
        out["price_current"] = nums[0]
        if len(nums) > 1 and nums[1] > nums[0]:
            out["price_list"] = nums[1]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()] if nums else []   # 가격이 적힌 알림일 때만
    for ln in lines:
        if URL_RE.search(ln) or "$" in ln or "점" in ln and "·" in ln or "보러가기" in ln:
            continue
        t = re.sub(r"^[⭐🔷⚠️\s]+", "", ln).strip()
        if len(t) >= 6:
            out["title"] = t
            break
    for e in (msg.get("entities") or []):
        if e.get("type") == "bold":
            b = _u16(text, e["offset"], e["length"]).strip()
            if out.get("title", "").startswith(b) and not re.search(r"HOT|FLASH|STEADY|\$|%", b):
                out["brand"] = b
                break
    return out


def split_brand(title: str) -> tuple[str, str]:
    import brands
    brands._load()
    t = brands._norm(title)
    for term, _tier, canon in brands._TERMS:
        if t.startswith(term + " ") and canon:
            return canon, title[len(term):].lstrip(" -·,").strip()
    return "", title


# ── 초안 만들기 ───────────────────────────────────────────────────────────────
def build_deal(url: str, hint: dict) -> tuple[dict | None, list[str], dt.datetime]:
    """딜 dict(threads_deal 형식), 주의 문구 목록, 확인 시각"""
    notes: list[str] = []
    now = dt.datetime.now(KST)
    d = TD.find_deal(url)
    if d:
        d = dict(d)
        seen = dt.datetime.fromisoformat(d.get("last_seen") or d.get("detected_at")).astimezone(KST)
        live = PP.read(url) if not hint.get("typed") else {}
        if live.get("price_current"):
            if abs(live["price_current"] - d["price_current"]) > 0.01:
                notes.append(f"가격이 바뀌었어요: {TD.money(d['price_current'])} → {TD.money(live['price_current'])} (상품 페이지 기준)")
                d["price_current"] = live["price_current"]
                if live.get("price_list"):
                    d["price_list"] = live["price_list"]
                _recalc(d)
            seen = now
        else:
            age = (now - seen).total_seconds() / 3600
            if age > TD.MAX_AGE_H:
                notes.append(f"딜보드에서 마지막으로 확인된 지 {age:.0f}시간 지났어요. 품절·가격 변동 여부를 확인하세요.")
        if hint.get("typed"):
            d["price_current"] = hint["price_current"]
            d["price_list"] = hint.get("price_list") or d.get("price_list")
            _recalc(d)
            seen = now
            notes.append("직접 알려주신 가격으로 썼어요.")
        return d, notes, seen

    page = {} if hint.get("price_current") and hint.get("title") else PP.read(url)
    title = hint.get("title") or page.get("title") or ""
    brand = hint.get("brand") or page.get("brand") or ""
    if title and not brand:
        brand, rest = split_brand(title)
        if brand:
            title = rest
    elif brand and title.lower().startswith(brand.lower()):
        title = title[len(brand):].lstrip(" -·,").strip()
    price = hint.get("price_current") or page.get("price_current")
    if not price or not title:
        return None, [page.get("error") or "상품명·가격을 찾지 못했어요"], now
    if (page.get("currency") or "USD").upper() != "USD" and not hint.get("price_current"):
        notes.append(f"통화가 {page.get('currency')}예요. 가격을 확인하세요.")
    if page.get("in_stock") is False:
        notes.append("상품 페이지에 품절로 나와요.")
    if hint.get("typed"):
        notes.append("직접 알려주신 가격으로 썼어요.")
    elif hint.get("price_current") and not page:
        notes.append("알림 메시지에 적힌 가격으로 썼어요. 지금 가격인지 확인하세요.")
        if hint.get("at"):
            now = dt.datetime.fromtimestamp(hint["at"], KST)
    if not (hint.get("price_list") or page.get("price_list")):
        notes.append("정가를 찾지 못해 할인율이 빠졌어요. 답장으로 \"판매가 정가\"를 보내면 넣어드려요.")
    dom = PP.domain(url)
    key = re.sub(r"[^a-z0-9]", "", (brand or "").lower())
    store = TD.STORES.get(dom) or (brand if key and key in dom.replace(".", "") else dom.split(".")[0].capitalize())
    d = {"url": url, "source": dom, "store_name": store, "title": title, "brand": brand,
         "category": PP.category_of(f"{brand} {title}"),
         "price_current": price, "price_list": hint.get("price_list") or page.get("price_list"),
         "image": page.get("image")}
    try:
        import affiliate
        d["buy_url"] = affiliate.wrap(url, dom)
    except Exception:
        d["buy_url"] = url
    _recalc(d, kr=True)
    return d, notes, now


def _recalc(d: dict, kr: bool = False) -> None:
    import pricing
    p, w = d.get("price_current"), d.get("price_list")
    d["discount_pct"] = round((1 - p / w) * 100) if p and w and w > p else None
    try:
        d["krw_effective"] = pricing.landed_cost_krw(p, d.get("category") or "etc")
    except Exception:
        pass
    if kr and os.environ.get("NAVER_CLIENT_ID"):
        try:
            v = pricing.kr_lowest_price(d.get("brand") or "", d.get("title") or "")
            # 국내가가 직구가보다 싸면 '국내보다 싸다'는 글이 거짓이 되므로 표시하되 경고한다
            d["kr_lowest_price"] = v
        except Exception:
            pass


def preview_text(dr: dict) -> str:
    d = dr["deal"]
    parts = ["📝 스레드 미리보기", "", dr["text"], "", "── 첫 답글 ──", dr["reply"]]
    warn = list(dr.get("notes") or [])
    kr, krw = d.get("kr_lowest_price"), d.get("krw_effective")
    if kr and krw and kr <= krw:
        warn.append(f"국내 최저가(₩{int(kr):,})가 직구 예상가(₩{int(krw):,})보다 싸요. 올릴지 다시 생각해보세요.")
    if warn:
        parts += ["", *("⚠️ " + w for w in warn)]
    parts += ["", f"주제 태그 #{TD.topic_tag()} · 본문 {len(dr['text'])}/500자 · 답장으로 \"19.99 495\" → 가격 수정, 다른 글 → 본문 교체"]
    return "\n".join(parts)[:4000]


def keyboard(did: str):
    return {"inline_keyboard": [[{"text": "✅ 스레드에 올리기", "callback_data": f"post:{did}"},
                                 {"text": "❌ 취소", "callback_data": f"cancel:{did}"}]]}


def card_path(did: str) -> Path:
    return TD.CARD_DIR / f"{did}.png"


def send_preview(st: dict, did: str, chat: str, reply_to: int | None = None) -> None:
    dr = st["drafts"][did]
    seen = dt.datetime.fromisoformat(dr["seen"])
    TD.CARD_DIR.mkdir(parents=True, exist_ok=True)
    TD.render_card(dr["deal"], seen, card_path(did))
    img = TD.product_image(dr["deal"])
    if img and dr.get("img_ok") is None:
        dr["img_ok"] = TD.image_ok(img)
    if img and dr.get("img_ok"):
        try:
            tg("sendPhoto", chat_id=chat, photo=img, caption="1/2 상품 사진", reply_to_message_id=reply_to)
        except Exception as e:
            dr["img_ok"] = False
            print("  상품 사진 전송 실패:", e)
    n = ("상품 사진을 불러오지 못해 카드 한 장으로 올라가요." if img and not dr.get("img_ok")
         else ("상품 사진을 찾지 못해 카드 한 장으로 올라가요." if not img else ""))
    if n and n not in dr.setdefault("notes", []):
        dr["notes"].append(n)
    try:
        ph = send_photo(chat, card_path(did), "2/2 딜 카드" if dr.get("img_ok") else "딜 카드", reply_to)
        dr["photo_msg"] = ph["message_id"]
    except Exception as e:
        print("  카드 전송 실패:", e)
    m = tg("sendMessage", chat_id=chat, text=preview_text(dr), disable_web_page_preview=True,
           reply_markup=keyboard(did))
    dr["msg"] = m["message_id"]
    dr["status"] = "waiting"


def new_draft(st: dict, chat: str, url: str, hint: dict, reply_to: int) -> str | None:
    did = hashlib.md5(f"{TD.norm(url)}{time.time()}".encode()).hexdigest()[:8]
    d, notes, seen = build_deal(url, hint)
    if not d:
        st["drafts"][did] = {"created": dt.datetime.now(KST).isoformat(timespec="seconds"), "url": url,
                             "hint": hint, "status": "waiting_price"}
        m = tg("sendMessage", chat_id=chat, reply_to_message_id=reply_to, disable_web_page_preview=True,
               text=f"🔎 {notes[0]}\n{url}\n\n이 메시지에 답장으로 판매가와 정가를 보내주세요.\n예) 19.99 495"
                    + ("" if hint.get("title") else "\n상품명도 함께 쓰려면 둘째 줄에 적어주세요."))
        st["drafts"][did]["msg"] = m["message_id"]
        return did
    text, reply = TD.build_text(d, seen)
    st["drafts"][did] = {"created": dt.datetime.now(KST).isoformat(timespec="seconds"), "url": url,
                         "deal": d, "seen": seen.isoformat(), "text": text, "reply": reply, "notes": notes,
                         "already": TD.already_posted(url)}
    if st["drafts"][did]["already"]:
        st["drafts"][did]["notes"].insert(0, "이미 스레드에 올린 적 있는 상품이에요.")
    send_preview(st, did, chat, reply_to)
    return did


def retire(chat: str, dr: dict, note: str) -> None:
    if dr.get("msg"):
        tg_safe("editMessageReplyMarkup", chat_id=chat, message_id=dr["msg"], reply_markup={"inline_keyboard": []})
        tg_safe("sendMessage", chat_id=chat, reply_to_message_id=dr["msg"], text=note)


# ── 게시 ──────────────────────────────────────────────────────────────────────
def publish(st: dict, did: str, chat: str, dry: bool) -> None:
    dr = st["drafts"][did]
    dr["status"] = "posting"
    save_state(st)
    tg_safe("editMessageReplyMarkup", chat_id=chat, message_id=dr["msg"], reply_markup={"inline_keyboard": []})
    prog = tg_safe("sendMessage", chat_id=chat, reply_to_message_id=dr["msg"], text="⏳ 스레드에 올리는 중… (30초~1분)")
    def say(t):
        if prog:
            tg_safe("editMessageText", chat_id=chat, message_id=prog["message_id"], text=t,
                    disable_web_page_preview=False)
        else:
            tg_safe("sendMessage", chat_id=chat, text=t)
    if dry:
        dr["status"] = "dry"
        say("🧪 테스트 실행이라 올리지 않았어요(dry-run).")
        return
    token = re.sub(r"\s", "", os.environ.get("THREADS_ACCESS_TOKEN") or "")
    uid = re.sub(r"\s", "", os.environ.get("THREADS_USER_ID") or "")
    try:
        card = card_path(did)
        if not card.exists():
            TD.CARD_DIR.mkdir(parents=True, exist_ok=True)
            TD.render_card(dr["deal"], dt.datetime.fromisoformat(dr["seen"]), card)
        img = TD.publish_image(card)
        pid, how = TD.post_deal(uid, token, dr["text"], img,
                                TD.product_image(dr["deal"]) if dr.get("img_ok") else None)
        link = T.permalink(pid, token)
        try:
            T.publish_post(uid, token, dr["reply"], reply_to=pid)
            note = f"{how} · 첫 답글(링크) 완료"
        except T.ThreadsError as e:
            note = f"{how} · 첫 답글 실패 ({e})"
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)[:300]}"
        dr["status"] = "waiting"
        dr["error"] = msg
        TD.record(dr["url"], "실패", msg)
        say(f"❌ 게시 실패\n{msg}\n\n아래 버튼으로 다시 시도할 수 있어요.")
        tg_safe("editMessageReplyMarkup", chat_id=chat, message_id=dr["msg"], reply_markup=keyboard(did))
        return
    dr["status"] = "posted"
    dr["link"] = link
    TD.record(dr["url"], "게시 완료", note, link)
    say(f"✅ 스레드에 올렸어요\n{link}\n{note}")


# ── 업데이트 처리 ────────────────────────────────────────────────────────────
def handle(st: dict, u: dict, dry: bool) -> None:
    chat = chat_of(u)
    if chat not in ALLOWED:
        print("  허용되지 않은 대화 — 무시:", chat)
        return
    if "callback_query" in u:
        cq = u["callback_query"]
        act, _, did = (cq.get("data") or "").partition(":")
        dr = st["drafts"].get(did)
        if not dr or dr.get("msg") != (cq.get("message") or {}).get("message_id"):
            tg_safe("answerCallbackQuery", callback_query_id=cq["id"], text="만료됐거나 새 미리보기로 바뀐 글이에요.")
            return
        if act == "cancel":
            tg_safe("answerCallbackQuery", callback_query_id=cq["id"], text="취소했어요")
            dr["status"] = "canceled"
            tg_safe("editMessageReplyMarkup", chat_id=chat, message_id=dr["msg"], reply_markup={"inline_keyboard": []})
            tg_safe("sendMessage", chat_id=chat, reply_to_message_id=dr["msg"], text="❌ 취소했어요. 올리지 않아요.")
            return
        if act == "post":
            if dr.get("status") in ("posted", "posting"):
                tg_safe("answerCallbackQuery", callback_query_id=cq["id"], text="이미 처리한 글이에요.")
                return
            tg_safe("answerCallbackQuery", callback_query_id=cq["id"], text="올리는 중…")
            publish(st, did, chat, dry)
        return

    msg = u.get("message") or {}
    text = (msg.get("text") or msg.get("caption") or "").strip()
    rt = msg.get("reply_to_message") or {}
    # 1) 미리보기·가격 질문에 대한 답장
    if rt:
        did = next((k for k, v in st["drafts"].items() if v.get("msg") == rt.get("message_id")
                    or v.get("photo_msg") == rt.get("message_id")), None)
        if did:
            dr = st["drafts"][did]
            if dr.get("status") in ("posted", "posting"):
                tg_safe("sendMessage", chat_id=chat, reply_to_message_id=msg["message_id"],
                        text="이미 올린 글이에요. 새로 올리려면 상품 링크를 다시 보내주세요.")
                return
            first, _, rest = text.partition("\n")
            m = NUMS_ONLY.match(first)
            if m:
                a, b = PP._num(m.group(1)), PP._num(m.group(2)) if m.group(2) else None
                hint = dict(dr.get("hint") or {})
                hint.update({"price_current": min(a, b) if b else a, "price_list": max(a, b) if b else None,
                             "typed": True})
                if rest.strip():
                    hint["title"] = rest.strip().splitlines()[0]
                if dr.get("status") != "waiting_price":
                    retire(chat, dr, "🔁 가격을 고친 새 미리보기를 보낼게요.")
                dr["status"] = "replaced"
                new_draft(st, chat, dr["url"], hint, msg["message_id"])
                return
            if dr.get("status") == "waiting_price":
                tg_safe("sendMessage", chat_id=chat, reply_to_message_id=msg["message_id"],
                        text="숫자 두 개로 보내주세요. 예) 19.99 495")
                return
            if len(text) > 500:
                tg_safe("sendMessage", chat_id=chat, reply_to_message_id=msg["message_id"],
                        text=f"스레드 글은 500자까지예요(지금 {len(text)}자). 줄여서 다시 답장해주세요.")
                return
            retire(chat, dr, "🔁 고친 본문으로 새 미리보기를 보낼게요.")
            dr["status"] = "replaced"
            nid = hashlib.md5(f"{did}{time.time()}".encode()).hexdigest()[:8]
            st["drafts"][nid] = {**{k: v for k, v in dr.items() if k not in ("msg", "photo_msg", "status")},
                                 "text": text, "created": dt.datetime.now(KST).isoformat(timespec="seconds")}
            send_preview(st, nid, chat, msg["message_id"])
            return

    # 2) 상품 링크 (직접 보낸 것, 전달한 알림, 알림에 단 답장)
    src = msg
    links = urls_in(msg)
    if not links and rt:
        src, links = rt, urls_in(rt)
    if not links:
        tg_safe("sendMessage", chat_id=chat, text=HELP)
        return
    hint = parse_alert(src)
    if src is msg and not hint.get("price_current"):
        # "링크 19.99 495" 처럼 $ 없이 숫자만 붙여 보낸 경우
        m = NUMS_ONLY.match(URL_RE.sub(" ", text).strip())
        if m:
            a, b = PP._num(m.group(1)), PP._num(m.group(2)) if m.group(2) else None
            hint.update({"price_current": min(a, b) if b else a, "price_list": max(a, b) if b else None,
                         "typed": True})
    for url in links[:3]:
        try:
            new_draft(st, chat, url, hint if len(links) == 1 else {}, msg["message_id"])
        except Exception as e:
            print("  초안 실패:", repr(e))
            tg_safe("sendMessage", chat_id=chat, reply_to_message_id=msg["message_id"],
                    text=f"⚠️ 미리보기를 만들지 못했어요: {type(e).__name__}: {str(e)[:200]}\n{url}")


def relevant(u: dict) -> bool:
    return chat_of(u) in ALLOWED


def peek() -> int:
    has = False
    try:
        ups = _get_updates(None, 0)
        has = any(relevant(u) for u in ups)
        if ups and not has:      # 남의 대화 등 처리할 필요 없는 것만 있으면 확인 처리해 비운다
            _get_updates(ups[-1]["update_id"] + 1, 0)
        print(f"새 업데이트 {len(ups)}건 · 처리할 것 {'있음' if has else '없음'}")
    except Exception as e:
        print("::warning::텔레그램 확인 실패:", e)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"has_work={'true' if has else 'false'}\n")
    return 0


def serve(minutes: float, dry: bool) -> int:
    if not (TOKEN and ALLOWED):
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 없습니다"); return 1
    st = load_state()
    deadline = time.time() + minutes * 60
    last = time.time()
    offset = None
    mine: set[str] = set()
    while True:
        left = deadline - time.time()
        waiting = [k for k in mine if st["drafts"].get(k, {}).get("status") in ("waiting", "waiting_price")]
        if left < 8 or (not waiting and time.time() - last > IDLE_SEC):
            break
        try:
            ups = _get_updates(offset, int(min(50, left - 6)))
        except TgError as e:
            print("::error::", e); break
        except Exception as e:
            print("  getUpdates 재시도:", e); time.sleep(3); continue
        for u in ups:
            offset = u["update_id"] + 1
            if u["update_id"] in st["done_updates"]:
                continue
            before = set(st["drafts"])
            try:
                handle(st, u, dry)
            except Exception as e:
                print("  처리 실패:", repr(e))
            mine |= set(st["drafts"]) - before
            st["done_updates"].append(u["update_id"])
            save_state(st)
            last = time.time()
    if offset is not None:
        try:
            _get_updates(offset, 0)      # 처리한 것까지 확인 처리
        except Exception:
            pass
    left_waiting = [k for k in mine if st["drafts"].get(k, {}).get("status") == "waiting"]
    if left_waiting:
        print(f"승인 대기 {len(left_waiting)}건 — 다음 실행에서 ✅ 를 받으면 게시합니다")
    save_state(st)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--peek", action="store_true")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--minutes", type=float, default=9)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.peek:
        return peek()
    return serve(a.minutes, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
