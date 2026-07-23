"""
sources_feed.py — 제휴 상품피드 수집 (봇차단 사이트 합법 우회)

메이시스·아웃넷·컬럼비아처럼 봇 보호가 강한 사이트는 크롤링이 막힌다(403).
이들은 대부분 Rakuten Advertising / CJ / Impact 같은 제휴 네트워크에
**상품 피드**를 올린다. 피드는 전 상품의 이름·브랜드·정가·세일가·링크·이미지를
구조화된 파일(CSV/TSV/XML)로 준다. 승인된 제휴사는 이걸 합법적으로 내려받는다.

장점:
  - 100% 합법 (봇 우회가 아니라 제공된 데이터)
  - 완전한 데이터 (전 상품 + 현재가 + 세일가 + 재고)
  - 클릭 시 커미션까지 (수익화와 직결)

설정:
  data/feeds.csv 에 승인받은 피드를 한 줄씩 등록한다.
    name,retailer,url,format,category
  format 은 auto/csv/tsv/xml. category 는 기본 카테고리(비우면 제목 추론).
  승인 전에는 파일이 비어 있어도 되고, 이 소스는 조용히 0건을 반환한다.
"""
from __future__ import annotations
import csv
import io
import os
import re
import urllib.request
import xml.etree.ElementTree as ET

from filter_engine import Deal

_CFG = os.path.join(os.path.dirname(__file__), "..", "data", "feeds.csv")
UA = "Mozilla/5.0 (compatible; DealondoBot/0.1; affiliate feed reader)"

# 피드마다 헤더명이 제각각이라 유사어를 모아 매핑한다.
_FIELD_ALIASES = {
    "title": ["title", "name", "product_name", "productname", "product name",
              "description", "product_title"],
    "brand": ["brand", "manufacturer", "brand_name", "advertiser_name"],
    "url": ["link", "url", "buy_url", "product_url", "clickurl", "click_url",
            "linkurl", "deep_link", "aw_deep_link"],
    "image": ["image", "image_link", "image_url", "imageurl", "large_image",
              "merchant_image_url", "aw_image_url"],
    "price": ["price", "retail_price", "regular_price", "list_price", "msrp",
              "original_price", "was_price"],
    "sale_price": ["sale_price", "saleprice", "discount_price", "special_price",
                   "current_price", "search_price", "store_price", "final_price"],
    "category": ["category", "product_type", "google_product_category",
                 "merchant_category", "primary_category"],
    "in_stock": ["availability", "in_stock", "stock_status", "instock"],
}


def _norm_key(k: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", (k or "").lower().replace(" ", "_"))


def _build_map(headers: list[str]) -> dict[str, str]:
    """실제 헤더 → 표준 필드명 매핑."""
    norm = {_norm_key(h): h for h in headers}
    out: dict[str, str] = {}
    for std, aliases in _FIELD_ALIASES.items():
        for a in aliases:
            if _norm_key(a) in norm:
                out[std] = norm[_norm_key(a)]
                break
    return out


def _money(v) -> float | None:
    if v is None:
        return None
    m = re.search(r"(\d[\d,]*\.?\d*)", str(v))
    return float(m.group(1).replace(",", "")) if m else None


def _rows_from_csv(text: str, delim: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    if not reader.fieldnames:
        return []
    fmap = _build_map(list(reader.fieldnames))
    out = []
    for r in reader:
        out.append({std: r.get(col) for std, col in fmap.items()})
    return out


def _rows_from_xml(text: str) -> list[dict]:
    # Google Merchant / RSS 형식: <item> 하위에 g:title, g:price 등.
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter():
        if not item.tag.endswith("item"):
            continue
        rec: dict[str, str] = {}
        for child in item:
            tag = child.tag.split("}")[-1].lower()   # 네임스페이스 제거
            rec[tag] = (child.text or "").strip()
        fmap = _build_map(list(rec.keys()))
        out.append({std: rec.get(col) for std, col in fmap.items()})
    return out


def _load_config() -> list[dict]:
    try:
        with open(_CFG, encoding="utf-8") as f:
            out = []
            for r in csv.DictReader(f):
                name = (r.get("name") or "").strip()
                url = (r.get("url") or "").strip()
                if not url or name.startswith("#") or url.startswith("#"):
                    continue                 # 주석줄·빈줄 건너뛰기
                out.append(r)
            return out
    except FileNotFoundError:
        return []


def _fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    # 피드는 UTF-8이 대부분이나, 간혹 latin-1. 관대하게 디코드.
    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def _parse_feed(text: str, fmt: str) -> list[dict]:
    fmt = (fmt or "auto").lower()
    head = text.lstrip()[:200].lower()
    if fmt == "xml" or (fmt == "auto" and head.startswith("<")):
        return _rows_from_xml(text)
    if fmt == "tsv":
        return _rows_from_csv(text, "\t")
    if fmt == "csv":
        return _rows_from_csv(text, ",")
    # auto: 첫 줄의 구분자 추정
    first = text.splitlines()[0] if text.splitlines() else ""
    if first.count("\t") > first.count(","):
        return _rows_from_csv(text, "\t")
    if "|" in first and first.count("|") > first.count(","):
        return _rows_from_csv(text, "|")
    return _rows_from_csv(text, ",")


def fetch_feeds(max_per_feed: int = 400) -> list[Deal]:
    """
    등록된 모든 제휴 피드를 읽어 '세일 중'인 상품만 Deal로 만든다.
    세일가(sale_price)가 정가(price)보다 낮은 항목만 담아 볼륨을 줄인다.
    """
    cfgs = _load_config()
    if not cfgs:
        return []

    deals: list[Deal] = []
    for cfg in cfgs:
        name = (cfg.get("name") or cfg.get("retailer") or "affiliate").strip()
        retailer = (cfg.get("retailer") or name).strip()
        cat_default = (cfg.get("category") or "").strip()
        try:
            text = _fetch(cfg["url"].strip())
        except Exception as e:
            print(f"[feed] {name} 내려받기 실패: {type(e).__name__}: {str(e)[:80]}")
            continue

        rows = _parse_feed(text, cfg.get("format", "auto"))
        got = 0
        for r in rows:
            title = (r.get("title") or "").strip()
            price = _money(r.get("price"))
            sale = _money(r.get("sale_price"))
            url = (r.get("url") or "").strip()
            if not (title and url):
                continue
            # 세일가가 없거나 정가와 같으면 스킵(할인 딜만).
            cur, lst = None, None
            if sale and price and sale < price:
                cur, lst = sale, price
            elif sale and not price:
                cur = sale
            elif price and not sale:
                cur = price
            if cur is None:
                continue
            # 재고 없는 항목 제외
            stock = (r.get("in_stock") or "").lower()
            if stock and re.search(r"out|no|0|false|unavailable", stock):
                continue
            deals.append(Deal(
                source=retailer, source_tier="T2", url=url,
                title=title[:140],
                brand=(r.get("brand") or title.split()[0] or "").strip()[:60],
                image=(r.get("image") or "").strip(),
                category=cat_default,
                price_current=cur, price_list=lst,
                collection_method="affiliate_feed",
            ))
            got += 1
            if got >= max_per_feed:
                break
        print(f"[feed] {name} {got}건 (세일 상품)")
    return deals


if __name__ == "__main__":
    ds = fetch_feeds()
    print(f"총 {len(ds)}건")
    for d in ds[:5]:
        print(" ", d.source, d.brand, d.title[:40], d.price_current, "<-", d.price_list)
