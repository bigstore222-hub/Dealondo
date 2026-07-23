"""
parsers.py — 사이트별 전용 파서

범용 파서(generic_sale_parser)는 "링크 + 근처 가격 두 개"만 집어서
브랜드를 놓친다. 브랜드가 없으면 스펙 3-C 카테고리 점수가 전부
'기타 5점'으로 떨어져 발행 기준(50점)을 못 넘는다.

각 파서는 그 사이트의 실제 마크업 구조를 이용해
브랜드·평점·리뷰수까지 정확히 뽑아낸다.

파서 시그니처: parser(html: str, site: Site) -> list[dict]
반환 dict는 filter_engine.Deal 의 필드 일부.
"""
from __future__ import annotations
import re, json, html as htmlmod


def _unescape_js(s: str) -> str:
    """Zappos 등이 쓰는 \\u002F 형태 유니코드 이스케이프 복원."""
    try:
        return json.loads(f'"{s}"')
    except Exception:
        return s.replace("\\u002F", "/").replace("\\/", "/")


def _slug_to_brand(href: str, title: str) -> str:
    """
    노드스트롬 계열은 상품 URL 슬러그에 브랜드가 들어있다.
        /s/kate-spade-new-york-kara-loafer-women/8725791
         └── 브랜드 ──┘└─ 상품명 ─┘
    상품명(title)을 슬러그에서 제거하면 앞부분이 브랜드로 남는다.
    """
    m = re.search(r"/s/([a-z0-9-]+)/", href)
    if not m:
        return ""
    slug = m.group(1)
    t_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if t_slug and t_slug in slug:
        brand = slug.split(t_slug)[0].strip("-")
    else:
        # 상품명 매칭 실패 시: 뒤쪽 성별/카테고리 토큰을 떼고 앞 3토큰까지를 브랜드 후보로
        parts = [p for p in slug.split("-") if p not in ("women", "men", "kids", "girls", "boys")]
        brand = "-".join(parts[:3])
    brand = brand.replace("-", " ").strip()
    return " ".join(w.capitalize() for w in brand.split())[:40]


# ---------------------------------------------------------------------------
# Nordstrom Rack / Nordstrom — 상품 카드 <article> + URL 슬러그 브랜드
# ---------------------------------------------------------------------------
_NR_CARD = re.compile(
    r'<article[^>]*>(?P<card>.{0,3000}?)</article>', re.S)
_NR_LINK = re.compile(r'href="(?P<href>/s/[^"?]+)')
_NR_TITLE = re.compile(r'alt="(?P<title>[^"]{4,120})"')
_NR_PRICES = re.compile(r'\$(?P<a>[\d,]+(?:\.\d{2})?)')
_NR_IMG = re.compile(r'<img[^>]+src="(?P<img>https://n\.nordstrommedia\.com/[^"]+)"')


def nordstrom(html: str, site) -> list[dict]:
    rows, seen = [], set()
    for cm in _NR_CARD.finditer(html):
        card = cm.group("card")
        lm = _NR_LINK.search(card)
        tm = _NR_TITLE.search(card)
        if not (lm and tm):
            continue
        href = htmlmod.unescape(lm.group("href"))
        title = htmlmod.unescape(tm.group("title")).replace(", Image", "").strip()
        prices = [float(p.replace(",", "")) for p in _NR_PRICES.findall(card)]
        if len(prices) < 2:
            continue
        cur, was = min(prices), max(prices)
        if was <= cur:
            continue
        key = href.split("?")[0]
        if key in seen:
            continue
        seen.add(key)

        brand = _slug_to_brand(href, title)
        im = _NR_IMG.search(card)
        rows.append({
            "url": site.base_url + href,
            "title": title[:120],
            "brand": brand or site.name,
            "image": htmlmod.unescape(im.group("img")) if im else "",
            "price_current": cur,
            "price_list": was,
            "price_baseline": was,
        })
        if len(rows) >= 60:
            break
    return rows


# ---------------------------------------------------------------------------
# Zappos — 페이지에 상품 JSON이 통째로 들어있다
#   brandName / productName / price / originalPrice / percentOff
#   productRating / reviewCount  → H3 하드필터(평점·리뷰수)까지 채울 수 있다
# ---------------------------------------------------------------------------
def _num(s: str):
    try:
        return float(str(s).replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return None


def enclosing_object(s: str, idx: int) -> str | None:
    """
    s[idx] 위치를 감싸는 JSON 객체 `{...}` 를 균형 중괄호로 잘라낸다.

    임베디드 상품 JSON은 이미지 URL 같은 **중첩 객체**를 품고 있어,
    정규식으로 `{...}` 경계를 잡으면 originalPrice 가 잘려나간다(실측: Zappos는
    정가가 전부 누락돼 모든 딜이 27점으로 탈락했다). 앞으로 걸어가 여는 `{` 를,
    뒤로 걸어가 짝이 맞는 `}` 를 찾아 정확한 객체를 반환한다.
    """
    depth = 0
    start = None
    i = idx
    while i >= 0:
        c = s[i]
        if c == '}':
            depth += 1
        elif c == '{':
            if depth == 0:
                start = i
                break
            depth -= 1
        i -= 1
    if start is None:
        return None
    depth = 0
    j = start
    n = len(s)
    while j < n:
        c = s[j]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return s[start:j + 1]
        j += 1
    return None


_ZAP_PID = re.compile(r'"productId":"(\d+)"')


def zappos(html: str, site) -> list[dict]:
    rows, seen = [], set()
    for pm in _ZAP_PID.finditer(html):
        pid = pm.group(1)
        if pid in seen:
            continue
        blk = enclosing_object(html, pm.start())
        if not blk or '"originalPrice"' not in blk or '"productName"' not in blk:
            continue

        def g(key, pat=r'"([^"]*)"'):
            m = re.search(rf'"{key}":\s*{pat}', blk)
            return m.group(1) if m else None

        brand = g("brandName")
        name = g("productName")
        cur = _num(g("price", r'"?\$?([\d,.]+)'))
        orig = _num(g("originalPrice", r'"?\$?([\d,.]+)'))
        if not (brand and name and cur):
            continue
        if not orig or orig <= cur:
            pct = _num(g("percentOff", r'"?([\d.]+)'))
            if pct and 0 < pct < 100:
                orig = round(cur / (1 - pct / 100), 2)
            else:
                continue
        seen.add(pid)

        rating = _num(g("productRating", r'"?([\d.]+)'))
        reviews = _num(g("reviewCount", r'"?([\d,]+)'))
        name = _unescape_js(name)
        brand = _unescape_js(brand)

        # Zappos 이미지: thumbnailImageUrl 또는 msaImageId 로 URL 조립
        img = g("thumbnailImageUrl")
        if img:
            img = _unescape_js(img)
        else:
            msa = g("msaImageId")
            img = f"https://m.media-amazon.com/images/I/{msa}._AC_SR255,340_.jpg" if msa else ""

        row = {
            "url": f"{site.base_url}/product/{pid}" if pid else site.base_url,
            "title": name[:120],
            "brand": brand,
            "image": img,
            "price_current": cur,
            "price_list": orig,
            "price_baseline": orig,
        }
        # 평점/리뷰가 있으면 H3 하드필터가 실제로 동작한다.
        # 단 0값은 '정보 없음'이므로 넣지 않는다(오탈락 방지).
        if rating:
            row["rating"] = rating
        if reviews:
            row["review_count"] = int(reviews)
        rows.append(row)
        # 세일 페이지는 얕은 할인부터 노출되므로, 상한을 넉넉히 둬
        # 뒤쪽의 깊은 할인(80%+) 상품까지 후보로 확보한다. 필터가 알아서 거른다.
        if len(rows) >= 200:
            break
    return rows


# ---------------------------------------------------------------------------
# eBay — 헤드리스 렌더 후 dne-itemtile 카드 + schema.org 마이크로데이터
#   주의: eBay는 접속 지역에 따라 통화를 바꿔 보여준다(한국에서는 KRW).
#   통화를 그대로 보존하고, 원화면 krw_effective 에도 넣어 H2 판정에 쓴다.
# ---------------------------------------------------------------------------
# 상품 카드 경계는 data-listing-id 가 가장 안정적이다
# (dne-itemtile div는 중첩돼 있어 경계로 쓰면 가격이 잘려나간다)
_EB_SPLIT = re.compile(r'(?=data-listing-id=")')
_EB_NAME = re.compile(r'itemprop="name"[^>]*>(?P<name>.{0,200}?)</span>', re.S)
_EB_URL = re.compile(r'href="(?P<url>https://www\.ebay\.com/itm/\d+)')
_EB_PRICE = re.compile(r'itemprop="price"[^>]*>(?P<cur>[A-Z]{0,3})\s?\$?(?P<val>[\d,]+(?:\.\d+)?)')
_EB_STRIKE = re.compile(r'itemtile-price-strikethrough"[^>]*>(?P<cur>[A-Z]{0,3})\s?\$?(?P<val>[\d,]+(?:\.\d+)?)')
_EB_IMG = re.compile(r'<img[^>]+src="(?P<img>https://i\.ebayimg\.com/[^"]+)"')


def ebay(html: str, site) -> list[dict]:
    rows, seen = [], set()
    for tile in _EB_SPLIT.split(html):
        if 'itemprop="price"' not in tile or "ebay.com/itm/" not in tile:
            continue
        nm = _EB_NAME.search(tile)
        um = _EB_URL.search(tile)
        pm = _EB_PRICE.search(tile)
        if not (nm and um and pm):
            continue
        url = um.group("url")
        if url in seen:
            continue

        title = htmlmod.unescape(re.sub(r"<[^>]+>", "", nm.group("name"))).strip()
        cur = _num(pm.group("val"))
        currency = pm.group("cur") or "USD"
        sm = _EB_STRIKE.search(tile)
        was = _num(sm.group("val")) if sm else None
        if not cur or not was or was <= cur:
            continue          # 정가 없는 항목은 할인 판정 불가 → 스킵

        seen.add(url)
        eim = _EB_IMG.search(tile)
        row = {
            "url": url,
            "title": title[:120],
            "brand": title.split()[0] if title else site.name,
            "image": htmlmod.unescape(eim.group("img")) if eim else "",
            "price_current": cur,
            "price_list": was,
            "price_baseline": was,
        }
        # 표시 통화를 보존한다. pricing.enrich() 가 이걸 보고 원화 환산 여부를 정한다.
        row["currency"] = currency if currency in ("KRW", "USD") else "USD"
        rows.append(row)
        if len(rows) >= 60:
            break
    return rows


# ---------------------------------------------------------------------------
# Amazon — 헤드리스 렌더 후 페이지에 박힌 딜 JSON을 파싱
#   구조: {"asin":..,"title":..,"link":"/../dp/ASIN",..,
#          "price":{"priceToPay":{"price":"199.99"},"basisPrice":{"price":"399.99"}},
#          "dealBadge":{...{"text":"50% off"}...,
#                       ...{"countdownTimer":{"targetTime":"2026-07-20T06:59:59.000Z"}}},
#          "dealDetails":{"state":"AVAILABLE",...}}
#   주의: 골드박스 그리드는 가격을 지연 로딩해서, 렌더 시점에 가격이 붙은 상품만 잡힌다.
#         (렌더 후 스크롤로 최대한 끌어내린 뒤 파싱)
# ---------------------------------------------------------------------------
_AMZ_ASIN = re.compile(r'"asin":"(?P<asin>[A-Z0-9]{10})"')
_AMZ_PRICE = re.compile(
    r'"priceToPay":\{[^{}]*?"price":"(?P<cur>[\d,.]+)"')
_AMZ_BASIS = re.compile(
    r'"basisPrice":\{[^{}]*?"price":"(?P<was>[\d,.]+)"')
_AMZ_TITLE = re.compile(r'"title":"(?P<title>(?:[^"\\]|\\.){3,300})"')
_AMZ_LINK = re.compile(r'"link":"(?P<link>/[^"]{5,300}?/dp/[A-Z0-9]{10})"')
_AMZ_BADGE = re.compile(r'"text":"(?P<pct>\d{1,2})% off"')
_AMZ_TIMER = re.compile(r'"targetTime":"(?P<t>[\dTZ:.\-]{10,40})"')
_AMZ_STATE = re.compile(r'"dealDetails":\{"state":"(?P<state>[A-Z_]+)"')
# 클릭 적용 쿠폰. 아마존 딜의 진짜 가치는 여기 있는 경우가 많다.
#   "coupon":{"label":{"text":"Save 10%"},"messaging":{"text":" with coupon"},"id":"/promo/XXX"}
#   "coupon":{"label":{"text":"Save $30"},...}
_AMZ_COUPON = re.compile(
    r'"coupon":\{"label":\{"text":"Save\s+(?P<pct>\d{1,2})%"'
    r'|"coupon":\{"label":\{"text":"Save\s+\$(?P<amt>[\d.]+)"')
_AMZ_PROMO = re.compile(r'"coupon":\{[^{}]*?"id":"(?P<promo>/promo/[A-Z0-9]+)"')
# 이미지: {"lowRes":{"baseUrl":"https://m.media-amazon.com/images/I/41xxx","extension":"jpg"}}
_AMZ_IMG = re.compile(
    r'"(?:lowRes|hiRes)":\{[^{}]*?"baseUrl":"(?P<base>https://m\.media-amazon\.com/images/I/[^"]+)"'
    r'[^{}]*?"extension":"(?P<ext>\w+)"')


def amazon(html: str, site) -> list[dict]:
    rows, seen = [], set()

    # 상품 경계: "asin" 등장 지점으로 자른 뒤, 가격이 붙은 조각만 사용
    idxs = [m.start() for m in _AMZ_ASIN.finditer(html)]
    idxs.append(len(html))

    for k in range(len(idxs) - 1):
        chunk = html[idxs[k]:idxs[k + 1]]
        if '"priceToPay"' not in chunk:
            continue          # 가격이 아직 안 붙은 상품(지연 로딩) → 스킵

        pm = _AMZ_PRICE.search(chunk)
        if not pm:
            continue
        cur = _num(pm.group("cur"))
        bm = _AMZ_BASIS.search(chunk)
        was = _num(bm.group("was")) if bm else None

        # 정가가 없으면 할인배지(% off)로 역산
        if not was or was <= cur:
            pct_m = _AMZ_BADGE.search(chunk)
            pct = int(pct_m.group("pct")) if pct_m else 0
            if 0 < pct < 100 and cur:
                was = round(cur / (1 - pct / 100), 2)
            else:
                continue

        am = _AMZ_ASIN.search(chunk)
        asin = am.group("asin") if am else None
        if not asin or asin in seen or not cur:
            continue
        seen.add(asin)

        tm = _AMZ_TITLE.search(chunk)
        title = _unescape_js(tm.group("title")) if tm else ""
        if len(title) < 5:
            continue
        lm = _AMZ_LINK.search(chunk)
        url = (site.base_url + _unescape_js(lm.group("link"))) if lm \
            else f"{site.base_url}/dp/{asin}"

        im = _AMZ_IMG.search(chunk)

        # 클릭 적용 쿠폰 반영.
        # 아마존은 표시 할인율이 30~50%여도 상품 페이지의 쿠폰을 적용하면
        # 실구매가가 크게 내려간다. 4년 실측에서 아마존이 1위 소스였던 이유가 이것이다.
        # 쿠폰 적용가를 실제 구매가로 보고 할인율을 다시 계산한다.
        coupon_txt = ""
        cm2 = _AMZ_COUPON.search(chunk)
        if cm2:
            if cm2.group("pct"):
                p = int(cm2.group("pct"))
                if 0 < p < 100:
                    cur = round(cur * (1 - p / 100), 2)
                    coupon_txt = f"쿠폰 {p}% 추가할인"
            elif cm2.group("amt"):
                a = _num(cm2.group("amt"))
                if a and a < cur:
                    cur = round(cur - a, 2)
                    coupon_txt = f"쿠폰 ${a:g} 추가할인"

        row = {
            "url": url,
            "title": title[:120],
            # 아마존 제목은 보통 '브랜드 + 상품명' 순이라 첫 토큰이 브랜드에 가깝다
            "brand": title.split(",")[0].split()[0] if title.split() else site.name,
            "image": f'{im.group("base")}._AC_SR255,340_.{im.group("ext")}' if im else "",
            "price_current": cur,
            "price_list": was,
            "price_baseline": was,
        }
        if coupon_txt:
            row["coupon_code"] = coupon_txt
            row["coupon_stackable"] = True     # 세일가 위에 추가로 붙는 쿠폰

        # 마감 타이머 → expires_at (H4 만료 하드필터에 사용)
        tmr = _AMZ_TIMER.search(chunk)
        if tmr:
            row["expires_at"] = tmr.group("t")
        # 재고 상태
        sm = _AMZ_STATE.search(chunk)
        if sm and sm.group("state") != "AVAILABLE":
            row["stock_status"] = "out_of_stock"

        rows.append(row)
        if len(rows) >= 60:
            break
    return rows


# ---------------------------------------------------------------------------
# Woot — 헤드리스 렌더 후 offerItem 카드 파싱
#   Woot은 React Native Web이라 CSS 클래스가 난독화(css-175oi2r r-e5yqq3)돼 있어
#   클래스 셀렉터를 못 쓴다. 대신 안정적인 data-test-ui="offerItem" 을 경계로 쓴다.
#   가격은 달러/센트가 별도 div로 쪼개져 렌더된다: $ | 439 | 00  →  439.00
#   카드 텍스트 순서: 가격 → 참조가 → "Reference Price" → "Save: $60.00 (12%)" → 제목
#
#   실측(4년치)에서 Woot은 서브도메인별로 게시됐다:
#     sport 64 / electronics 42 / computers 40 / sellout 31 / home 20 / tools 6
#   /alldeals 페이지가 전 서브도메인 딜을 한 번에 모아준다.
# ---------------------------------------------------------------------------
# 주의: 카드 경계 lookahead에 닫는 따옴표를 반드시 넣을 것.
# 넣지 않으면 카드 내부의 data-test-ui="offerItemImage" 에도 걸려 본문이 잘린다.
_WT_CARD = re.compile(
    r'href="(?P<url>https?://[a-z]+\.woot\.com/offers/[a-z0-9\-]{4,100})[^"]*"'
    r'[^>]*data-test-ui="offerItem"(?P<body>.{0,4000}?)'
    r'(?=href="https?://[a-z]+\.woot\.com/offers/|$)', re.S)

# 태그를 구분자로 바꾼 텍스트 흐름에서 파싱한다.
# 예: $ | 439 | 00 | $499.00 | Reference Price | Save: $60.00 (12%) | 제목
_WT_CUR = re.compile(r'\$\s*\|?\s*([\d,]+)\s*\|\s*(\d{2})\b')
_WT_REF = re.compile(r'\$\s*([\d,]+\.\d{2})\s*\|?\s*Reference Price')
_WT_SAVE = re.compile(r'Save:\s*\$\s*([\d,]+\.\d{2})\s*\((\d{1,2})%\)')
_WT_TITLE = re.compile(r'Save:\s*\$[\d,]+\.\d{2}\s*\(\d{1,2}%\)\s*\|?\s*([^|]{5,140})')
_WT_IMG = re.compile(r'<img[^>]+src="(?P<img>https://[^"]*(?:wootcdn|media-amazon)[^"]+)"')


def _flow(s: str) -> str:
    """태그를 | 로 바꿔 텍스트 흐름을 만든다 (Woot은 값이 div로 잘게 쪼개져 있음)."""
    t = re.sub(r"<[^>]+>", "|", s)
    t = re.sub(r"\|{2,}", "|", t)
    return re.sub(r"[ \t]+", " ", t).strip()


def woot(html: str, site) -> list[dict]:
    rows, seen = [], set()
    for cm in _WT_CARD.finditer(html):
        url = htmlmod.unescape(cm.group("url"))
        body = cm.group("body")
        if url in seen:
            continue

        text = _flow(body)

        # 현재가: 쪼개진 달러/센트를 합친다 ($ | 439 | 00 → 439.00)
        cm2 = _WT_CUR.search(text)
        cur = _num(f"{cm2.group(1)}.{cm2.group(2)}") if cm2 else None
        if not cur:
            continue

        # 정가(Reference Price), 없으면 Save 퍼센트로 역산
        was = None
        rm = _WT_REF.search(text)
        if rm:
            was = _num(rm.group(1))
        if not was or was <= cur:
            sm = _WT_SAVE.search(text)
            if sm:
                pct = int(sm.group(2))
                if 0 < pct < 100:
                    was = round(cur / (1 - pct / 100), 2)
        if not was or was <= cur:
            continue

        # 제목: "Save: $X (N%)" 뒤에 오는 문장
        tm = _WT_TITLE.search(text)
        title = tm.group(1).strip() if tm else ""
        if not title:
            # 폴백: URL 슬러그를 제목화
            slug = url.rsplit("/", 1)[-1]
            title = " ".join(w.capitalize() for w in slug.split("-"))[:120]
        title = htmlmod.unescape(title)

        seen.add(url)
        wim = _WT_IMG.search(body)
        rows.append({
            "url": url,
            "title": title[:120],
            "brand": title.split(",")[0].split()[0] if title.split() else site.name,
            "image": htmlmod.unescape(wim.group("img")) if wim else "",
            "price_current": cur,
            "price_list": was,
            "price_baseline": was,
        })
        if len(rows) >= 60:
            break
    return rows


# ---------------------------------------------------------------------------
# 등록소 — sources.PARSERS 로 주입된다
# ---------------------------------------------------------------------------
REGISTRY = {
    "nordstromrack.com": nordstrom,
    "nordstrom.com": nordstrom,
    "zappos.com": zappos,
    "6pm.com": zappos,       # 6pm은 Zappos 계열 — 동일 임베디드 JSON 구조
    "ebay.com": ebay,
    "amazon.com": amazon,
    "woot.com": woot,
}
