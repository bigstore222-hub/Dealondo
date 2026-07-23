"""
generic_parser.py — 사이트 무관 범용 상품 추출기

왜 새로 만들었나:
    기존 범용 파서는 "링크 + 근처에 $가격 두 개"라는 좁은 패턴만 봤다.
    실측 결과 31개 사이트 중 3곳만 통과했다. 진단해보니 원인이 명확했다.

        SSENSE      세일페이지 1195KB, $가격 901개 → 파싱 0건
        Eddie Bauer  846KB, $가격 195개 → 파싱 0건
        Newegg       488KB, $가격 101개 → 파싱 0건

    접속도 되고 가격도 잔뜩 있는데 못 뽑았다. 파서가 병목이었다.

전략:
    사이트마다 마크업이 다르므로 **여러 전략을 순서대로 시도**하고
    가장 많이 뽑히는 것을 채택한다.

      1) JSON-LD (schema.org Product) — 표준이라 가장 신뢰도 높음
      2) 의미 속성 (data-test / data-testid 에 price·name·brand 포함)
      3) 클래스명 휴리스틱 (class 에 price / product-title 등)
      4) 근접 패턴 (기존 방식)

    실제 예시 — SSENSE:
        data-test="productBrandName0"    → adidas Originals
        data-test="productName0"         → 화이트 삼바 OG 스니커즈
        data-test="productCurrentPrice0" → $77
        data-test="productFormerPrice0"  → $110
"""
from __future__ import annotations
import json, re, html as htmlmod

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _txt(s: str) -> str:
    return _WS.sub(" ", htmlmod.unescape(_TAG.sub(" ", s))).strip()


def _num(s) -> float | None:
    try:
        v = float(re.sub(r"[^\d.]", "", str(s)))
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# 전략 1 — JSON-LD (schema.org)
# ---------------------------------------------------------------------------
_LD = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)


def _walk_products(node, out: list):
    """중첩된 JSON-LD에서 Product 노드를 모두 찾는다."""
    if isinstance(node, list):
        for n in node:
            _walk_products(n, out)
    elif isinstance(node, dict):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if "Product" in types:
            out.append(node)
        for v in node.values():
            if isinstance(v, (dict, list)):
                _walk_products(v, out)


def from_jsonld(html: str, base_url: str) -> list[dict]:
    prods: list = []
    for m in _LD.finditer(html):
        raw = m.group(1).strip()
        try:
            _walk_products(json.loads(raw), prods)
        except Exception:
            continue

    rows = []
    for p in prods:
        name = p.get("name")
        if not isinstance(name, str) or len(name) < 4:
            continue
        offers = p.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if not isinstance(offers, dict):
            continue
        cur = _num(offers.get("price") or offers.get("lowPrice"))
        if not cur:
            continue
        was = _num(offers.get("highPrice"))
        brand = p.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")
        img = p.get("image")
        if isinstance(img, list):
            img = img[0] if img else ""
        url = p.get("url") or offers.get("url") or ""
        rows.append({
            "title": _txt(name)[:120],
            "brand": _txt(brand)[:40] if isinstance(brand, str) else "",
            "image": img if isinstance(img, str) else "",
            "url": url if isinstance(url, str) and url.startswith("http") else base_url,
            "price_current": cur,
            "price_list": was,
        })
    return rows


# ---------------------------------------------------------------------------
# 전략 2 — 의미 속성 (data-test / data-testid)
# ---------------------------------------------------------------------------
# 예: data-test="productCurrentPrice0" ... </span>
_ATTR_ITEM = re.compile(
    r'data-test(?:id)?="(?P<key>[^"]*(?:price|name|brand|title)[^"]*?)(?P<idx>\d+)"[^>]*>(?P<val>[^<]{1,120})<',
    re.I)


def from_semantic_attrs(html: str, base_url: str) -> list[dict]:
    # 인덱스(0,1,2...)로 같은 상품끼리 묶는다
    groups: dict[str, dict] = {}
    for m in _ATTR_ITEM.finditer(html):
        key = m.group("key").lower()
        idx = m.group("idx")
        val = _txt(m.group("val"))
        if not val:
            continue
        g = groups.setdefault(idx, {})
        if "brand" in key:
            g.setdefault("brand", val)
        elif "former" in key or "was" in key or "original" in key or "list" in key:
            g.setdefault("was", val)
        elif "price" in key:
            g.setdefault("cur", val)
        elif "name" in key or "title" in key:
            g.setdefault("title", val)

    rows = []
    for idx, g in groups.items():
        cur = _num(g.get("cur"))
        if not cur or not g.get("title"):
            continue
        rows.append({
            "title": g["title"][:120],
            "brand": (g.get("brand") or "")[:40],
            "image": "",
            "url": base_url,
            "price_current": cur,
            "price_list": _num(g.get("was")),
        })
    return rows


# ---------------------------------------------------------------------------
# 전략 2.5 — 상용 검색 플랫폼 (Searchspring 등)
#
# 리테일러들이 자체 개발 대신 상용 검색 솔루션을 쓰는 경우가 많다.
# 플랫폼 하나를 지원하면 그걸 쓰는 여러 사이트가 한꺼번에 열린다.
#
# Searchspring 예시(Shoebacca):
#   <a href="/collections/sale/products/..." class="ss__result__details">
#     <p class="ss__result__brand">PUMA | Mens</p>
#     <p class="ss__result__name">AMF1 Replica Team Crew Neck T-Shirt</p>
#     <p class="ss__result__pricing">
#       <span class="ss__result__price--on-sale">$54.95</span>
#       <span class="ss__result__msrp">$70.00</span>
# ---------------------------------------------------------------------------
_SS_BLOCK = re.compile(
    r'href="(?P<href>[^"]+)"[^>]*class="[^"]*ss__result__details[^"]*"'
    r'(?P<body>.{0,900}?)</a>', re.S)
_SS_BRAND = re.compile(r'ss__result__brand[^>]*>([^<]{1,60})<')
_SS_NAME = re.compile(r'ss__result__name[^>]*>([^<]{3,140})<')
_SS_PRICE = re.compile(r'ss__result__price[^>]*>\s*\$?\s*([\d,]+(?:\.\d{2})?)')
_SS_MSRP = re.compile(r'ss__result__msrp[^>]*>\s*\$?\s*([\d,]+(?:\.\d{2})?)')


def from_searchspring(html: str, base_url: str) -> list[dict]:
    from urllib.parse import urljoin
    rows = []
    for m in _SS_BLOCK.finditer(html):
        body = m.group("body")
        nm = _SS_NAME.search(body)
        pm = _SS_PRICE.search(body)
        if not (nm and pm):
            continue
        cur = _num(pm.group(1))
        mm = _SS_MSRP.search(body)
        was = _num(mm.group(1)) if mm else None
        if not cur:
            continue
        bm = _SS_BRAND.search(body)
        brand = _txt(bm.group(1)).split("|")[0].strip() if bm else ""
        rows.append({
            "title": _txt(nm.group(1))[:120],
            "brand": brand[:40],
            "image": "",
            "url": urljoin(base_url, htmlmod.unescape(m.group("href"))),
            "price_current": cur,
            "price_list": was,
        })
    return rows


# ---------------------------------------------------------------------------
# 전략 2.7 — 의미 있는 클래스명 (product-name / product-oldprice 관례)
#
# 상당수 사이트가 클래스명에 역할을 그대로 적는다. 이건 사실상 업계 관례라
# 하나의 규칙으로 여러 사이트를 커버할 수 있다.
#
# Eddie Bauer 예시:
#   <a class="product-link" href="...">
#     <div class="product-brand">Eddie Bauer</div>
#     <div class="product-name">Men's Horizon Takeoff Stretch Chino Pants</div>
#     <div class="product-oldprice money">$70.00</div>
#     <div class="product-newprice money">$55.99</div>
# ---------------------------------------------------------------------------
# 본문 상한을 넉넉히 둔다. 카드 안에 이미지 srcset 이 길게 들어가는 사이트가 많아
# 3000자로 자르면 그 뒤에 오는 가격을 놓친다(실측: END Clothing).
_CS_LINK = re.compile(
    r'<a[^>]+href="(?P<href>[^"#]{6,300})"[^>]*>(?P<body>.{60,12000}?)</a>', re.S | re.I)
_CS_NAME = re.compile(
    r'class="[^"]*(?:product[-_]?(?:name|title)|item[-_]?(?:name|title))[^"]*"[^>]*>'
    r'\s*([^<]{4,140})', re.I)
_CS_BRAND = re.compile(r'class="[^"]*(?:product[-_]?brand|brand[-_]?name)[^"]*"[^>]*>\s*([^<]{2,60})', re.I)
# 정가: old / was / regular / compare / list / msrp
_CS_OLD = re.compile(
    r'class="[^"]*(?:old|was|regular|compare|list|msrp|original|strike)[^"]*price[^"]*"[^>]*>'
    r'[^\d<]{0,6}([\d,]+(?:\.\d{2})?)', re.I)
_CS_OLD2 = re.compile(
    r'class="[^"]*price[^"]*(?:old|was|regular|compare|list|msrp|original|strike)[^"]*"[^>]*>'
    r'[^\d<]{0,6}([\d,]+(?:\.\d{2})?)', re.I)
# 판매가: new / sale / final / special / current
_CS_NEW = re.compile(
    r'class="[^"]*(?:new|sale|final|special|current|now)[^"]*price[^"]*"[^>]*>'
    r'[^\d<]{0,6}([\d,]+(?:\.\d{2})?)', re.I)
_CS_NEW2 = re.compile(
    r'class="[^"]*price[^"]*(?:new|sale|final|special|current|now)[^"]*"[^>]*>'
    r'[^\d<]{0,6}([\d,]+(?:\.\d{2})?)', re.I)
_CS_IMG = re.compile(r'<img[^>]+(?:data-)?src="(https?://[^"]{10,300})"')
_CS_ALT = re.compile(r'alt="([^"]{5,140})"')

# data-test-id 방식도 같은 링크 블록 안에서 처리한다.
# 주의: 속성명이 data-test / data-testid / data-test-id 셋 다 쓰인다.
# 하이픈을 빠뜨리면 END Clothing 같은 사이트를 통째로 놓친다(실측).
#
# END Clothing 예시:
#   <span data-test-id="ProductCard__PlpName">New Balance Abzorb 1890 Sneaker</span>
#   <span data-test-id="ProductCard__ProductFullPrice"> $209</span>
#   <span data-test-id="ProductCard__ProductFinalPrice"> $105</span>
_DT = r'data-test[-_]?(?:id)?="[^"]*'
_CS_DT_NAME = re.compile(_DT + r'(?:name|title)[^"]*"[^>]*>\s*([^<]{4,140})', re.I)
_CS_DT_FULL = re.compile(_DT + r'(?:full|was|regular|original|list|msrp)price[^"]*"[^>]*>'
                         r'[^\d<]{0,6}([\d,]+(?:\.\d{2})?)', re.I)
_CS_DT_FINAL = re.compile(_DT + r'(?:final|sale|current|now|discount)price[^"]*"[^>]*>'
                          r'[^\d<]{0,6}([\d,]+(?:\.\d{2})?)', re.I)
_CS_DT_BRAND = re.compile(_DT + r'brand[^"]*"[^>]*>\s*([^<]{2,60})', re.I)


def _first(body: str, *pats):
    for p in pats:
        m = p.search(body)
        if m:
            return m.group(1)
    return None


def from_class_semantics(html: str, base_url: str) -> list[dict]:
    from urllib.parse import urljoin
    rows, seen = [], set()
    for m in _CS_LINK.finditer(html):
        body = m.group("body")
        cur = _num(_first(body, _CS_NEW, _CS_NEW2, _CS_DT_FINAL))
        was = _num(_first(body, _CS_OLD, _CS_OLD2, _CS_DT_FULL))
        if not cur or not was or was <= cur:
            continue

        title = _first(body, _CS_NAME, _CS_DT_NAME) or _first(body, _CS_ALT)
        if not title:
            continue
        url = urljoin(base_url, htmlmod.unescape(m.group("href")))
        if url in seen:
            continue
        seen.add(url)

        bm = _CS_BRAND.search(body) or _CS_DT_BRAND.search(body)
        im = _CS_IMG.search(body)
        rows.append({
            "title": _txt(title)[:120],
            "brand": _txt(bm.group(1))[:40] if bm else "",
            "image": im.group(1) if im else "",
            "url": url,
            "price_current": cur,
            "price_list": was,
        })
        if len(rows) >= 60:
            break
    return rows


# ---------------------------------------------------------------------------
# 전략 3 — 클래스명 휴리스틱 (상품 카드 블록 단위)
# ---------------------------------------------------------------------------
# 주의: 닫는 태그로 카드 경계를 잡으면 안 된다.
# 카드 안에 중첩된 <div>가 있어서 첫 </div>에서 잘리고,
# 그 뒤에 오는 가격을 놓친다(실측: Shoebacca에서 발생).
# 그래서 '다음 카드가 시작되는 지점'을 경계로 쓴다.
_CARD_OPEN = re.compile(
    r'<(?:li|article)[^>]+class="[^"]*(?:product|item|tile|card|result)[^"]*"[^>]*>', re.I)
_A_HREF = re.compile(r'href="(?P<h>[^"#]{4,200})"')
_IMG = re.compile(r'<img[^>]+src="(?P<s>https?://[^"]{10,300})"')
_PRICE_TAG = re.compile(
    r'class="[^"]*price[^"]*"[^>]*>\s*[^\d<]{0,8}([\d,]+(?:\.\d{2})?)', re.I)
_ANY_PRICE = re.compile(r'[\$€£]\s?([\d,]+(?:\.\d{2})?)')


def from_card_blocks(html: str, base_url: str) -> list[dict]:
    from urllib.parse import urljoin
    rows, seen = [], set()

    # 카드 시작 위치들을 찾아 '다음 시작 직전'까지를 한 카드로 본다
    starts = [m.start() for m in _CARD_OPEN.finditer(html)]
    if not starts:
        return []
    starts.append(len(html))
    blocks = [html[starts[i]:min(starts[i + 1], starts[i] + 6000)]
              for i in range(len(starts) - 1)]

    for body in blocks:
        prices = [_num(p) for p in _PRICE_TAG.findall(body)]
        prices = [p for p in prices if p]
        if len(prices) < 2:
            prices = [_num(p) for p in _ANY_PRICE.findall(body)]
            prices = [p for p in prices if p]
        if len(prices) < 2:
            continue
        cur, was = min(prices), max(prices)
        if was <= cur or was / cur > 50:      # 비현실적 비율 제외
            continue

        hm = _A_HREF.search(body)
        if not hm:
            continue
        url = urljoin(base_url, htmlmod.unescape(hm.group("h")))
        if url in seen:
            continue

        # 제목: alt 속성이나 링크 텍스트에서
        title = ""
        am = re.search(r'alt="([^"]{5,120})"', body)
        if am:
            title = _txt(am.group(1))
        if not title:
            tm = re.search(r'>([^<>{}]{8,120})<', _TAG.sub(lambda x: x.group(0), body))
            title = _txt(tm.group(1)) if tm else ""
        if len(title) < 5:
            continue

        seen.add(url)
        im = _IMG.search(body)
        rows.append({
            "title": title[:120], "brand": "",
            "image": im.group("s") if im else "",
            "url": url, "price_current": cur, "price_list": was,
        })
        if len(rows) >= 60:
            break
    return rows


# ---------------------------------------------------------------------------
# 통합 — 가장 많이 뽑히는 전략 채택
# ---------------------------------------------------------------------------
STRATEGIES = [
    ("jsonld", from_jsonld),
    ("semantic", from_semantic_attrs),
    ("searchspring", from_searchspring),
    ("class", from_class_semantics),
    ("card", from_card_blocks),
]


def parse(html: str, site, debug: bool = False) -> list[dict]:
    """
    사이트에 맞는 전략을 자동 선택해 상품을 추출한다.
    할인(정가 > 현재가)이 확인된 것만 남긴다.
    """
    best_name, best_rows = "", []
    for name, fn in STRATEGIES:
        try:
            rows = fn(html, site.base_url)
        except Exception:
            rows = []
        # 할인 있는 것만
        rows = [r for r in rows
                if r.get("price_current") and r.get("price_list")
                and r["price_list"] > r["price_current"]]
        if debug:
            print(f"    [{name}] {len(rows)}건")
        if len(rows) > len(best_rows):
            best_name, best_rows = name, rows

    for r in best_rows:
        r.setdefault("price_baseline", r.get("price_list"))
        if not r.get("brand"):
            r["brand"] = site.name
    if debug and best_rows:
        print(f"    → 채택: {best_name} ({len(best_rows)}건)")
    return best_rows[:60]
