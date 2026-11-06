"""
deal_writer.py — 딜 하나를 '상품 특성에 맞는 각도'로 소개하는 스레드 글 작성기 (규칙 기반, 비용 0)

왜 바꿨나 (11/6 실측)
  템플릿 하나로 쓴 딜 글 7개 조회수 0~3, 같은 기간 정보성 글은 0~170.
  매번 같은 틀(브랜드 품목 → N% 할인 → 관부가세 없음 → 사이즈 빠져요 → 질문)이라
  읽는 사람이 첫 줄만 보고 넘기고, 딜보드 정보를 옮긴 것뿐이라 '왜 사야 하는지'가 없었다.

원칙
  · 숫자(가격·할인율·원화·면세한도·국내가)는 전부 코드가 계산한다. 문장은 숫자를 '설명'만 한다.
  · 상품마다 소개 각도를 고른다: 속도 / 코드 / 기록 / 국내비교 / 브랜드 / 시즌 / 사이즈 / 실용 / 기본
  · 최근 쓴 각도·첫 문장은 피해서 연달아 같은 느낌이 나지 않게 한다.
  · 근거가 부족한 딜(가격이 안 내렸거나, 무슨 상품인지 모르겠거나)은 problems 로 돌려 게시 전에 막는다.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import html
import re

import kr_names as K

KST = dt.timezone(dt.timedelta(hours=9))
WINTER = re.compile(r"\b(coat|jacket|puffer|parka|down|fleece|sweater|cardigan|hoodie|sweatshirt|beanie|scarf|gloves|"
                    r"boot|boots|bootie|shearling|wool|cashmere|thermal|insulated|vest|ugg|slipper)\b", re.I)
WIRELESS = re.compile(r"\b(bluetooth|wireless|earbuds?|airpods|headphones?|smart ?watch|apple watch|galaxy watch|"
                      r"pixel watch|fitbit|garmin|tws|earphones?|wi-?fi|ipad|tablet|kindle|macbook|laptop|speaker)\b", re.I)
STORES = {"nordstromrack.com": "Nordstrom Rack", "nordstrom.com": "Nordstrom", "zappos.com": "Zappos", "6pm.com": "6pm",
          "amazon.com": "Amazon", "woot.com": "Woot", "bestbuy.com": "Best Buy", "macys.com": "Macy's", "ebay.com": "eBay",
          "walmart.com": "Walmart", "target.com": "Target", "saksoff5th.com": "Saks OFF 5TH", "ashford.com": "Ashford",
          "bloomingdales.com": "Bloomingdale's", "carhartt.com": "Carhartt", "adidas.com": "adidas", "nike.com": "Nike"}


# ── 숫자·한국어 도우미 ──────────────────────────────────────────────────────
def usd(v: float) -> str:
    return f"${v:,.2f}".replace(".00", "")


def won(v: float) -> str:
    """69420 → '약 6만 9천 원', 1,234,000 → '약 123만 원'"""
    if v is None:
        return ""
    if v >= 1_000_000:
        return f"약 {round(v / 10000):,}만 원"
    k = round(v / 1000)
    man, chun = divmod(k, 10)
    if man == 0:
        return f"약 {chun}천 원"
    return f"약 {man}만 {chun}천 원" if chun else f"약 {man}만 원"


_DIGIT_BATCHIM = {"0": True, "1": True, "2": False, "3": True, "4": False, "5": False, "6": True, "7": True, "8": True, "9": False}


def _has_batchim(word: str) -> bool:
    w = re.sub(r"[\s)\]\"'.,!?]+$", "", word or "")
    if not w:
        return False
    c = w[-1]
    if "가" <= c <= "힣":
        return (ord(c) - 0xAC00) % 28 != 0
    if c.isdigit():
        return _DIGIT_BATCHIM[c]
    return c.lower() in "lmnr"          # 영문 끝소리 대략: -l, -m, -n, -r(얼/엄/언/어ㄹ)


def josa(word: str, pair: str) -> str:
    """josa('블레이저', '이/가') → '블레이저가'"""
    a, b = pair.split("/")
    return f"{word}{a if _has_batchim(word) else b}"


def pick(options: list, seed: str):
    return options[int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(options)]


# ── 사실 정리 (숫자는 여기서만 만든다) ─────────────────────────────────────────
def facts(d: dict, fx: float | None = None, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(KST)
    fx = fx or 1400.0
    brand = html.unescape(d.get("brand") or "").strip()
    title = re.sub(r"\s*[™®©]\s*", " ", html.unescape(d.get("title") or "")).strip()
    title = re.sub(r"\s{2,}", " ", title)
    item = K.item_kr(title if not brand or not title.lower().startswith(brand.lower()) else title[len(brand):])
    bkr = K.brand_kr(brand) if brand else ""
    p, was = d.get("price_current"), d.get("price_list")
    pct = d.get("discount_pct") or ((1 - p / was) * 100 if p and was and was > p else None)
    limit = 150 if WIRELESS.search(f"{brand} {title}") else 200
    hist = [h[1] for h in (d.get("history") or []) if h and h[1]]
    sig = d.get("signals") or {}
    src = str(d.get("source") or "").split(":")[-1]
    f = {
        "brand": bkr, "item": item, "name": K.headline_name(brand, title), "title": title,
        "kind": K.kind(title), "gender": K.gender(title, d.get("url") or "", d.get("gender") or ""),
        "store": d.get("store_name") or STORES.get(src, src.split(".")[0].capitalize() if src else "미국 쇼핑몰"),
        "now": p, "was": was if was and p and was > p else None, "pct": round(pct) if pct else None,
        "save_usd": (was - p) if was and p and was > p else None,
        "krw_now": p * fx if p else None, "krw_was": was * fx if was else None,
        "limit": limit, "free": bool(p and p <= limit), "room": (limit - p) if p else None,
        "krw_eff": d.get("krw_effective"), "kr": d.get("kr_lowest_price"),
        "premium": d.get("brand_tier") == "premium",
        "code": d.get("coupon_code") or "",
        "winter": bool(WINTER.search(title)) and now.month in (9, 10, 11, 12, 1, 2),
        "votes": sig.get("sd_votes"), "age_min": sig.get("sd_age_min"),
        "rising": bool(sig.get("sd_hot_origin") and (sig.get("sd_votes") or 0) >= 5 and (sig.get("sd_age_min") or 999) <= 180),
        "reddit_low": sig.get("reddit_low"),
        "hist_max": max(hist) if hist else None, "hist_days": len(hist),
    }
    f["kr_save"] = round((1 - f["krw_eff"] / f["kr"]) * 100) if f["kr"] and f["krw_eff"] else None
    # '기록 최저'는 실제로 더 비쌌던 기록이 있을 때만 의미가 있다(가격이 그대로였던 상품 제외)
    f["record_low"] = bool(p and f["hist_max"] and f["hist_max"] >= p * 1.05 and min(hist) >= p - 0.01)
    f["reddit_beat"] = bool(p and f["reddit_low"] and p <= f["reddit_low"] * 1.02)
    return f


def problems(f: dict) -> list[str]:
    """게시 전에 막아야 할 문제 — 미리보기에 경고로 띄우고, 추천 딜 자동 선정에서는 제외한다."""
    out = []
    if not f["now"]:
        out.append("가격을 모르는 딜이에요.")
    if f["pct"] is None and not f["code"] and not f["rising"]:
        out.append("정가가 없어 할인 폭을 설명할 수 없어요.")
    if not f["item"] and not f["brand"]:
        out.append("무슨 상품인지 제목만으로 알기 어려워요. 본문을 직접 고쳐주세요.")
    return out


# ── 각도 고르기 ───────────────────────────────────────────────────────────────
ANGLE_ORDER = ["speed", "code", "compare", "record", "brand", "season", "size", "practical", "basic"]


def eligible(f: dict) -> list[str]:
    ok = []
    if f["rising"]:
        ok.append("speed")
    if f["code"]:
        ok.append("code")
    if f["kr_save"] is not None and f["kr_save"] >= 15:
        ok.append("compare")
    if f["record_low"] or f["reddit_beat"]:
        ok.append("record")
    if f["brand"] and (f["premium"] or (f["krw_was"] or 0) >= 200_000) and (f["pct"] or 0) >= 50:
        ok.append("brand")
    if f["winter"] and f["kind"] in ("clothes", "shoes"):
        ok.append("season")
    if f["kind"] in ("clothes", "shoes") and (f["pct"] or 0) >= 40:
        ok.append("size")
    if f["kind"] in ("electronics", "etc", "bags"):
        ok.append("practical")
    ok.append("basic")
    return ok


def choose_angle(f: dict, recent: list[str] | None = None) -> str:
    recent = recent or []
    el = eligible(f)
    for a in el:                       # 강한 근거(speed·code·compare·record)는 최근에 썼어도 우선
        if a in ("speed", "code", "compare") :
            return a
    fresh = [a for a in el if a not in recent[:2]]
    return (fresh or el)[0]


# ── 문장 조각 ────────────────────────────────────────────────────────────────
def _price_line(f: dict, seed: str) -> str:
    if f["was"]:
        return pick([f"정가 {usd(f['was'])} → {usd(f['now'])} ({f['pct']}% 할인)",
                     f"{usd(f['was'])}짜리가 {usd(f['now'])}, {f['pct']}% 할인",
                     f"{f['pct']}% 할인해서 {usd(f['now'])} (정가 {usd(f['was'])})"], seed + "p")
    return f"지금 가격 {usd(f['now'])}"


def _duty_line(f: dict, seed: str) -> str:
    if f["free"]:
        base = pick([f"${f['limit']} 아래라 관부가세 없이 받아요.",
                     f"면세한도 ${f['limit']} 안이라 세금 걱정 없어요.",
                     f"관부가세 없음(미국발 ${f['limit']} 이하)."], seed + "d")
        if f["room"] is not None and f["room"] < 25:
            base += f" 다만 한도까지 {usd(f['room'])}밖에 안 남아서, 다른 걸 같이 담으면 세금이 붙어요."
        return base
    eff = f" 관부가세까지 {won(f['krw_eff'])}." if f["krw_eff"] else ""
    return f"면세한도 ${f['limit']}을 넘어서 관부가세가 붙어요.{eff}"


def _won_line(f: dict, seed: str) -> str:
    return pick([f"원화로 {won(f['krw_now'])}, 여기에 배대지 배송비만 더하면 돼요.",
                 f"환율 계산하면 {won(f['krw_now'])}이에요.",
                 f"한국 돈으로 {won(f['krw_now'])}쯤이에요."], seed + "w")


QUESTIONS = {
    "brand": ["{brand} 입어보신 분, 사이즈 어떻게 나와요?", "{brand}는 이 가격 아니면 손이 안 가던데, 다들 어떠세요?",
              "이 가격이면 선물로도 괜찮겠죠?"],
    "season": ["올겨울 {item} 아직 안 사셨어요?", "겨울 {item}, 이 가격이면 하나 더 있어도 되겠죠?",
               "다들 겨울옷은 언제 사세요?"],
    "size": ["미국 사이즈, 평소보다 반 치수 작게 사세요 크게 사세요?", "직구할 때 사이즈 실패해본 적 있으세요?",
             "사이즈 고르는 본인만의 기준 있으세요?"],
    "practical": ["이 정도면 직구할 만한가요, 국내에서 사는 게 나을까요?", "이런 건 국내가랑 비교해보고 사시죠?",
                  "직구할 때 제일 먼저 보는 게 뭐예요?"],
    "speed": ["이런 딜, 몇 분 만에 결정하세요?", "품절 전에 잡아본 딜 있으세요?"],
    "code": ["결제창 코드 넣는 거 깜빡해본 적 있으세요?", "코드 딜은 놓치면 아깝죠, 다들 챙기세요?"],
    "compare": ["국내가랑 이 정도 차이면 직구하시겠어요?", "직구로 아낀 돈, 제일 크게 아낀 게 얼마였어요?"],
    "record": ["가격 오를 때까지 기다리시나요, 바로 사시나요?", "최저가 찍을 때 사는 편이세요?"],
    "basic": ["이 정도면 장바구니 담으시겠어요?", "이 가격, 직구해볼 만할까요?"],
}


def _q(angle: str, f: dict, seed: str) -> str:
    q = pick(QUESTIONS[angle], seed + "q")
    return q.format(brand=f["brand"] or "이 브랜드", item=f["item"] or "아이템")


# ── 각도별 글 ────────────────────────────────────────────────────────────────
def write(angle: str, f: dict, seed: str) -> str:
    name, item, brand = f["name"], f["item"] or "이 상품", f["brand"]
    g = f" ({f['gender']})" if f["gender"] else ""
    L: list[str] = []
    if angle == "speed":
        L = [f"미국 딜 커뮤니티에 올라온 지 {f['age_min']}분 만에 추천 {f['votes']}개 붙은 딜.", "",
             f"{name}{g} · {f['store']}", _price_line(f, seed), _duty_line(f, seed), "",
             pick(["추천이 이렇게 빨리 붙는 딜은 재고도 빨리 빠지는 편이에요.",
                   "반응이 빠른 딜이라 늦게 보면 품절일 수 있어요."], seed + "s"),
             _q(angle, f, seed)]
    elif angle == "code":
        L = [f"결제할 때 코드 {f['code']} 하나 넣으면 {usd(f['now'])}.", "",
             f"{name}{g} · {f['store']}",
             "코드 없이 사면 이 가격이 안 나와요" + (" (정가 " + usd(f["was"]) + ")" if f["was"] else "") + ".",
             _duty_line(f, seed), "", _q(angle, f, seed)]
    elif angle == "compare":
        L = [pick([f"{name}, 국내 최저가보다 {f['kr_save']}% 싸요.",
                   f"국내에서 {won(f['kr'])} 하는 {name}, 직구하면 {won(f['krw_eff'])}."], seed + "c"), "",
             f"{f['store']} {_price_line(f, seed)}", _duty_line(f, seed),
             "국내가는 네이버 쇼핑 최저가 기준이고, 배대지 배송비는 별도예요.", "", _q(angle, f, seed)]
    elif angle == "record":
        first = (f"{josa(name, '이/가')} 딜온도가 기록한 가격 중 오늘 제일 싸요."
                 if f["record_low"] else
                 f"{name}, 레딧에 올라온 과거 최저가 {usd(f['reddit_low'])}보다 지금이 싸거나 같아요.")
        L = [first, "", f"{f['store']} · {_price_line(f, seed)}",
             f"최근 기록 중 최고가는 {usd(f['hist_max'])}였어요." if f["record_low"] and f["hist_max"] else None,
             _duty_line(f, seed), "", _q(angle, f, seed)]
    elif angle == "brand":
        first = pick([f"정가 {usd(f['was'])}짜리 {name}, 지금 {usd(f['now'])}." if f["was"] else f"{name} {usd(f['now'])}.",
                      f"{josa(brand, '이/가')} 이 가격에 나오는 건 흔치 않아요.",
                      f"{name}{g}, 한국 돈으로 {won(f['krw_now'])}이에요."], seed + "b")
        L = [first, "",
             (f"{name}{g} · " if name not in first else "") + f"{f['store']}에서 {f['pct']}% 할인 중이에요.",
             _won_line(f, seed) if "한국 돈" not in first else None, _duty_line(f, seed), "",
             pick(["할인 폭이 큰 브랜드 딜은 인기 사이즈부터 빠져요.",
                   "이런 가격은 시즌 재고가 정리될 때 잠깐 나와요."], seed + "b2"),
             _q(angle, f, seed)]
    elif angle == "season":
        L = [pick([f"지금 주문하면 본격적인 추위 전에 받는 {item}.",
                   f"겨울 {josa(item, '을/를')} {f['pct']}% 할인에 살 수 있는 타이밍이에요.",
                   f"{name}{g}, 블프 전인데 벌써 {f['pct']}% 할인."], seed + "se"), "",
             f"{f['store']} · {_price_line(f, seed)}", _duty_line(f, seed),
             "배대지를 거쳐도 보통 2주 안팎이면 받아요.", "", _q(angle, f, seed)]
    elif angle == "size":
        L = [pick([f"{item} 직구는 사이즈가 반이에요.",
                   f"{name}{g}, {f['pct']}% 할인이면 사이즈 모험해볼 만해요.",
                   f"{usd(f['now'])}짜리 {name}, 사이즈만 맞으면 이득이에요."], seed + "sz"), "",
             f"{f['store']} · {_price_line(f, seed)}", _duty_line(f, seed),
             pick(["미국 사이즈는 브랜드마다 달라서, 상품 페이지 사이즈표를 꼭 보고 고르세요.",
                   "신발은 US 사이즈를 mm로 바꿔서 확인하세요. 같은 US 8도 브랜드마다 달라요.",
                   "후기에 'runs small/large'가 있는지 먼저 보면 실패가 줄어요."], seed + "sz2"), "",
             _q(angle, f, seed)]
    elif angle == "practical":
        L = [pick([f"{name}, {usd(f['now'])}에 관부가세 {'없음' if f['free'] else '포함 ' + won(f['krw_eff'])}.",
                   f"{name} 살 때 볼 건 두 가지예요. 가격이랑 면세한도.",
                   f"{_price_line(f, seed)}, {name}."], seed + "pr"), "",
             f"{f['store']} 판매가예요.", _duty_line(f, seed), _won_line(f, seed),
             "무선 기능이 있는 기기는 미국발이어도 면세한도가 $150이에요." if f["limit"] == 150 else None, "",
             _q(angle, f, seed)]
    else:  # basic
        L = [pick([f"{name}{g}, {_price_line(f, seed)}.",
                   f"{f['store']}에 {name} {f['pct']}% 할인 떴어요." if f["pct"] else f"{f['store']}에 {name} 떴어요.",
                   f"{usd(f['now'])}에 {name}."], seed + "ba"), "",
             _duty_line(f, seed), _won_line(f, seed), "", _q(angle, f, seed)]
    text = "\n".join(x for x in L if x is not None)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


REPLY_TAILS = ["팔로우해두면 이런 딜을 피드에서 바로 볼 수 있어요.",
               "블프까지 이런 딜을 골라서 계속 올려요.",
               "가격은 수시로 바뀌니 결제 전에 한 번 더 확인하세요.",
               "다른 사이즈·색상은 링크에서 확인하세요."]


def compose(d: dict, fx: float | None = None, recent_angles: list[str] | None = None,
            recent_openers: list[str] | None = None, now: dt.datetime | None = None) -> dict:
    f = facts(d, fx, now)
    angle = choose_angle(f, recent_angles)
    day = (now or dt.datetime.now(KST)).strftime("%Y%m%d")
    seed = f"{f['title']}{day}"
    text = write(angle, f, seed)
    # 최근 글과 첫 문장이 같으면 다른 문형으로 다시 쓴다
    for i in range(3):
        if not recent_openers or text.split("\n")[0] not in recent_openers:
            break
        text = write(angle, f, seed + str(i))
    link = d.get("buy_url") or d.get("url")
    reply = f"상품 링크: {link}\n{pick(REPLY_TAILS, seed + 'r')}"
    if d.get("buy_url") and d.get("buy_url") != d.get("url"):
        reply = "이 링크는 제휴 링크로, 구매 시 일정액의 수수료를 제공받습니다.\n" + reply
    return {"text": text[:500], "reply": reply[:500], "angle": angle, "problems": problems(f), "facts": f}


def roundup(deals: list[dict], fx: float | None = None, now: dt.datetime | None = None) -> dict:
    """비슷한 시간에 승인된 딜 여러 개를 '모아보기' 글 하나로 (최대 5개 — 답글 링크 5개 제한)."""
    now = now or dt.datetime.now(KST)
    fs = [facts(d, fx, now) for d in deals[:5]]
    kinds = {f["kind"] for f in fs}
    label = {"shoes": "신발", "clothes": "옷", "bags": "가방·잡화", "electronics": "전자기기"}.get(next(iter(kinds)), "") \
        if len(kinds) == 1 else ""
    allfree = all(f["free"] for f in fs)
    head = pick([f"오늘 들어온 직구 {label + ' ' if label else ''}딜 {len(fs)}개, 한 번에 볼게요.",
                 f"{'관부가세 없는 ' if allfree else ''}{label + ' ' if label else ''}딜 {len(fs)}개 모아봤어요.",
                 f"지금 살 만한 직구 {label + ' ' if label else ''}딜 {len(fs)}개."], now.strftime("%Y%m%d%H"))
    lines = [head, ""]
    for i, f in enumerate(fs, 1):
        price = f"{usd(f['was'])} → {usd(f['now'])}" if f["was"] else usd(f["now"])
        pct = f" ({f['pct']}%)" if f["pct"] else ""
        lines.append(f"{i}. {f['name']} {price}{pct}")
    lines += ["", "모두 미국발 면세한도 안이라 관부가세 없어요." if allfree else "면세한도는 상품마다 달라요. 카드 이미지에서 확인하세요.",
              "", pick(["이 중에 하나만 고른다면 몇 번이에요?", "다들 몇 번이 제일 끌려요?"], now.strftime("%Y%m%d"))]
    links = [f"{i}. {d.get('buy_url') or d.get('url')}" for i, d in enumerate(deals[:5], 1)]
    reply = "\n".join(links)
    if any(d.get("buy_url") and d.get("buy_url") != d.get("url") for d in deals[:5]):
        reply = "일부 링크는 제휴 링크로, 구매 시 일정액의 수수료를 제공받습니다.\n" + reply
    return {"text": "\n".join(lines)[:500], "reply": reply[:500], "angle": "roundup"}
