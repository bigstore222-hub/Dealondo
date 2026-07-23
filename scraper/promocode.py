"""
promocode.py — 결제 단계 프로모션 코드 추출

왜 이게 핵심인가:
    아마존의 클릭 쿠폰과 달리, 결제창에 입력하는 프로모션 코드는
    리테일러가 **특정 채널로만 먼저 뿌린다.** 조사해보면 1차 경로는 거의 이메일이다.
    브랜드가 구독자에게 코드를 보내고 → 받은 사람이 슬릭딜에 올린다.

    즉 **메일함에 코드가 도착한 시점이 슬릭딜보다 앞선다.**
    이 시간차가 "슬릭딜보다 먼저 공유한다"의 실현 가능한 지점이다.

    세일 페이지에는 코드가 거의 노출되지 않는다(실측 확인).
    리테일러는 아무나 쓸 수 있게 두지 않기 때문이다.

추출 전략:
    코드 문자열만 찾으면 오탐이 폭발한다(SALE, SHOP, HTTPS 같은 게 다 걸린다).
    그래서 **"코드를 알리는 문맥"** 을 먼저 찾고, 그 근처에서만 코드를 뽑는다.
    추가로 코드에 딸린 할인율·최소구매액·만료일까지 같이 잡는다.
"""
from __future__ import annotations
import re
from dataclasses import dataclass


# 코드임을 알리는 문맥 (이게 있어야 코드로 인정)
_CONTEXT = (
    r"(?:promo(?:tion)?\s*code|coupon\s*code|discount\s*code|offer\s*code"
    r"|use\s*code|with\s*code|apply\s*code|enter\s*code|code"
    r"|프로모\s*코드|쿠폰\s*코드|할인\s*코드|코드)"
)
# 코드 본체: 대문자/숫자 조합 4~20자
_CODE_BODY = r"([A-Z0-9][A-Z0-9\-_]{3,19})"

_CODE_PATTERNS = [
    re.compile(_CONTEXT + r"\s*[:：]?\s*[\"'\[]?\s*" + _CODE_BODY, re.I),
    re.compile(_CODE_BODY + r"\s*(?:을|를)?\s*(?:입력|사용)", re.I),
    re.compile(r"\b" + _CODE_BODY + r"\b\s*(?:at\s*checkout|at\s*check\s*out)", re.I),
]

# 코드처럼 생겼지만 코드가 아닌 것들
_BLOCKLIST = {
    "SALE", "SHOP", "HTTPS", "HTTP", "CLICK", "HERE", "FREE", "SAVE", "MORE",
    "NEWS", "VIEW", "OPEN", "TERMS", "APPLY", "OFFER", "PROMO", "CODE", "COUPON",
    "ORDER", "PRICE", "TODAY", "NULL", "NONE", "TRUE", "FALSE", "EMAIL", "UNSUB",
    "WOMEN", "MENS", "KIDS", "HOME", "GIFT", "CART", "ITEM", "SIZE", "COLOR",
    "SHIPPING", "RETURN", "DETAIL", "DETAILS", "EXCLUSIONS", "SELECT", "STYLES",
    "ONLINE", "STORE", "STORES", "LIMITED", "ENDS", "SOON", "NEW", "ONLY",
}

_PCT = re.compile(r"(\d{1,2})\s*%\s*(?:off|할인)", re.I)
_AMT = re.compile(r"\$\s?(\d{1,4}(?:\.\d{2})?)\s*off", re.I)
_MIN = re.compile(r"(?:on\s+orders?\s+(?:of\s+)?|orders?\s+over\s+|이상)\s*\$?\s?(\d{2,4})", re.I)
_EXP = re.compile(r"(?:expires?|ends?|valid\s+(?:through|until))\s*[:\s]*"
                  r"([A-Z][a-z]{2,8}\.?\s*\d{1,2}(?:,?\s*\d{4})?|\d{1,2}/\d{1,2}(?:/\d{2,4})?)", re.I)


# 코드 성격 판정 신호
#
# 이걸 구분하지 않으면 치명적이다.
# 신규가입 코드는 1회용이고 내 계정에 묶여 있어서, 그걸 적용한 가격을 발행하면
# 구독자는 그 가격에 살 수 없다. 딜보드에서 가장 위험한 오류다.
_WELCOME = re.compile(
    r"welcome|first\s+(?:order|purchase|time)|new\s+(?:customer|member|subscriber)"
    r"|thanks?\s+for\s+(?:signing|subscrib|joining)|sign(?:ing)?\s*up\s*(?:offer|gift|code)"
    r"|신규\s*가입|첫\s*구매|가입\s*축하|환영", re.I)

_PERSONAL = re.compile(
    r"just\s+for\s+you|exclusive(?:ly)?\s+for\s+you|your\s+exclusive"
    r"|member[- ]only|members\s+only|your\s+birthday|loyalty\s+reward"
    r"|because\s+you|we\s+miss\s+you|회원\s*전용|고객님만|생일", re.I)

_PUBLIC = re.compile(
    r"sitewide|site\s*wide|everyone|all\s+customers|storewide|store\s*wide"
    r"|friends\s*(?:&|and)\s*family|everything|entire\s+(?:site|store|order)"
    r"|전\s*상품|전\s*품목|전\s*사이트", re.I)


@dataclass
class PromoCode:
    code: str
    percent: int | None = None       # 할인율
    amount: float | None = None      # 정액 할인($)
    min_order: float | None = None   # 최소 주문액
    expires: str = ""
    context: str = ""                # 코드 주변 문장 (검증용)
    kind: str = "unknown"            # public / welcome / personal / unknown

    @property
    def shareable(self) -> bool:
        """다른 사람에게 공유해도 실제로 쓸 수 있는 코드인가."""
        return self.kind == "public"

    def describe(self) -> str:
        parts = [self.code]
        if self.percent:
            parts.append(f"{self.percent}% 할인")
        elif self.amount:
            parts.append(f"${self.amount:g} 할인")
        if self.min_order:
            parts.append(f"${self.min_order:g} 이상")
        if self.expires:
            parts.append(f"~{self.expires}")
        label = {"welcome": "신규가입 전용", "personal": "개인 전용",
                 "unknown": "적용범위 미확인"}.get(self.kind)
        if label:
            parts.append(f"[{label}]")
        return " · ".join(parts)


def classify(context: str, subject: str = "", public_source: bool = False) -> str:
    """
    코드의 적용 범위를 판정한다.
    확신이 없으면 unknown 으로 두고, unknown 은 상품에 자동 적용하지 않는다.
    (틀리게 싸다고 알리는 것보다 놓치는 편이 낫다)

    public_source=True 는 "이 코드가 공개 딜 게시글(슬릭딜·DoA 등)에서 왔다"는 뜻.
    이런 코드는 애초에 모두가 쓰라고 공개 브로드캐스트된 것이라, 신규가입/개인 신호가
    없으면 공개(public)로 본다. 이메일 뉴스레터(신규가입 코드가 섞임)와 달리 안전하다.
    """
    blob = f"{subject} {context}"
    if _WELCOME.search(blob):
        return "welcome"
    if _PERSONAL.search(blob):
        return "personal"
    if _PUBLIC.search(blob):
        return "public"
    # 아마존 셀러 코드처럼 딜사이트에 공개된 '결제창 코드'는 공개로 간주.
    if public_source:
        return "public"
    return "unknown"


def _valid(code: str) -> bool:
    c = code.upper()
    if c in _BLOCKLIST or len(c) < 4:
        return False
    # 순수 알파벳 단어(사전에 있을 법한)는 제외 — 진짜 코드는 보통 숫자를 포함하거나
    # 흔한 단어가 아니다. 단 8자 이상 대문자 조합은 코드로 인정.
    if c.isalpha() and len(c) < 8:
        return False
    # 연도·가격처럼 보이는 순수 숫자 제외
    if c.isdigit():
        return False
    return True


def extract(text: str, max_codes: int = 5, subject: str = "",
            public_source: bool = False) -> list[PromoCode]:
    """
    본문에서 프로모션 코드를 뽑는다.
    문맥 없이 대문자 덩어리만 있는 건 무시한다(오탐 방지).

    public_source=True 면 공개 딜 게시글 출처로 보고, 신규가입/개인 신호가 없는 코드를
    공유 가능(public)으로 분류한다. (DoA RSS·슬릭딜 본문에 쓴다)
    """
    if not text:
        return []
    flat = re.sub(r"\s+", " ", text)
    found: dict[str, PromoCode] = {}

    for pat in _CODE_PATTERNS:
        for m in pat.finditer(flat):
            code = m.group(1).upper().strip("-_")
            if not _valid(code) or code in found:
                continue
            # 코드 주변 200자를 문맥으로 삼아 조건을 읽는다
            lo, hi = max(0, m.start() - 120), min(len(flat), m.end() + 200)
            ctx = flat[lo:hi]

            pc = PromoCode(code=code, context=ctx.strip()[:180])

            # 할인율은 '코드에서 가장 가까운' 것을 쓴다.
            # "Up to 80% off ... extra 20% off with code EXTRA20" 같은 문장에서
            # 단순히 첫 매치를 쓰면 80%를 코드 할인으로 오인한다(실제로 발생).
            code_pos = m.start(1) - lo
            cands = []
            for pm in _PCT.finditer(ctx):
                v = int(pm.group(1))
                if 0 < v < 100:
                    # 'extra/additional' 이 앞에 붙은 값은 코드 할인일 확률이 높다
                    near = ctx[max(0, pm.start() - 25):pm.start()].lower()
                    bonus = -40 if re.search(r"extra|additional|추가", near) else 0
                    cands.append((abs(pm.start() - code_pos) + bonus, v))
            if cands:
                pc.percent = min(cands)[1]

            if pc.percent is None:
                am = _AMT.search(ctx)
                if am:
                    pc.amount = float(am.group(1))
            mm = _MIN.search(ctx)
            if mm:
                pc.min_order = float(mm.group(1))
            em = _EXP.search(ctx)
            if em:
                pc.expires = em.group(1).strip()

            # 적용 범위 판정.
            # 코드 주변 문맥이 1차 근거, 메일 전체(제목 포함)가 2차 근거다.
            # 예: 제목이 "Welcome! Here's 15% off" 면 본문에 신호가 없어도 신규가입 코드다.
            pc.kind = classify(ctx, subject, public_source)
            if pc.kind == "unknown":
                pc.kind = classify(flat[:1500], subject, public_source)

            found[code] = pc
            if len(found) >= max_codes:
                return list(found.values())
    return list(found.values())


def best(codes: list[PromoCode], shareable_only: bool = False) -> PromoCode | None:
    """
    할인 폭이 가장 큰 코드. 조건 없는 것을 우선한다.
    shareable_only=True 면 공유 가능한(public) 코드만 후보로 본다.
    """
    pool = [c for c in codes if c.shareable] if shareable_only else codes
    if not pool:
        return None

    def rank(c: PromoCode):
        val = c.percent or 0
        if not val and c.amount:
            val = min(int(c.amount), 50)      # 정액은 대략 환산
        # 공유 가능한 코드를 우선한다 (같은 할인폭이면 public 이 이김)
        return (1 if c.shareable else 0, val, -(c.min_order or 0))
    return max(pool, key=rank)


def apply_to_price(price: float, pc: PromoCode) -> float:
    """코드를 적용한 실구매가."""
    if pc.percent:
        return round(price * (1 - pc.percent / 100), 2)
    if pc.amount and pc.amount < price:
        return round(price - pc.amount, 2)
    return price


if __name__ == "__main__":
    samples = [
        ("Extra 30% off sitewide. Use code SAVE30 at checkout. Ends July 25.",
         "Extra 30% Off Everything"),
        ("Welcome! Thanks for signing up. Take 15% off your first order with code WELCOME15.",
         "Welcome to Macy's"),
        ("Just for you: exclusive 25% off with code VIP25.",
         "An exclusive offer"),
        ("Take $25 off orders over $100 with promo code SUMMER25. Expires Aug 1, 2026.",
         "Summer Sale"),
        ("Shop the sale now! Free shipping on all orders.", "Free Shipping"),
    ]
    print("코드         할인   적용범위        공유가능")
    print("-" * 52)
    for body, subj in samples:
        cs = extract(body, subject=subj)
        if not cs:
            print(f"(코드 없음)  —      —               —      ← {subj}")
            continue
        for c in cs:
            v = f"{c.percent}%" if c.percent else (f"${c.amount:g}" if c.amount else "-")
            print(f"{c.code:12s} {v:6s} {c.kind:14s} {'O' if c.shareable else 'X'}")
