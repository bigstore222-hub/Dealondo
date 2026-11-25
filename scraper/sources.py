"""
sources.py
소스 어댑터. 각 어댑터는 raw 데이터를 가져와 filter_engine.Deal 리스트로 변환한다.

컴플라이언스 원칙(중요):
- DealsOfAmerica: 파트너용 공개 RSS(arssm.xml) 사용 → 허용된 경로.
- Slickdeals: ToS상 스크래핑 금지. 공식 Partner API 키가 있을 때만 활성화되는 슬롯.
  키 없이는 비활성(빈 리스트) — 무단 스크래핑 코드를 넣지 않는다.
- Retailer: 각 사이트 robots.txt를 먼저 확인(check_robots)하고 통과 시에만 세일페이지 파싱.

실제 네트워크 호출은 사용자의 로컬/서버 환경에서 실행된다.
이 파일은 그 실행을 위한 어댑터 골격 + DoA RSS의 동작 구현을 제공한다.
"""
from __future__ import annotations
import os
import socket
import urllib.request
import urllib.robotparser
from urllib.parse import urlparse
from typing import Optional
import xml.etree.ElementTree as ET
import re
from datetime import datetime, timezone
import time

# 어떤 네트워크 호출도 무한 대기하지 않도록 전역 소켓 타임아웃을 건다.
# (타임아웃 인자를 깜빡한 호출이나 라이브러리 내부 호출까지 보호 → CI 행 방지)
socket.setdefaulttimeout(int(os.environ.get("RADAR_SOCKET_TIMEOUT", "20")))

from filter_engine import Deal

USER_AGENT = "HotdealRadar/0.1 (+contact: your-email@example.com)"


# ---------------------------------------------------------------------------
# 0. 컴플라이언스 게이트 — robots.txt 자동 점검
# ---------------------------------------------------------------------------
_robots_cache: dict = {}


def check_robots(url: str, user_agent: str = USER_AGENT) -> bool:
    """대상 URL을 이 UA로 가져와도 되는지 robots.txt 기준으로 판정.

    주의: RobotFileParser.read()는 내부 urlopen에 **타임아웃이 없어** 응답을
    안 주는 서버에서 영원히 멈춘다(실측: CI 30분 행의 원인). 그래서 robots.txt를
    타임아웃 있는 _http_get 으로 직접 받아 parse() 한다. 도메인별로 캐시한다.
    """
    try:
        parsed = urlparse(url)
        host = parsed.netloc
        robots_url = f"{parsed.scheme}://{host}/robots.txt"
        rp = _robots_cache.get(host)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                txt = _http_get(robots_url, timeout=8)
                rp.parse(txt.splitlines())
            except Exception:
                rp = True          # robots 못 읽음 → 판정 불가, 통과(관대)
            _robots_cache[host] = rp
        if rp is True:
            return True
        return rp.can_fetch(user_agent, url)
    except Exception:
        return True


def _http_get(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 1. Deals of America — 공개 RSS (허용). arssm.xml
# ---------------------------------------------------------------------------
DOA_RSS = "https://www.dealsofamerica.com/arssm.xml"

# "$120 → $60", "$60 (was $120)", "50% off" 같은 패턴에서 가격/할인 추출
_PRICE_ARROW = re.compile(r"\$?([\d,]+(?:\.\d+)?)\s*(?:->|→|to)\s*\$?([\d,]+(?:\.\d+)?)", re.I)
_PRICE_WAS = re.compile(r"\$([\d,]+(?:\.\d+)?)\D+was\s+\$([\d,]+(?:\.\d+)?)", re.I)
_PCT_OFF = re.compile(r"(\d{1,3})\s*%\s*off", re.I)


def _f(num: str) -> float:
    return float(num.replace(",", ""))


def _brand_from_title(title: str) -> str:
    """제목에서 '사전에 등록된 실제 브랜드'만 뽑는다. 없으면 빈 문자열.

    애그리게이터(슬릭딜·DoA·DealNews)는 제목만 주므로 브랜드를 제목에서 찾는데,
    첫 단어를 무조건 브랜드로 쓰면 '25%', 'Prime', '5.5"', 'PROVIDES' 같은
    쓰레기가 브랜드로 뜬다(실측). 그래서 사전 매칭에 성공한 것만 브랜드로 인정한다.
    """
    try:
        import brands as _b
        b = _b.lookup(title or "")[1]
    except Exception:
        return ""
    # 'Diesel' 은 패션 브랜드지만 차량용품 제목의 '가솔린·디젤 겸용'에도 나온다
    # (실측: 점프스타터가 브랜드 Diesel → 프리미엄 패션으로 분류됨)
    if b.lower() == "diesel" and re.search(r"\b(gas|engine|jump|starter|truck|generator|fuel|\d+(?:\.\d+)?l)\b", title or "", re.I):
        return ""
    return b


def parse_doa_item(title: str, link: str, desc: str = "") -> Deal:
    text = f"{title} {desc}"
    price_current = price_list = None

    m = _PRICE_ARROW.search(text)
    if m:
        price_list, price_current = _f(m.group(1)), _f(m.group(2))
    else:
        m2 = _PRICE_WAS.search(text)
        if m2:
            price_current, price_list = _f(m2.group(1)), _f(m2.group(2))

    # 할인율(%)만 표기된 경우: 본문에서 단독 $가격을 현재가로 잡고 정가 역산
    if price_list is None:
        mp = _PCT_OFF.search(text)
        if mp:
            if price_current is None:
                ms = re.search(r'\$\s?([\d,]+(?:\.\d+)?)', text)
                if ms:
                    price_current = _f(ms.group(1))
            if price_current is not None:
                pct = int(mp.group(1))
                if 0 < pct < 100:
                    price_list = round(price_current / (1 - pct / 100), 2)

    brand = _brand_from_title(title)

    # 결제창 프로모션 코드 추출.
    # 아마존 셀러 코드(FHJMZNRA 류)는 상품 페이지엔 없고 이런 딜 게시글 본문에만 있다.
    # DoA는 공개 딜 애그리게이터라 여기 실린 코드는 공개(public)로 본다.
    import promocode as _pc
    coupon_code = ""
    code_kind = ""
    codes = _pc.extract(text, subject=title, public_source=True)
    best = _pc.best(codes, shareable_only=True)
    if best:
        coupon_code = best.code
        code_kind = best.kind
        # 본문에 최종가가 "= $24.96" 형태로 명시되면 그걸 최우선으로 쓴다.
        mfin = re.search(r'=\s*\$\s?(\d{1,4}(?:\.\d+)?)', text)
        if mfin:
            coded = _f(mfin.group(1))
            if price_list is None and price_current:
                price_list = price_current      # 코드 전 가격을 정가 기준으로 승격
            price_current = coded
        elif best.percent and price_current and price_list is None:
            # 정가를 아직 모를 때만 '코드 전 가격=현재가'로 보고 코드 %를 적용한다.
            # 정가가 이미 있으면 현재가가 곧 최종가이므로 재적용하면 이중할인이 된다.
            price_list = price_current
            price_current = _pc.apply_to_price(price_current, best)

    return Deal(
        source="dealsofamerica",
        source_tier="T2",
        url=link,
        title=title.strip(),
        brand=brand,
        price_current=price_current,
        price_list=price_list,
        coupon_code=coupon_code,
        code_kind=code_kind,
        collection_method="rss",
    )


# ---------------------------------------------------------------------------
# 1-B. DealNews — 공개 RSS (todays-edition). DoA 보완 애그리게이터.
# ---------------------------------------------------------------------------
DEALNEWS_RSS = "https://www.dealnews.com/rss/todays-edition/"
_DN_FOR = re.compile(r'\b(?:for|now|just|only)\s+\$([\d,]+(?:\.\d+)?)', re.I)
_DN_FROM = re.compile(r'\bfrom\s+\$([\d,]+(?:\.\d+)?)', re.I)
_DN_IMG = re.compile(r"src=['\"](https?://[^'\"]+?\.(?:avif|jpg|jpeg|png|webp)[^'\"]*)['\"]", re.I)
_DN_TITLEATTR = re.compile(r'title=["\']([^"\']{20,})["\']')


def parse_dealnews_item(title: str, link: str, desc: str) -> Deal:
    import html as _html
    import promocode as _pc
    title = _html.unescape(title or "").strip()
    desc_raw = _html.unescape(desc or "")
    # 설명 안의 요약 텍스트(가격·조건이 풀어져 있음)와 이미지 추출
    tm = _DN_TITLEATTR.search(desc_raw)
    snippet = _html.unescape(tm.group(1)) if tm else ""
    text = f"{title} {snippet}"

    price_current = price_list = None
    m = _PRICE_ARROW.search(text)
    if m:
        price_list, price_current = _f(m.group(1)), _f(m.group(2))
    else:
        m2 = _PRICE_WAS.search(text)
        if m2:
            price_current, price_list = _f(m2.group(1)), _f(m2.group(2))
    if price_current is None:
        mf = _DN_FOR.search(title) or _DN_FROM.search(title) or _DN_FOR.search(snippet)
        if mf:
            price_current = _f(mf.group(1))
    if price_list is None:
        mp = _PCT_OFF.search(text)
        if mp and price_current:
            pct = int(mp.group(1))
            if 0 < pct < 100:
                price_list = round(price_current / (1 - pct / 100), 2)

    # 결제창 코드(공개 딜 게시라 public)
    coupon_code = code_kind = ""
    codes = _pc.extract(text, subject=title, public_source=True)
    best = _pc.best(codes, shareable_only=True)
    if best:
        coupon_code, code_kind = best.code, best.kind
        mfin = re.search(r'=\s*\$\s?(\d{1,4}(?:\.\d+)?)', text)
        if mfin:
            if price_list is None and price_current:
                price_list = price_current
            price_current = _f(mfin.group(1))
        elif best.percent and price_current and price_list is None:
            # 정가가 이미 있으면 현재가가 곧 최종가 → 코드 %를 재적용하면 이중할인이 된다.
            price_list = price_current
            price_current = _pc.apply_to_price(price_current, best)

    im = _DN_IMG.search(desc_raw)
    # 브랜드는 제목 전체에서 탐지(첫 단어만 보면 'Tommy Hilfiger'→'Tommy'로 놓친다).
    return Deal(
        source="dealnews", source_tier="T2", url=link,
        title=title[:140], brand=_brand_from_title(title),
        image=im.group(1) if im else "",
        price_current=price_current, price_list=price_list,
        coupon_code=coupon_code, code_kind=code_kind,
        collection_method="rss",
    )


def fetch_dealnews(limit: int = 60) -> list[Deal]:
    """DealNews todays-edition RSS를 읽어 딜로 변환."""
    try:
        raw = _http_get(DEALNEWS_RSS, timeout=12)
    except Exception as e:
        print(f"[dealnews] fetch 실패: {getattr(e,'code',type(e).__name__)}")
        return []
    deals: list[Deal] = []
    try:
        root = ET.fromstring(raw)
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "")
            if title and link:
                deals.append(parse_dealnews_item(title, link, desc))
            if len(deals) >= limit:
                break
    except ET.ParseError as e:
        print(f"[dealnews] RSS 파싱 실패: {e}")
    print(f"[dealnews] {len(deals)}건 수집")
    return deals


# ---------------------------------------------------------------------------
# 1-C. TechBargains — 공개 RSS (600건). 링크가 리테일러로 직접 연결돼 수익화에 유리.
#   item: title(가격) / link(리테일러 URL 직결) / description / vendorname(리테일러)
#         / category / imagelink.  대부분 할인율 표기 없이 가격만 → 큐레이션 소스로 취급.
# ---------------------------------------------------------------------------
TECHBARGAINS_RSS = "https://www.techbargains.com/rss.xml"

# TechBargains 카테고리 → 내부 카테고리
_TB_CAT = {
    "laptops": "electronics", "desktops": "electronics", "gaming": "electronics",
    "cell phones": "electronics", "tvs": "electronics", "electronics": "electronics",
    "tablets": "electronics", "cameras": "electronics", "audio": "electronics",
    "computers": "electronics", "video games": "electronics",
    "clothing & shoes": "premium_fashion", "clothing": "premium_fashion",
    "home & garden": "home", "tools": "home", "kitchen": "home", "furniture": "home",
    "toys": "kids", "baby & kids": "kids", "kids": "kids",
}


def parse_techbargains_item(title, link, desc, vendor, category, imagelink) -> Deal:
    import html as _html
    import promocode as _pc
    title = _html.unescape(title or "").strip()
    desc_clean = re.sub(r"<[^>]+>", " ", _html.unescape(desc or ""))
    text = f"{title} {desc_clean}"

    price_current = price_list = None
    m = _PRICE_ARROW.search(text)
    if m:
        price_list, price_current = _f(m.group(1)), _f(m.group(2))
    else:
        m2 = _PRICE_WAS.search(text)
        if m2:
            price_current, price_list = _f(m2.group(1)), _f(m2.group(2))
    if price_current is None:
        mf = _DN_FOR.search(title) or _DN_FROM.search(title)
        if mf:
            price_current = _f(mf.group(1))
        else:
            ms = re.findall(r'\$([\d,]+(?:\.\d+)?)', title)
            if ms:
                price_current = _f(ms[-1])
    if price_list is None:
        mp = _PCT_OFF.search(text)
        if mp and price_current:
            pct = int(mp.group(1))
            if 0 < pct < 100:
                price_list = round(price_current / (1 - pct / 100), 2)

    coupon_code = code_kind = ""
    codes = _pc.extract(text, subject=title, public_source=True)
    best = _pc.best(codes, shareable_only=True)
    if best:
        coupon_code, code_kind = best.code, best.kind
        if best.percent and price_current and price_list is None:
            price_list = price_current
            price_current = _pc.apply_to_price(price_current, best)

    cat = _TB_CAT.get(_html.unescape(category or "").strip().lower(), "")
    vend = _html.unescape(vendor or "").strip()
    src = f"techbargains:{vend.lower()}" if vend else "techbargains"
    return Deal(
        source=src, source_tier="T2", url=(link or "").strip(),
        title=title[:140], brand=_brand_from_title(title),
        image=(imagelink or "").strip(), category=cat,
        price_current=price_current, price_list=price_list,
        coupon_code=coupon_code, code_kind=code_kind,
        collection_method="rss",
    )


def fetch_techbargains(limit: int = 200) -> list[Deal]:
    """TechBargains 공개 RSS(600건)를 읽어 딜로 변환. 링크는 리테일러 직결."""
    try:
        raw = _http_get(TECHBARGAINS_RSS, timeout=12)
    except Exception as e:
        print(f"[techbargains] fetch 실패: {getattr(e,'code',type(e).__name__)}")
        return []
    deals: list[Deal] = []
    try:
        root = ET.fromstring(raw)
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not (title and link):
                continue
            deals.append(parse_techbargains_item(
                title, link,
                item.findtext("description") or "",
                item.findtext("vendorname") or "",
                item.findtext("category") or "",
                item.findtext("imagelink") or "",
            ))
            if len(deals) >= limit:
                break
    except ET.ParseError as e:
        print(f"[techbargains] RSS 파싱 실패: {e}")
    print(f"[techbargains] {len(deals)}건 수집")
    return deals


# DoA의 파트너 RSS(arssm.xml)는 2023-12-07에 멈춰 있다(실측 — lastBuildDate 고정).
# 대신 카테고리 목록 페이지 HTML을 직접 파싱한다. robots.txt 허용 경로이고,
# 목록에 our-price(현재가)·list-price(정가)가 함께 있어 상세 진입 없이 할인율을 얻는다.
DOA_BASE = "https://www.dealsofamerica.com"
# DoA 상세페이지 진입(코드 확보)에 쓸 최대 시간(초). 느린 상세페이지가 쌓여
# CI가 타임아웃 걸리는 걸 막는다.
DOA_ENRICH_BUDGET_SEC = int(os.environ.get("RADAR_DOA_ENRICH_BUDGET_SEC", "90"))
# 해외직구로 의미 있는 카테고리 위주. hot-deals가 전 카테고리 인기 딜을 모아준다.
DOA_PAGES = [
    "/hot-deals.php", "/amazon-deals.php", "/apparel-deals.php", "/shoes-deals.php",
    "/cell-phone-deals.php", "/laptop-deals.php", "/digital-camera-deals.php",
    "/kitchen-deals.php", "/home-and-garden-deals.php", "/toys-deals.php",
    "/video-games-deals.php", "/furniture-deals.php",
    # 다변화: 도서·미디어·전자 변형으로 카테고리 폭만 넓힌다.
    # (best-buy·kohls 같은 백화점 페이지는 하우스브랜드 잡전자를 끌어와 제외)
    "/books-and-ebooks-deals.php", "/movies-tv-show-deals.php",
    "/lcd-tv-deals.php", "/desktop-deals.php",
]

# 페이지 자체가 카테고리를 알려준다 → 제목 추론보다 정확하게 태깅해
# 브랜드 게이트(H7)가 의류·신발·완구에 제대로 걸리게 한다.
# hot/amazon은 잡카테고리라 비워 두고 제목 추론에 맡긴다.
DOA_PAGE_CATEGORY = {
    "/apparel-deals.php": "premium_fashion",
    "/shoes-deals.php": "sports_outdoor",
    "/toys-deals.php": "kids",
    "/cell-phone-deals.php": "electronics",
    "/laptop-deals.php": "electronics",
    "/digital-camera-deals.php": "electronics",
    "/video-games-deals.php": "electronics",
    "/kitchen-deals.php": "home",
    "/home-and-garden-deals.php": "home",
    "/furniture-deals.php": "home",
    "/books-and-ebooks-deals.php": "etc",
    "/movies-tv-show-deals.php": "etc",
    "/lcd-tv-deals.php": "electronics",
    "/desktop-deals.php": "electronics",
}

_DOA_TITLE = re.compile(
    r'<div class="title">\s*<a href="(' + re.escape(DOA_BASE) +
    r'/[^"]+?\.htm)"[^>]*>([^<]+)</a>', re.S)
_DOA_OUR = re.compile(r'class="our-price">\s*\$?([\d,]+(?:\.\d+)?)')
_DOA_LIST = re.compile(r'class="list-price">\s*\$?([\d,]+(?:\.\d+)?)')
_DOA_IMG = re.compile(r'<img src="([^"]+)"[^>]*alt="[^"]*[Dd]eals?[^"]*"')
# 상세페이지의 할인율 뱃지와 설명(코드 추출용)
_DOA_HOT = re.compile(r'class="hot-deal">\*{0,2}\s*(\d{1,3})\s*%\s*off', re.I)
_DOA_DESC = re.compile(r'class="deal-desc">\s*<p>(.{0,600}?)</p>', re.S)


def _parse_doa_listing(html: str, category: str = "") -> list[dict]:
    """카테고리 목록 페이지에서 (title,url,our,list,img) 딜들을 뽑는다."""
    rows: list[dict] = []
    # 각 딜은 [가격 블록 ... <div class="title"><a ...>제목</a>] 순서.
    # 제목 앵커를 기준으로 잘라, 직전 구간에서 가격/이미지를 찾는다.
    import html as _html
    marks = list(_DOA_TITLE.finditer(html))
    for i, m in enumerate(marks):
        url = m.group(1)
        title = _html.unescape(re.sub(r"\s+", " ", m.group(2)).strip())  # &#39; → '
        lo = marks[i - 1].end() if i else max(0, m.start() - 700)
        pre = html[lo:m.start()]
        om = _DOA_OUR.search(pre)
        if not om:
            continue
        cur = float(om.group(1).replace(",", ""))
        lm = _DOA_LIST.search(pre)
        lst = float(lm.group(1).replace(",", "")) if lm else None
        if lst and lst <= cur:
            lst = None
        im = _DOA_IMG.search(pre)
        rows.append({"title": title, "url": url, "price_current": cur,
                     "price_list": lst, "image": im.group(1) if im else "",
                     "category": category})
    return rows


def fetch_dealsofamerica(limit: int = 600, enrich_top: int = 10,
                         elec_enrich: int = 24, per_page: int = 45) -> list[Deal]:
    """
    DoA 카테고리 목록들을 순회해 딜을 모은다(정가 포함).
    상위 enrich_top개는 상세페이지까지 열어 결제창 프로모션 코드를 확보한다
    (아마존 셀러 코드가 여기 있다). 나머지는 목록 정보만으로 충분하다.

    per_page: 한 카테고리에서 가져올 상한. 한 페이지가 전체 상한을 잡아먹어
    뒤쪽 카테고리가 통째로 누락되는 걸 막아 다양성을 확보한다(실측 문제 수정).
    """
    import promocode as _pc
    seen: set[str] = set()
    rows: list[dict] = []
    for page in DOA_PAGES:
        try:
            html = _http_get(DOA_BASE + page, timeout=12)
        except Exception as e:
            print(f"[dealsofamerica] {page} 실패: {getattr(e,'code',type(e).__name__)}")
            continue
        got = 0
        for r in _parse_doa_listing(html, DOA_PAGE_CATEGORY.get(page, "")):
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            rows.append(r)
            got += 1
            if got >= per_page:
                break
        if len(rows) >= limit:
            break

    # 할인 깊은 순으로 상위만 상세 진입 → 프로모션 코드 확보
    def _disc(r):
        c, l = r["price_current"], r["price_list"]
        return (1 - c / l) if (c and l and l > c) else 0
    rows.sort(key=_disc, reverse=True)

    # 상세페이지를 열어 결제창 코드를 확보할 대상 선정.
    # 코드 딜은 목록상 할인이 낮아 보여(코드 적용 전 가격) 할인정렬로는 놓친다.
    # 사용자 방침: 전자제품 코드딜은 빠짐없이 → 전자 카테고리를 우선 대량 진입.
    elec = [r for r in rows if r.get("category") == "electronics"]
    others = [r for r in rows if r.get("category") != "electronics"]
    to_enrich = elec[:elec_enrich] + others[:enrich_top]

    # 상세 진입은 상품마다 네트워크 왕복이라, 전체 시간 상한을 둔다.
    # 이게 없으면 느린 상세페이지가 쌓여 CI가 30분 타임아웃에 걸린다(실측).
    import time as _t
    enrich_deadline = _t.time() + DOA_ENRICH_BUDGET_SEC
    for r in to_enrich:
        if _t.time() > enrich_deadline:
            break
        try:
            dh = _http_get(r["url"], timeout=8)
        except Exception:
            continue
        hm = _DOA_HOT.search(dh)
        if hm and not r["price_list"]:
            pct = int(hm.group(1))
            if 0 < pct < 100:
                r["price_list"] = round(r["price_current"] / (1 - pct / 100), 2)
        dm = _DOA_DESC.search(dh)
        desc = dm.group(1) if dm else ""
        codes = _pc.extract(f"{r['title']} {desc}", subject=r["title"], public_source=True)
        best = _pc.best(codes, shareable_only=True)
        if best:
            r["coupon_code"] = best.code
            r["code_kind"] = best.kind
            mfin = re.search(r'=\s*\$\s?(\d{1,4}(?:\.\d+)?)', desc)
            if mfin:
                if not r["price_list"]:
                    r["price_list"] = r["price_current"]
                r["price_current"] = float(mfin.group(1))
            elif best.percent and not r["price_list"]:
                # 정가가 없을 때만 코드 %를 적용(정가가 있으면 이중할인 방지)
                r["price_list"] = r["price_current"]
                r["price_current"] = _pc.apply_to_price(r["price_current"], best)

    deals: list[Deal] = []
    for r in rows:
        deals.append(Deal(
            source="dealsofamerica", source_tier="T2", url=r["url"],
            title=r["title"], brand=_brand_from_title(r["title"]),
            image=r.get("image", ""), category=r.get("category", ""),
            price_current=r["price_current"], price_list=r["price_list"],
            coupon_code=r.get("coupon_code", ""), code_kind=r.get("code_kind", ""),
            collection_method="scrape",
        ))
    print(f"[dealsofamerica] {len(DOA_PAGES)}개 카테고리에서 {len(deals)}건 "
          f"(상위 {min(enrich_top, len(deals))}건 코드 확인)")
    return deals


# ---------------------------------------------------------------------------
# 2. Slickdeals — 공개 RSS 신디케이션 피드 (frontpage/popular)
#    HTML 스크래핑(ToS 금지)이 아니라, 슬릭딜이 신디케이션용으로 공개한 RSS를 소비.
#    frontpage = 커뮤니티가 투표로 검증한 '가장 뜨거운 딜' → 아마존 코드딜의 원천.
#    링크는 슬릭딜 딜페이지로 되돌려 준다(피드의 본래 용도 존중 + 출처 표기).
# ---------------------------------------------------------------------------
# 순서가 곧 우선순위다.
#  · hotdeals  = Hot Deals 포럼 새 글. 올라오자마자 잡히는 가장 빠른 경로(피드 25건 ≈ 최근 30분).
#               해외직구는 품절 전에 사는 게 핵심이라 이걸 주 소스로 본다.
#  · frontpage = 추천이 붙어 편집자가 올린 글. 이미 뜬 딜이라 늦을 수 있어 '확신을 높이는 신호'로 쓴다.
#  (Popular 목록은 RSS 경로가 robots.txt 로 막혀 있어 쓰지 않는다 → 대신 추천 증가 속도로 인기를 잰다.)
SLICKDEALS_FEEDS = [
    ("hotdeals", "https://feeds.feedburner.com/SlickdealsnetForums-9"),
    ("frontpage", "https://feeds.feedburner.com/SlickdealsnetFP"),
]
_SD_PUBDATE = re.compile(r"<pubDate>(.*?)</pubDate>", re.S)
_SD_RETAILER = re.compile(r'<description>\s*https?://(?:www\.|m\.)?([^/<\s]+)', re.I)
_SD_THUMB = re.compile(r'Thumb Score:\s*([+\-]?\d+)')
_SD_IMG = re.compile(r'<img[^>]+src="([^"]+)"')
# 소수점 자릿수를 \.\d{2}(정확히 2자리)로 고정했더니 커뮤니티가 캐주얼하게
# 적은 "$37.5" 같은 1자리 가격에서 소수부가 통째로 날아가 $37로 잘렸다
# (실측: "...Carl's Jr Gift Cards $37.5" → 현재가 37, 정가 역산 49.33로
#  둘 다 틀어짐 — 실제로는 37.5/0.75=$50). \.\d+로 자릿수 제한을 풀어 고친다.
_SD_PRICE = re.compile(r'\$([\d,]+(?:\.\d+)?)')

# 슬릭딜 딜 주소를 스레드 번호만 남긴 표준형(https://slickdeals.net/f/<번호>)으로 맞춘다.
# 같은 딜이 RSS(슬러그 포함 주소)와 알림 메일(번호만 확인 가능) 두 경로로 들어와도
# 같은 딜로 인식되게 하려는 것. 표준형 주소도 슬릭딜이 원래 글로 리다이렉트한다.
_SD_THREAD = re.compile(r'slickdeals\.net/f/(\d+)')


def slickdeals_canonical(url: str) -> str:
    m = _SD_THREAD.search(url or "")
    return f"https://slickdeals.net/f/{m.group(1)}" if m else url


def parse_slickdeals_item(title: str, link: str, desc: str, content: str,
                          frontpage: bool) -> Deal:
    import html as _html
    import promocode as _pc
    title = _html.unescape(title or "").strip()
    content = _html.unescape(content or "")
    # 코드/가격 추출용 텍스트는 HTML 태그를 제거해 만든다.
    # (안 그러면 data-couponid 같은 속성을 코드로 오인한다 — 실측 버그)
    clean = re.sub(r"<[^>]+>", " ", content)
    text = f"{title} {clean}"

    # 가격: 제목의 마지막 $금액을 현재가로(대개 "제품명 ... $XX + Free S&H")
    prices = _SD_PRICE.findall(title)
    price_current = float(prices[-1].replace(",", "")) if prices else None
    price_list = None
    m = _PRICE_ARROW.search(text)
    if m:
        price_list, price_current = _f(m.group(1)), _f(m.group(2))
    mp = _PCT_OFF.search(text)
    if price_list is None and mp and price_current:
        pct = int(mp.group(1))
        if 0 < pct < 100:
            price_list = round(price_current / (1 - pct / 100), 2)

    # 결제창 코드(있으면)
    coupon_code = code_kind = ""
    codes = _pc.extract(text, subject=title, public_source=True)
    best = _pc.best(codes, shareable_only=True)
    if best:
        coupon_code, code_kind = best.code, best.kind
        if best.percent and price_current and price_list is None:
            price_list = price_current
            price_current = _pc.apply_to_price(price_current, best)

    # 커뮤니티 투표(Thumb Score) → 점수 신호
    tm = _SD_THUMB.search(content)
    votes = int(tm.group(1)) if tm else None
    # 리테일러 도메인(설명 미리보기에서)
    rm = _SD_RETAILER.search(f"<description>{desc}")
    retailer = rm.group(1).lower() if rm else "slickdeals.net"
    im = _SD_IMG.search(content)

    return Deal(
        source=f"slickdeals:{retailer}", source_tier="T1",
        url=slickdeals_canonical(link.split("?")[0]) if link else link,
        title=title[:140], brand=_brand_from_title(title),
        image=im.group(1) if im else "",
        price_current=price_current, price_list=price_list,
        coupon_code=coupon_code, code_kind=code_kind,
        community_votes=votes, frontpage=frontpage,
        collection_method="rss",
    )


# ---------------------------------------------------------------------------
# 슬릭딜 글 상세 보강 (2026-11-20, 카톡 큐레이션 비교 분석 결과)
#   RSS 50건 중 정가가 있는 건 4건뿐이라(실측) 할인율을 몰라 H7·H8 에서 대부분 탈락했다.
#   Bexel 이 단톡방에 올린 슬릭딜 딜 205건 중 딜보드에 뜬 건 17건 — 원인의 대부분이 이것.
#   글 상세(/f/<id>, robots 허용)의 구조화 데이터에서 판매가·정가·추천 수·브랜드·카테고리·판매처를 읽는다.
#   같은 글은 DB(sd_meta)에 저장해 한 번만 읽는다. 한 실행에 최대 SD_ENRICH_MAX 건.
# ---------------------------------------------------------------------------
SD_ENRICH_MAX = int(os.environ.get("RADAR_SD_ENRICH_MAX", "30"))
SD_ENRICH_SECS = float(os.environ.get("RADAR_SD_ENRICH_SECS", "90"))   # T1 실행 시간 상한(300초) 안에서 쓰는 몫
_SD_META = re.compile(r'<meta class="swiftype" name="([^"]+)" data-type="[^"]+" content="([^"]*)"')
_SD_LIST = re.compile(r'listPrice":"([\d.]+)"')
_SD_GETDEAL = re.compile(r"Get Deal at ([^\"&<]{2,40})")
_SD_STORE = re.compile(r'storeName"[^>]*>(?:<!---->)?([^<"]+)<')


def parse_slickdeals_page(html_text: str) -> dict:
    import html as _html
    meta = dict(_SD_META.findall(html_text))
    out: dict = {}
    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    out["price"] = _num(meta.get("price"))
    lm = _SD_LIST.search(html_text)
    out["list"] = _num(lm.group(1)) if lm else None
    try:
        out["votes"] = int(meta.get("dealScore"))
    except (TypeError, ValueError):
        out["votes"] = None
    out["brand"] = _html.unescape(meta.get("brand") or "").strip()
    out["cat"] = _html.unescape(meta.get("primaryCategory") or "").strip()
    sm = _SD_STORE.search(html_text)
    out["store"] = _html.unescape(sm.group(1)).strip() if sm else ""
    # 2026-11-25: storeName 블록이 'at ' 만 담는 구조로 바뀌어 대부분 비어 있었다(실측 14건 중 10건).
    # 구매 버튼 문구 'Get Deal at <판매처>' 에서 보충한다.
    if not out["store"] or out["store"].lower() in ("at", "slickdeals"):
        gm = _SD_GETDEAL.search(html_text)
        out["store"] = _html.unescape(gm.group(1)).strip() if gm else ""
    out["published_at"] = meta.get("published_at") or ""
    out["expired"] = (meta.get("expired") or "").lower() == "yes"
    out["store_domain"] = store_domain(out["store"])
    out["direct"] = direct_store_url(html_text, out["store_domain"])
    return out


# ---------------------------------------------------------------------------
# 판매처 직접 링크 (2026-11-25)
#   딜보드 구매 버튼의 68%가 슬릭딜 글로 가고 있었다(수수료 0, 영어 페이지 한 번 더).
#   슬릭딜의 'Get Deal' 버튼은 /click 리다이렉트인데 Cloudflare 봇 확인이 걸려 있어
#   따라가지 않는다(우회 금지). 대신 글 상세 HTML 안에 이미 들어 있는 판매처 정보만 쓴다:
#     · 본문 링크의 u2= 파라미터(판매처 원래 주소)
#     · 월마트 리뷰 링크의 상품번호 → walmart.com/ip/<번호>
#     · 판매처가 아마존이고 글 안의 ASIN 이 딱 하나일 때 → amazon.com/dp/<ASIN>
#   확신이 없으면 빈 문자열(= 기존대로 슬릭딜 글 링크). 엉뚱한 상품으로 보내는 것보다 낫다.
# ---------------------------------------------------------------------------
_STORE_DOMAINS = {
    "amazon": "amazon.com", "walmart": "walmart.com", "best buy": "bestbuy.com", "ebay": "ebay.com",
    "woot": "woot.com", "woot!": "woot.com", "target": "target.com", "macy's": "macys.com",
    "costco": "costco.com", "home depot": "homedepot.com", "the home depot": "homedepot.com",
    "lowe's": "lowes.com", "nike": "nike.com", "adidas": "adidas.com", "kohl's": "kohls.com",
    "newegg": "newegg.com", "b&h photo video": "bhphotovideo.com", "b&h": "bhphotovideo.com",
    "nordstrom rack": "nordstromrack.com", "nordstrom": "nordstrom.com", "zappos": "zappos.com",
    "6pm": "6pm.com", "dick's sporting goods": "dickssportinggoods.com", "rei": "rei.com",
    "old navy": "oldnavy.com", "gap": "gap.com", "j.crew": "jcrew.com", "jcpenney": "jcpenney.com",
    "dell": "dell.com", "lenovo": "lenovo.com", "hp": "hp.com", "samsung": "samsung.com",
    "apple": "apple.com", "staples": "staples.com", "office depot": "officedepot.com",
    "sam's club": "samsclub.com", "wayfair": "wayfair.com", "ulta": "ulta.com", "sephora": "sephora.com",
}
_U2 = re.compile(r'u2=([^&"\'\s<>]+)')
_WALMART_REV = re.compile(r"walmart\.com/reviews/product/(\d+)")
_ASIN = re.compile(r"amazon\.com/(?:[^\s\"'<>]*/)?(?:dp|gp/product)/(B0[A-Z0-9]{8})")


def store_domain(store: str) -> str:
    s = (store or "").strip().lower()
    if not s:
        return ""
    if s in _STORE_DOMAINS:
        return _STORE_DOMAINS[s]
    if re.fullmatch(r"[a-z0-9-]+\.(?:com|net|org|co)", s):
        return s
    return ""


def _host(u: str) -> str:
    m = re.match(r"https?://(?:www\.|m\.)?([^/?#]+)", u or "")
    return m.group(1).lower() if m else ""


def direct_store_url(html_text: str, dom: str) -> str:
    import html as _html
    import urllib.parse as _up
    if not dom:
        return ""
    t = _up.unquote(_html.unescape(html_text or ""))
    if dom == "walmart.com":
        ids = set(_WALMART_REV.findall(t))
        if len(ids) == 1:
            return f"https://www.walmart.com/ip/{ids.pop()}"
    if dom == "amazon.com":
        asins = set(_ASIN.findall(t))
        if len(asins) == 1:
            return f"https://www.amazon.com/dp/{asins.pop()}"
    for raw in _U2.findall(_html.unescape(html_text or "")):
        u = _up.unquote(raw)
        h = _host(u)
        if h and (h == dom or h.endswith("." + dom)) and "/reviews" not in u and "/search" not in u:
            return u.split("#")[0]
    return ""


def enrich_slickdeals(deals: list, con=None, max_fetch: int | None = None) -> int:
    """슬릭딜 딜에 글 상세 정보를 붙인다. 붙인 건수를 돌려준다."""
    import json as _json
    max_fetch = SD_ENRICH_MAX if max_fetch is None else max_fetch
    if con is not None:
        con.execute("CREATE TABLE IF NOT EXISTS sd_meta (thread TEXT PRIMARY KEY, meta TEXT, fetched_at TEXT)")
    fetched = done = 0
    robots_ok = None
    t0 = time.time()
    for d in deals:
        if not str(getattr(d, "source", "")).startswith("slickdeals"):
            continue
        m = _SD_THREAD.search(getattr(d, "url", "") or "")
        if not m:
            continue
        th = m.group(1)
        meta = None
        if con is not None:
            row = con.execute("SELECT meta FROM sd_meta WHERE thread=?", (th,)).fetchone()
            if row:
                try:
                    meta = _json.loads(row[0])
                except ValueError:
                    meta = None
                if meta is not None and "store_domain" not in meta:
                    # 2026-11-25 이전 캐시(판매처 링크 없음) — 예산이 남으면 다시 읽고, 아니면 그대로 쓴다
                    if fetched < max_fetch and time.time() - t0 <= SD_ENRICH_SECS:
                        meta = None
        if meta is None:
            if fetched >= max_fetch or time.time() - t0 > SD_ENRICH_SECS:
                continue                     # 못 읽은 글은 다음 실행에서(피드에 30분쯤 머문다)
            if robots_ok is None:
                robots_ok = check_robots(f"https://slickdeals.net/f/{th}")
            if not robots_ok:
                break
            try:
                meta = parse_slickdeals_page(_http_get(f"https://slickdeals.net/f/{th}", timeout=12))
            except Exception as e:
                print(f"[slickdeals] 글 상세 실패 {th}: {getattr(e, 'code', type(e).__name__)}")
                continue
            fetched += 1
            time.sleep(0.4)
            if con is not None:
                con.execute("INSERT OR REPLACE INTO sd_meta(thread, meta, fetched_at) VALUES (?,?,?)",
                            (th, _json.dumps(meta, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
        apply_slickdeals_meta(d, meta)
        done += 1
    if con is not None:
        con.commit()
    if done:
        print(f"[slickdeals] 글 상세 보강 {done}건 (새로 읽음 {fetched}건)")
    return done


def apply_slickdeals_meta(d, meta: dict) -> None:
    """글 상세 값으로 가격·정가·추천·브랜드를 보정한다(RSS 제목 파싱보다 정확)."""
    if not meta:
        return
    p, lp = meta.get("price"), meta.get("list")
    if p and p > 0:
        # 코드가 이미 적용돼 RSS 가격이 더 낮으면 그 값을 존중
        if not d.price_current or not (d.coupon_code and d.price_current < p):
            d.price_current = p
    if lp and d.price_current and lp > d.price_current and not d.price_list:
        d.price_list = lp
    v = meta.get("votes")
    if v is not None:
        d.community_votes = max(v, d.community_votes or v)
    b = meta.get("brand") or ""
    if b and not d.brand:
        d.brand = b
    sig = d.signals if isinstance(getattr(d, "signals", None), dict) else {}
    sig["sd_cat"] = meta.get("cat") or ""
    sig["sd_store"] = meta.get("store") or ""
    if meta.get("brand"):
        sig["sd_brand"] = meta["brand"]
    d.signals = sig
    if meta.get("expired"):
        d.stock_status = "out_of_stock"
    # 판매처가 확인되면 출처를 'slickdeals:<판매처>' 로 바꾸고 직접 링크를 신호로 남긴다.
    dom = meta.get("store_domain") or ""
    if dom and str(getattr(d, "source", "")) == "slickdeals:slickdeals.net":
        d.source = f"slickdeals:{dom}"
    if meta.get("direct"):
        sig["direct_url"] = meta["direct"]


# ---------------------------------------------------------------------------
# 딜보드 슬릭딜 딜 재확인 (2026-11-25)
#   글 상세는 처음 볼 때 한 번만 읽어 캐시했기 때문에, 그 뒤 슬릭딜에서 'Expired' 처리돼도
#   딜보드에는 최대 48시간 '재고 있음'으로 남았다(실측: 24시간 넘게 재확인 안 된 딜 31%).
#   발행할 때마다 '가장 오래전에 확인한' 슬릭딜 딜부터 몇 건씩 다시 읽어 종료 여부·판매처 링크를 갱신한다.
# ---------------------------------------------------------------------------
SD_RECHECK_HOURS = float(os.environ.get("RADAR_SD_RECHECK_HOURS", "6"))
SD_RECHECK_MAX = int(os.environ.get("RADAR_SD_RECHECK_MAX", "12"))
SD_RECHECK_SECS = float(os.environ.get("RADAR_SD_RECHECK_SECS", "35"))


def recheck_slickdeals_rows(rows: list, con) -> dict:
    """딜보드 행(dict) 중 슬릭딜 딜을 재확인한다.
    반환: {스레드번호: meta} — 이번에 새로 읽은 것만. meta["expired"] 가 True 면 종료된 딜."""
    import json as _json
    if con is None:
        return {}
    con.execute("CREATE TABLE IF NOT EXISTS sd_meta (thread TEXT PRIMARY KEY, meta TEXT, fetched_at TEXT)")
    now = datetime.now(timezone.utc)
    due = []
    for r in rows:
        if not str(r.get("source", "")).startswith("slickdeals"):
            continue
        m = _SD_THREAD.search(r.get("url") or "")
        if not m:
            continue
        th = m.group(1)
        row = con.execute("SELECT fetched_at FROM sd_meta WHERE thread=?", (th,)).fetchone()
        try:
            age_h = (now - datetime.fromisoformat(row[0])).total_seconds() / 3600 if row else 1e9
        except (TypeError, ValueError):
            age_h = 1e9
        if age_h >= SD_RECHECK_HOURS:
            due.append((age_h, th))
    due.sort(reverse=True)                       # 가장 오래전에 확인한 것부터
    out: dict = {}
    t0 = time.time()
    for _, th in due[:SD_RECHECK_MAX]:
        if time.time() - t0 > SD_RECHECK_SECS:
            break
        try:
            meta = parse_slickdeals_page(_http_get(f"https://slickdeals.net/f/{th}", timeout=12))
        except Exception as e:
            print(f"[slickdeals] 재확인 실패 {th}: {getattr(e, 'code', type(e).__name__)}")
            continue
        con.execute("INSERT OR REPLACE INTO sd_meta(thread, meta, fetched_at) VALUES (?,?,?)",
                    (th, _json.dumps(meta, ensure_ascii=False), now.isoformat()))
        out[th] = meta
        time.sleep(0.4)
    con.commit()
    if out:
        print(f"[slickdeals] 딜보드 재확인 {len(out)}건 · 종료 {sum(1 for m in out.values() if m.get('expired'))}건")
    return out


def fetch_slickdeals(limit: int = 80) -> list[Deal]:
    """슬릭딜 공개 RSS(Hot Deals 포럼 → 프론트페이지)를 읽어 딜로 변환."""
    from email.utils import parsedate_to_datetime as _pd
    deals: list[Deal] = []
    seen: set[str] = set()
    by_thread: dict[str, Deal] = {}
    for name, url in SLICKDEALS_FEEDS:
        try:
            raw = _http_get(url, timeout=12)
        except Exception as e:
            print(f"[slickdeals] {name} 실패: {getattr(e,'code',type(e).__name__)}")
            continue
        # content:encoded 네임스페이스 때문에 정규식으로 item 단위 파싱
        for block in re.findall(r"<item>(.*?)</item>", raw, re.S):
            def _tag(t):
                mm = re.search(rf"<{t}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{t}>", block, re.S)
                return mm.group(1) if mm else ""
            title = _tag("title")
            link = _tag("link")
            desc = _tag("description")
            cm = re.search(r"<content:encoded>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</content:encoded>",
                           block, re.S)
            content = cm.group(1) if cm else ""
            if not (title and link):
                continue
            key = slickdeals_canonical(link.split("?")[0])
            if key in by_thread:                 # Hot Deals 에서 이미 잡은 글이 프론트페이지에도 올라옴 → 신호만 추가
                d0 = by_thread[key]
                d0.frontpage = d0.frontpage or name == "frontpage"
                d0.signals.setdefault("sd_feeds", []).append(name)
                continue
            if link in seen:
                continue
            seen.add(link)
            d = parse_slickdeals_item(title, link, desc, content, frontpage=(name == "frontpage"))
            pm = _SD_PUBDATE.search(block)
            if pm:
                try:
                    d.posted_at = _pd(pm.group(1).strip()).astimezone(timezone.utc).isoformat()
                except Exception:
                    pass
            d.signals["sd_feeds"] = [name]
            by_thread[key] = d
            deals.append(d)
            if len(deals) >= limit:
                break
    hot = sum(1 for d in deals if "hotdeals" in d.signals.get("sd_feeds", []))
    print(f"[slickdeals] RSS {len(deals)}건 수집 (Hot Deals {hot} · 프론트페이지 {len(deals) - hot})")
    return deals


# ---------------------------------------------------------------------------
# 3. 리테일러 세일페이지 직접 감시 — 컴플라이언스 게이트 통과 시에만
# ---------------------------------------------------------------------------
def fetch_retailer_salepage(name: str, sale_url: str,
                            parser, source_tier: str = "T2") -> list[Deal]:
    """
    name       : 리테일러 이름 (source 라벨)
    sale_url   : 세일/클리어런스 페이지 URL
    parser     : html(str) -> list[dict] 함수. 각 dict는 Deal 필드 일부.
    """
    if not check_robots(sale_url):
        print(f"[{name}] robots.txt 차단 → 스킵 (제휴 네트워크 피드로 대체 권장)")
        return []
    try:
        html = _http_get(sale_url)
    except Exception as e:
        print(f"[{name}] fetch 실패: {e}")
        return []
    deals: list[Deal] = []
    for row in parser(html):
        deals.append(Deal(source=name, source_tier=source_tier,
                          collection_method="scrape", **row))
    return deals


# ---------------------------------------------------------------------------
# 4. 워치리스트 기반 리테일러 순회 — 이 프로젝트의 주력 수집 경로
# ---------------------------------------------------------------------------
def generic_sale_parser(html: str, site) -> list[dict]:
    """
    범용 추출기. generic_parser 의 다중 전략을 먼저 쓰고,
    실패하면 아래 근접 패턴으로 폴백한다.
    """
    try:
        import generic_parser
        rows = generic_parser.parse(html, site)
        if rows:
            return rows
    except Exception:
        pass
    return _proximity_parser(html, site)


def _proximity_parser(html: str, site) -> list[dict]:
    """
    사이트별 전용 파서가 없을 때 쓰는 범용 추출기.
    상품 링크와 가격 쌍을 최대한 보수적으로 긁는다.
    실제 운영에서는 사이트별 파서(PARSERS)를 등록해 정확도를 올린다.
    """
    rows = []
    # $현재가 + $정가(취소선) 인접 패턴
    pat = re.compile(
        r'href="(?P<href>/[^"]{10,150})"[^>]*>(?P<txt>[^<]{5,120})<.{0,400}?'
        r'\$(?P<cur>[\d,]+(?:\.\d+)?)\D{0,80}?\$(?P<was>[\d,]+(?:\.\d+)?)',
        re.S)
    for m in pat.finditer(html):
        try:
            cur = float(m.group("cur").replace(",", ""))
            was = float(m.group("was").replace(",", ""))
        except ValueError:
            continue
        if was <= cur or was <= 0:
            continue
        title = re.sub(r"\s+", " ", m.group("txt")).strip()
        if len(title) < 5:
            continue
        rows.append({
            "url": site.base_url + m.group("href"),
            "title": title[:120],
            "brand": site.name,
            "price_current": cur,
            "price_list": was,
            "price_baseline": was,
        })
        if len(rows) >= 40:
            break
    return rows


# 사이트별 전용 파서 등록소 (parsers.py에서 주입)
try:
    from parsers import REGISTRY as PARSERS
except ImportError:
    PARSERS: dict = {}

# 헤드리스 렌더러 (선택적 의존성 — 없으면 정적 fetch만으로 동작)
try:
    import renderer
except ImportError:
    renderer = None

_renderer_checked = False
_renderer_ok = False


def _renderer_available() -> bool:
    """렌더러 사용 가능 여부 (한 번만 확인하고 캐시)."""
    global _renderer_checked, _renderer_ok
    if renderer is None:
        return False
    if not _renderer_checked:
        _renderer_ok = renderer.available()
        _renderer_checked = True
    return _renderer_ok


def _use_renderer(domain: str) -> bool:
    """이 도메인이 JS 렌더가 필요하고, 이 환경에서 렌더가 가능한가."""
    global _renderer_checked, _renderer_ok
    if renderer is None or not renderer.needs_render(domain):
        return False
    # 전용 파서가 없으면 렌더링해봐야 추출할 수 없다.
    # 렌더링은 페이지당 수 초가 들므로 파서 있는 사이트에만 쓴다.
    if domain not in PARSERS:
        return False
    if not _renderer_checked:
        _renderer_ok = renderer.available()
        _renderer_checked = True
        print(f"[renderer] 헤드리스 렌더링 {'사용 가능' if _renderer_ok else '불가 → 정적 fetch로 폴백'}")
    return _renderer_ok


# 한 번의 fetch_watchlist 호출이 잡아먹을 수 있는 최대 시간(초).
# 이게 없으면 0건 사이트마다 헤드리스 브라우저를 새로 띄우다 2.5시간씩 멈춘다(실측).
# 예산을 넘기면 남은 사이트는 다음 주기로 미룬다.
FETCH_BUDGET_SEC = int(os.environ.get("RADAR_FETCH_BUDGET_SEC", "600"))
# 정적 fetch가 0건일 때 브라우저 렌더로 재시도하는 건 비싸다(사이트당 5~40초).
# 한 주기에 이 횟수까지만 렌더 폴백을 허용한다.
RENDER_FALLBACK_CAP = int(os.environ.get("RADAR_RENDER_FALLBACK_CAP", "12"))
# 한 사이트에 쓸 수 있는 최대 시간(초). Yoox처럼 sale URL이 여러 개인 무거운
# 렌더 사이트가 URL마다 30초씩 잡아먹어 마지막에 멈춘 것처럼 보이는 걸 막는다.
# 이 시간을 넘기면 남은 후보 URL을 포기하고 다음 사이트로 넘어간다.
SITE_BUDGET_SEC = int(os.environ.get("RADAR_SITE_BUDGET_SEC", "50"))


def fetch_watchlist(tiers: tuple[str, ...] = ("T1", "T2"),
                    max_sites: int | None = None) -> list[Deal]:
    """워치리스트에서 직접 크롤 가능한 사이트를 순회하며 세일 페이지를 감시."""
    import watchlist as wl
    import time as _time

    sites = [s for s in wl.crawlable() if s.tier in tiers]
    # Woot 공식 API 키가 있으면 이 함수의 헤드리스 렌더링 경로는 건너뛴다.
    # (fetch_woot_api가 같은 딜을 JSON으로 더 정확하게·더 빨리 가져온다.
    #  둘 다 돌리면 렌더 예산만 낭비하고 같은 딜이 두 경로로 중복 수집된다.)
    if os.environ.get("WOOT_API_KEY"):
        sites = [s for s in sites if s.domain != "woot.com"]
    if max_sites:
        sites = sites[:max_sites]

    deals: list[Deal] = []
    total = len(sites)
    deadline = _time.time() + FETCH_BUDGET_SEC
    render_budget = RENDER_FALLBACK_CAP
    for i, site in enumerate(sites, 1):
        # 시간 예산을 넘기면 남은 사이트는 건너뛴다(다음 주기에 잡힌다).
        if _time.time() > deadline:
            print(f"[{site.tier}] 시간 예산({FETCH_BUDGET_SEC}s) 초과 — "
                  f"남은 {total - i + 1}개 사이트는 다음 주기로 미룹니다.", flush=True)
            break
        # 사이트 진입 시 미리 한 줄 찍어 화면이 살아있게 한다.
        # 렌더링 사이트는 응답에 시간이 걸려, 이게 없으면 멈춘 것처럼 보인다.
        print(f"[{site.tier}] ({i}/{total}) {site.name} 확인 중...", flush=True)
        parser = PARSERS.get(site.domain)
        got_site = 0
        # 후보 주소를 다 실패하면 한 줄로만 보고한다.
        # (사이트당 4줄씩 찍히면 로그에서 진짜 문제가 안 보인다)
        errors: list[str] = []
        site_start = _time.time()
        for url in site.sale_urls:
            # 한 사이트가 후보 URL을 돌며 너무 오래 끌면(무거운 렌더 사이트),
            # 남은 URL을 포기하고 다음 사이트로. 마지막 사이트에서 멈춘 듯 보이는 걸 방지.
            if _time.time() - site_start > SITE_BUDGET_SEC:
                errors.append("시간초과")
                break
            if not check_robots(url):
                errors.append(f"{url.split('/')[-1] or '/'}:robots")
                continue
            # JS 렌더 사이트는 헤드리스 브라우저로, 나머지는 정적 fetch로
            html = ""
            if _use_renderer(site.domain):
                html = renderer.fetch(site.domain, url)
            if not html:
                try:
                    html = _http_get(url, timeout=12)
                except Exception as e:
                    code = getattr(e, "code", type(e).__name__)
                    errors.append(f"{url.split('/')[-1] or '/'}:{code}")
                    continue
            rows = parser(html, site) if parser else generic_sale_parser(html, site)

            # 정적 fetch로 0건이면 렌더링으로 재시도.
            #
            # 상당수 사이트가 요청마다 다른 HTML을 준다(실측: SSENSE는 같은 URL을
            # 연속 호출했을 때 상품 120개 → 0개로 바뀌었다). 봇 완화이거나
            # 지연 하이드레이션인데, 실제 브라우저로 열면 안정적으로 나온다.
            if (not rows and render_budget > 0
                    and renderer is not None and _renderer_available()):
                render_budget -= 1
                # 폴백 렌더는 짧은 타임아웃으로(무한 대기 방지). 정상 사이트는 이 안에 뜬다.
                rendered = renderer.render(url, wait_selector=None, timeout_ms=15000)
                if rendered:
                    rows = parser(rendered, site) if parser else generic_sale_parser(rendered, site)
                    if rows:
                        print(f"  [{site.name}] 렌더링으로 {len(rows)}건 확보")
            for row in rows:
                deals.append(Deal(source=site.domain, source_tier=site.tier,
                                  collection_method="scrape", **row))
            got_site += len(rows)
            if rows:
                break        # 한 경로에서 건졌으면 다음 사이트로
        if got_site:
            print(f"[{site.tier}] {site.name:22s} {got_site}건")
        else:
            why = f"  ({', '.join(errors[:3])})" if errors else "  (파서가 상품을 못 찾음)"
            print(f"[{site.tier}] {site.name:22s} 0건{why}")
    return deals


# ---------------------------------------------------------------------------
# 소스 레지스트리 — main에서 순회
# ---------------------------------------------------------------------------
def fetch_email() -> list[Deal]:
    """봇 차단 사이트(Macy's·The Outnet 등)를 이메일 뉴스레터로 우회."""
    try:
        from sources_email import fetch_email_deals
        return fetch_email_deals()
    except ImportError:
        return []


def fetch_slickdeals_alerts() -> list[Deal]:
    """슬릭딜 딜 알림 메일(사용자가 슬릭딜에서 직접 설정한 키워드 알림).
    RADAR_IMAP_USER/PASS 가 없으면 조용히 빈 리스트."""
    try:
        from sources_email import fetch_slickdeals_alert_deals
        return fetch_slickdeals_alert_deals()
    except ImportError:
        return []


def fetch_woot() -> list[Deal]:
    """Woot 공식 Developer API (WOOT_API_KEY 없으면 조용히 빈 리스트 — 기존
    fetch_watchlist의 헤드리스 렌더링 경로가 계속 Woot을 커버한다)."""
    try:
        from sources_woot_api import fetch_woot_api
        return fetch_woot_api()
    except ImportError:
        return []


ACTIVE_SOURCES = [
    ("watchlist_retailers", fetch_watchlist),   # 주력: 리테일러 직접 감시
    ("woot_api", fetch_woot),                   # Woot 공식 API (키 있을 때만)
    ("email_newsletter", fetch_email),          # 봇 차단 사이트 대응
    ("dealsofamerica", fetch_dealsofamerica),
    ("slickdeals", fetch_slickdeals),
]


def collect_all(limit_per_source: int = 50) -> list[Deal]:
    all_deals: list[Deal] = []
    for name, fn in ACTIVE_SOURCES:
        try:
            got = fn()
        except Exception as e:
            print(f"[{name}] 수집 오류: {type(e).__name__}: {e}")
            got = []
        print(f"[{name}] 합계 {len(got)}건\n")
        all_deals.extend(got)
    return all_deals
