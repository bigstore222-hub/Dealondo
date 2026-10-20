"""
product_page.py — 상품 페이지 주소 하나로 가격·정가·브랜드·상품명을 읽어온다.

읽는 순서(먼저 성공한 것을 쓴다)
  1) 딜온도 딜보드(deals.json)에 이미 잡힌 상품이면 그 데이터(가장 정확: 한국 도착가·국내가 포함)
  2) Shopify 쇼핑몰이면 공개 상품 JSON(/products/<handle>.js) — 판매가·정가(compare_at_price)·재고
  3) 상품 페이지에 박힌 구조화 데이터(JSON-LD Product, og/product 메타태그)
  4) 정가는 페이지 속 흔한 키(listPrice·compareAtPrice·originalPrice·regularPrice·StrikethroughPrice)에서 보충

봇 차단이 강한 대형몰(노드스트롬랙·메이시스·아마존 등)은 2)~4)가 막힐 수 있다.
그 경우 가격을 못 읽었다고 돌려주고, 텔레그램에서 가격만 답장으로 받는다.
"""
from __future__ import annotations
import html as htmlmod, json, re, urllib.parse, urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
WIRELESS = re.compile(r"\b(bluetooth|wireless|earbuds?|airpods|headphones?|smart ?watch|apple watch|"
                      r"galaxy watch|pixel watch|fitbit|garmin|tws|earphones?)\b", re.I)


def _get(url: str, timeout: int = 15) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
                                               "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def _num(v) -> float | None:
    try:
        f = float(str(v).replace(",", "").replace("$", "").strip())
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def domain(url: str) -> str:
    return re.sub(r"^www\.", "", urllib.parse.urlparse(url).netloc.lower())


def category_of(title: str) -> str:
    # 무선 통신 기능이 있는 전자기기만 미국발이어도 면세 $150 (목록통관 배제). 그 외는 $200.
    return "electronics" if WIRELESS.search(title or "") else "etc"


def _shopify(url: str) -> dict | None:
    p = urllib.parse.urlparse(url)
    m = re.search(r"/products/([^/?#]+)", p.path)
    if not m:
        return None
    base = f"{p.scheme}://{p.netloc}/products/{m.group(1)}.js"
    try:
        st, body = _get(base)
        j = json.loads(body)
    except Exception:
        return None
    vid = urllib.parse.parse_qs(p.query).get("variant", [None])[0]
    variants = j.get("variants") or []
    v = next((x for x in variants if str(x.get("id")) == str(vid)), None) \
        or next((x for x in variants if x.get("available")), None) or (variants[0] if variants else {})
    price = (v.get("price") or j.get("price") or 0) / 100
    cmp_ = (v.get("compare_at_price") or j.get("compare_at_price") or 0) / 100
    return {"title": j.get("title"), "brand": j.get("vendor") or "",
            "price_current": price or None, "price_list": cmp_ if cmp_ > price else None,
            "currency": "USD", "in_stock": bool(j.get("available")), "how": "shopify"}


def _jsonld_products(html: str):
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(htmlmod.unescape(block.strip()))
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x); continue
            if not isinstance(x, dict):
                continue
            if "@graph" in x:
                stack.extend(x["@graph"] if isinstance(x["@graph"], list) else [x["@graph"]])
            t = x.get("@type")
            if t == "Product" or (isinstance(t, list) and "Product" in t):
                yield x
            if isinstance(x.get("hasVariant"), list):
                stack.extend(x["hasVariant"])


def _from_html(html: str) -> dict:
    out: dict = {}
    for prod in _jsonld_products(html):
        out.setdefault("title", prod.get("name"))
        b = prod.get("brand")
        out.setdefault("brand", (b.get("name") if isinstance(b, dict) else b) or "")
        offers = prod.get("offers") or {}
        offers = offers if isinstance(offers, list) else [offers]
        for o in offers:
            if not isinstance(o, dict):
                continue
            pr = _num(o.get("price")) or _num(o.get("lowPrice"))
            if pr and "price_current" not in out:
                out["price_current"] = pr
                out["currency"] = o.get("priceCurrency") or "USD"
                out["in_stock"] = "OutOfStock" not in str(o.get("availability", ""))
            for spec in (o.get("priceSpecification") or []) if isinstance(o.get("priceSpecification"), list) else []:
                if "Strikethrough" in str(spec.get("priceType", "")) or "ListPrice" in str(spec.get("priceType", "")):
                    lp = _num(spec.get("price"))
                    if lp:
                        out.setdefault("price_list", lp)
        if out.get("price_current"):
            break
    def meta(prop):
        m = re.search(rf'<meta[^>]+(?:property|name)=["\']{prop}["\'][^>]+content=["\']([^"\']+)', html, re.I) \
            or re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{prop}["\']', html, re.I)
        return htmlmod.unescape(m.group(1)) if m else None
    out.setdefault("title", meta("og:title") or "")
    if not out.get("price_current"):
        pr = _num(meta("product:price:amount") or meta("og:price:amount"))
        if pr:
            out["price_current"] = pr
            out["currency"] = meta("product:price:currency") or meta("og:price:currency") or "USD"
    if not out.get("price_list") and out.get("price_current"):
        cands = []
        for key in ("listPrice", "compareAtPrice", "compare_at_price", "originalPrice", "regularPrice",
                    "wasPrice", "strikethroughPrice", "msrp"):
            for m in re.finditer(rf'"{key}"\s*:\s*\{{?\s*(?:"(?:value|amount)"\s*:\s*)?"?([\d,]+(?:\.\d+)?)', html):
                v = _num(m.group(1))
                if v:
                    cands.append(v / 100 if v > out["price_current"] * 100 else v)
        good = [v for v in cands if out["price_current"] < v < out["price_current"] * 30]
        if good:
            out["price_list"] = max(set(good), key=good.count)
    return out


def read(url: str) -> dict:
    """{title, brand, price_current, price_list, currency, in_stock, how, error?}"""
    info = _shopify(url)
    if info and info.get("price_current"):
        return info
    try:
        st, html = _get(url)
    except Exception as e:
        return {"error": f"페이지를 열 수 없어요({type(e).__name__}) — 봇 차단 사이트일 수 있어요"}
    out = _from_html(html)
    out["how"] = "page"
    if not out.get("price_current"):
        out["error"] = "페이지에서 가격을 찾지 못했어요"
    return out
