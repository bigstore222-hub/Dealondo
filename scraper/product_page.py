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
    img = (v.get("featured_image") or {}).get("src") if isinstance(v.get("featured_image"), dict) else None
    img = img or j.get("featured_image") or ((j.get("images") or [None])[0])
    if img and img.startswith("//"):
        img = "https:" + img
    return {"title": j.get("title"), "brand": j.get("vendor") or "", "image": img,
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


def _main_products(html: str, url: str) -> list:
    """
    페이지의 구조화 데이터 중 '이 상품'만 고른다.
    추천 상품까지 Product 로 넣어두는 몰(6pm·Zappos 등)이 있어, 아무거나 집으면 엉뚱한 가격이 나온다.
    상품 번호(주소 속 숫자)나 페이지 제목과 맞는 것만 쓰고, 여러 개인데 맞는 게 없으면 쓰지 않는다.
    """
    prods = list(_jsonld_products(html))
    if len(prods) <= 1:
        return prods
    ids = [x for x in re.findall(r"\d{5,}", urllib.parse.urlparse(url or "").path)]
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    page_title = htmlmod.unescape(m.group(1)).lower() if m else ""
    def hit(pr):
        offers = pr.get("offers") or {}
        offers = offers if isinstance(offers, list) else [offers]
        urls = " ".join(str(o.get("url", "")) for o in offers if isinstance(o, dict)) + " " + str(pr.get("url", "")) \
            + " " + str(pr.get("@id", "")) + " " + str(pr.get("sku", "")) + " " + str(pr.get("productID", ""))
        if ids and any(i in urls for i in ids):
            return True
        name = str(pr.get("name") or "").lower().strip()
        return bool(name) and len(name) > 6 and name in page_title
    return [pr for pr in prods if hit(pr)]


def _from_html(html: str, url: str = "") -> dict:
    out: dict = {}
    for prod in _main_products(html, url):
        out.setdefault("title", prod.get("name"))
        im = prod.get("image")
        im = im[0] if isinstance(im, list) and im else im
        im = im.get("url") if isinstance(im, dict) else im
        if isinstance(im, str) and im.startswith("http"):
            out.setdefault("image", im)
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
        m = re.search(rf'<meta[^>]+(?:property|name)=["\']{prop}["\'][^>]+content=(?:"([^"]*)"|\'([^\']*)\')', html, re.I) \
            or re.search(rf'<meta[^>]+content=(?:"([^"]*)"|\'([^\']*)\')[^>]+(?:property|name)=["\']{prop}["\']', html, re.I)
        return htmlmod.unescape(m.group(1) or m.group(2) or "") if m else None
    out.setdefault("title", meta("og:title") or "")
    if not out.get("image") and (meta("og:image") or "").startswith("http"):
        out["image"] = meta("og:image")
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


BLOCK_RE = re.compile(r"unusual activity|access denied|are you a (human|robot)|verify you are (a )?human|captcha|"
                      r"pardon our interruption|request blocked|automated traffic|bot detection", re.I)

# 브라우저 화면에서 '이 상품'의 판매가·정가·이름·사진을 읽는 스크립트.
# 구조화 데이터가 없거나 추천 상품과 섞인 몰을 위해, 화면에 보이는 가격 블록을 직접 본다.
_JS = r"""
(known) => {
  const num = s => { const m = (s||'').replace(/,/g,'').match(/\$\s?(\d+(?:\.\d{1,2})?)/); return m ? parseFloat(m[1]) : null; };
  const q = sel => document.querySelector(sel);
  const txt = el => el ? (el.textContent||'').trim() : '';
  const out = {};
  // 판매가
  const ip = q('[itemprop=price]');
  if (ip) out.price = parseFloat(ip.getAttribute('content')) || num(txt(ip));
  const az = q('#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen') || q('#corePrice_feature_div .a-offscreen')
          || q('.priceToPay .a-offscreen') || q('#priceblock_dealprice') || q('#priceblock_ourprice');
  if (!out.price && az) out.price = num(txt(az));
  const tw = q('#twister-plus-price-data-price');
  if (!out.price && tw) out.price = parseFloat(tw.value) || null;
  const ap = q('#apex_desktop [data-pricetopay-savings-label]') || q('#corePrice_feature_div .a-price .a-offscreen');
  if (!out.price && ap) out.price = num(txt(ap));
  if (!out.price && known) out.price = known;
  // 화면 낭독용 문구: "Original price was: $330.00. Current price is: $99.00." (Zappos 등)
  const all = (document.body ? document.body.textContent : '').replace(/,/g,'');
  const op = all.match(/Original price(?: was)?:?\s*\$\s?(\d+(?:\.\d{1,2})?)\.?\s*Current price(?: is)?:?\s*\$\s?(\d+(?:\.\d{1,2})?)/i);
  if (op && out.price && Math.abs(parseFloat(op[2]) - out.price) < 0.01) out.list = parseFloat(op[1]);
  // 정가(아마존: List Price / Typical price)
  const azl = q('#corePriceDisplay_desktop_feature_div .basisPrice .a-offscreen') || q('.basisPrice .a-offscreen')
           || q('#corePrice_desktop .a-text-price .a-offscreen');
  if (azl) out.list = num(txt(azl));
  // 판매가가 적힌 작은 블록 안에서 더 큰 금액(취소선·MSRP·Was)을 정가로 본다
  if (out.price && !out.list) {
    const want = out.price;
    const els = Array.from(document.querySelectorAll('body *')).filter(e => {
      if (e.children.length > 3) return false;
      const t = (e.textContent||'').replace(/,/g,'');
      return t.length < 60 && Math.abs((num(t)||-1) - want) < 0.01;
    }).slice(0, 5);
    for (const e of els) {
      let a = e;
      for (let i = 0; i < 3 && a.parentElement; i++) {
        a = a.parentElement;
        const t = (a.textContent||'').replace(/,/g,'');
        if (t.length > 260) break;
        const vals = (t.match(/\$\s?\d+(?:\.\d{1,2})?/g)||[]).map(num).filter(v => v > want * 1.05 && v < want * 30);
        if (vals.length) { out.list = Math.max(...vals); break; }
      }
      if (out.list) break;
    }
  }
  const meta = p => { const m = q(`meta[property="${p}"]`) || q(`meta[name="${p}"]`); return m ? m.getAttribute('content') : null; };
  out.title = txt(q('#productTitle')) || meta('og:title') || txt(q('h1')) || document.title;
  const by = txt(q('#bylineInfo'));
  const bm = by.match(/Visit the (.+?) Store/i) || by.match(/Brand:\s*(.+)/i);
  if (bm) out.brand = bm[1].trim();
  const hi = q('#landingImage');
  out.image = (hi && (hi.getAttribute('data-old-hires') || hi.getAttribute('src'))) || meta('og:image');
  out.body = (document.body ? document.body.innerText : '').slice(0, 3000);
  return out;
}
"""


def render(url: str, wait_ms: int = 3500) -> dict:
    """
    실제 브라우저(Playwright)로 페이지를 열어 읽는다. 자바스크립트로 가격을 그리는 몰용.
    자동 접속을 막는 사이트는 막힌 그대로 둔다(우회하지 않는다) → {"blocked": True}
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"error": "브라우저(Playwright)가 설치돼 있지 않아요"}
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(locale="en-US")
            r = pg.goto(url, wait_until="domcontentloaded", timeout=35000)
            pg.wait_for_timeout(wait_ms)
            status = r.status if r else 0
            html = pg.content()
            known = _from_html(html, url).get("price_current")
            js = pg.evaluate(_JS, known)
            b.close()
    except Exception as e:
        return {"error": f"페이지를 열지 못했어요({type(e).__name__})"}
    body = js.pop("body", "")
    if status >= 400 or (BLOCK_RE.search(body[:1500]) and not js.get("price")):
        return {"blocked": True, "error": "자동 접속을 막는 사이트예요"}
    out = _from_html(html, url)
    if js.get("price"):
        out["price_current"] = js["price"]
        out.setdefault("currency", "USD")
    if js.get("list") and out.get("price_current") and js["list"] > out["price_current"]:
        out["price_list"] = js["list"]
    for k in ("title", "brand", "image"):
        if js.get(k) and not out.get(k):
            out[k] = js[k]
    if "in_stock" not in out and re.search(r"out of stock|sold out|currently unavailable", body, re.I):
        out["in_stock"] = False
    return out


def slug_title(url: str) -> str:
    """주소 속 상품 이름(…/theory-carissa-wool-blazer/8429298)을 읽는다. 막힌 사이트에서 상품명만이라도."""
    best = ""
    for seg in urllib.parse.urlparse(url).path.split("/"):
        seg = re.sub(r"\.(html?|aspx?|php)$", "", urllib.parse.unquote(seg))
        if seg.count("-") >= 2 and re.search(r"[a-zA-Z]{3,}", seg) and len(seg) > len(best):
            best = seg
    words = [w for w in re.split(r"[-_]+", best) if w and not re.fullmatch(r"\d{4,}|[a-z]?\d+[a-z]?\d*", w.lower())]
    words = [w for w in words if w.lower() not in ("regular", "plus", "petite", "nordstrom", "exclusive")]
    fix = lambda w: w.upper() if re.fullmatch(r"(ii|iii|iv|vi|vii|viii|ix|xl|xs|xxl)", w.lower()) else \
        (w.capitalize() if w.islower() else w)
    return " ".join(fix(w) for w in words)


def clean_title(title: str) -> tuple[str, str]:
    """'Women's Stuart Weitzman Loralei Mary Jane | 6pm' → ('Stuart Weitzman Loralei Mary Jane', "women's")"""
    t = re.split(r"\s+[|–—]\s+|\s+-\s+(?=[^-]*$)(?=.{0,30}$)", (title or "").strip())[0]
    t = re.sub(r"^amazon\.com\s*:\s*", "", t, flags=re.I)
    m = re.match(r"^((?:women|men|girl|boy|kid|unisex)'?s?'?)\s+", t, re.I)
    return (t[m.end():] if m else t).strip(), (m.group(1).lower() if m else "")


def read(url: str) -> dict:
    """
    {title, brand, price_current, price_list, currency, in_stock, image, how, gender?, blocked?, error?}
    1) Shopify 공개 JSON → 2) 페이지 HTML(구조화 데이터) → 3) 실제 브라우저로 열어 화면의 가격 블록
    자동 접속을 막는 사이트는 상품명만 주소에서 읽고 가격은 비워 둔다(텔레그램에서 가격만 물어본다).
    """
    info = _shopify(url)
    if info and info.get("price_current"):
        return info
    out: dict = {}
    try:
        st, html = _get(url)
        out = _from_html(html, url)
        out["how"] = "page"
    except Exception:
        out = {}
    if not out.get("price_current") or not out.get("price_list"):
        r = render(url)
        if r.get("price_current"):
            r["how"] = "browser"
            out = {**out, **{k: v for k, v in r.items() if v not in (None, "")}}
        elif not out.get("price_current"):
            out = {**r, **{k: v for k, v in out.items() if v}}
    title, g = clean_title(out.get("title") or "")
    if not title or out.get("blocked") or not out.get("price_current"):
        # 가격도 못 읽은 페이지의 제목은 오류·안내 화면 제목일 수 있어 주소 속 상품명을 먼저 쓴다
        title = slug_title(url) or (title if title and not BLOCK_RE.search(title)
                                    and title.lower() not in ("nordstrom", "access denied") else "")
        # 주소 끝의 성별 단어(…-boot-women)는 상품명에서 빼고 성별로만 쓴다
        m = re.search(r"\s+(Women|Men|Womens|Mens|Kids|Girls|Boys)$", title)
        if m:
            g = g or m.group(1).lower()
            title = title[:m.start()]
    out["title"] = title
    if g:
        out["gender"] = g
    if not out.get("price_current"):
        out.setdefault("error", "페이지에서 가격을 찾지 못했어요")
    return out
