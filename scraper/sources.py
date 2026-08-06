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
        return _b.lookup(title or "")[1]
    except Exception:
        return ""


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
        mfin = re.search(r'=\s*\$\s?(\d{1,4}(?:\.\d{2})?)', text)
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
_DN_FOR = re.compile(r'\b(?:for|now|just|only)\s+\$([\d,]+(?:\.\d{2})?)', re.I)
_DN_FROM = re.compile(r'\bfrom\s+\$([\d,]+(?:\.\d{2})?)', re.I)
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
        mfin = re.search(r'=\s*\$\s?(\d{1,4}(?:\.\d{2})?)', text)
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
            ms = re.findall(r'\$([\d,]+(?:\.\d{2})?)', title)
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
_DOA_OUR = re.compile(r'class="our-price">\s*\$?([\d,]+(?:\.\d{2})?)')
_DOA_LIST = re.compile(r'class="list-price">\s*\$?([\d,]+(?:\.\d{2})?)')
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
            mfin = re.search(r'=\s*\$\s?(\d{1,4}(?:\.\d{2})?)', desc)
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
SLICKDEALS_FEEDS = [
    ("frontpage", "https://feeds.feedburner.com/SlickdealsnetFP"),
    ("popular", "https://feeds.feedburner.com/SlickdealsnetForums-9"),
]
_SD_RETAILER = re.compile(r'<description>\s*https?://(?:www\.|m\.)?([^/<\s]+)', re.I)
_SD_THUMB = re.compile(r'Thumb Score:\s*([+\-]?\d+)')
_SD_IMG = re.compile(r'<img[^>]+src="([^"]+)"')
_SD_PRICE = re.compile(r'\$([\d,]+(?:\.\d{2})?)')


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
        url=link.split("?")[0] if link else link,
        title=title[:140], brand=_brand_from_title(title),
        image=im.group(1) if im else "",
        price_current=price_current, price_list=price_list,
        coupon_code=coupon_code, code_kind=code_kind,
        community_votes=votes, frontpage=frontpage,
        collection_method="rss",
    )


def fetch_slickdeals(limit: int = 50) -> list[Deal]:
    """슬릭딜 공개 RSS(frontpage/popular)를 읽어 딜로 변환."""
    import xml.etree.ElementTree as _ET
    deals: list[Deal] = []
    seen: set[str] = set()
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
            if not (title and link) or link in seen:
                continue
            seen.add(link)
            deals.append(parse_slickdeals_item(title, link, desc, content,
                                                frontpage=(name == "frontpage")))
            if len(deals) >= limit:
                break
    print(f"[slickdeals] RSS {len(deals)}건 수집 (frontpage/popular)")
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
        r'\$(?P<cur>[\d,]+(?:\.\d{2})?)\D{0,80}?\$(?P<was>[\d,]+(?:\.\d{2})?)',
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


ACTIVE_SOURCES = [
    ("watchlist_retailers", fetch_watchlist),   # 주력: 리테일러 직접 감시
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
