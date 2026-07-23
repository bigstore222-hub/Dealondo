"""
renderer.py — JS 렌더 사이트용 헤드리스 브라우저 어댑터

Amazon·eBay·Woot·Nike 등은 상품을 자바스크립트로 나중에 그린다.
정적 fetch로는 빈 껍데기만 오므로(실측: Amazon 골드박스 556KB HTML에 가격 9개),
실제 브라우저를 띄워 JS 실행이 끝난 뒤의 DOM을 가져와야 한다.

Playwright가 없거나 실행 불가한 환경에서는 조용히 비활성화되고,
파이프라인은 정적 수집만으로 계속 동작한다(선택적 의존성).

설치:
    pip install playwright
    playwright install chromium
    playwright install-deps chromium      # 리눅스에서 시스템 라이브러리 필요
"""
from __future__ import annotations
import os

LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]

# 지연 로딩 상품을 끌어내리기 위한 스크롤 설정.
# 늘리면 수집량이 늘지만 사이트당 소요 시간도 비례해 늘어난다.
# (Amazon 골드박스는 가격이 지연 로딩이라 스크롤 수가 수집량을 좌우한다)
SCROLL_STEPS = int(os.environ.get("RADAR_SCROLL_STEPS", "3"))
SCROLL_WAIT_MS = int(os.environ.get("RADAR_SCROLL_WAIT_MS", "500"))

# 봇 탐지를 자극하지 않도록 일반 브라우저 UA를 쓴다.
# (우회 목적이 아니라, 헤드리스 기본 UA가 일부 사이트에서 오작동을 유발하기 때문)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_available: bool | None = None

# 디스크 여유 하한.
# 헤드리스 브라우저는 페이지마다 임시 캐시를 쓴다. 여유가 없으면
# 렌더링뿐 아니라 DB 쓰기까지 실패한다(실측: OSError Errno 28 로 사이클 중단).
MIN_FREE_MB = int(os.environ.get("RADAR_MIN_FREE_MB", "1500"))


def free_mb(path: str | None = None) -> int:
    """남은 디스크 공간(MB)."""
    import shutil
    try:
        return shutil.disk_usage(path or os.path.expanduser("~")).free // (1024 * 1024)
    except Exception:
        return 10 ** 6          # 확인 불가면 제한하지 않는다


def cleanup_temp() -> int:
    """
    브라우저가 남긴 임시 폴더를 지운다. (지운 개수)
    Playwright는 정상 종료 시 스스로 치우지만, 타임아웃·강제 종료 때 남는다.
    """
    import glob, shutil, tempfile, time as _t
    removed = 0
    cutoff = _t.time() - 3600          # 1시간 이상 된 것만
    patterns = ["playwright*", "chrome_*", ".org.chromium.*", "scoped_dir*"]
    for pat in patterns:
        for p in glob.glob(os.path.join(tempfile.gettempdir(), pat)):
            try:
                if os.path.getmtime(p) > cutoff:
                    continue
                shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
                removed += 1
            except Exception:
                continue
    return removed


def disk_ok(verbose: bool = True) -> bool:
    """
    렌더링을 시도해도 되는 상태인지.
    부족하면 임시 파일을 먼저 정리하고 다시 확인한다.
    """
    mb = free_mb()
    if mb >= MIN_FREE_MB:
        return True
    n = cleanup_temp()
    mb2 = free_mb()
    if verbose:
        print(f"[디스크] 여유 {mb}MB → 임시파일 {n}개 정리 → {mb2}MB")
    if mb2 < MIN_FREE_MB and verbose:
        print(f"[디스크] 여유 부족({mb2}MB < {MIN_FREE_MB}MB) — 렌더링을 건너뜁니다."
              f"\n         디스크를 정리하거나 RADAR_MIN_FREE_MB 로 기준을 낮추세요.")
    return mb2 >= MIN_FREE_MB


def available() -> bool:
    """이 환경에서 헤드리스 렌더링이 가능한가."""
    global _available
    if _available is not None:
        return _available
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(args=LAUNCH_ARGS)
            b.close()
        _available = True
    except Exception as e:
        print(f"[renderer] 헤드리스 렌더링 비활성: {type(e).__name__}")
        _available = False
    return _available


def render(url: str, wait_selector: str | None = None,
           timeout_ms: int = 30000, scroll: bool = True) -> str:
    """
    URL을 브라우저로 열어 JS 실행 후 HTML을 반환.
    wait_selector : 이 요소가 나타날 때까지 대기 (상품 카드 셀렉터)
    scroll        : 지연 로딩(lazy load) 상품을 끌어내리기 위해 스크롤
    실패 시 빈 문자열.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""

    # 디스크가 부족하면 렌더링을 건너뛴다.
    # 무리하게 진행하면 브라우저가 아니라 DB 쓰기에서 터져 사이클 전체가 죽는다.
    if not disk_ok():
        return ""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=LAUNCH_ARGS)
            ctx = browser.new_context(user_agent=UA,
                                      viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            # 이미지·폰트는 차단해 속도를 크게 올린다 (상품 데이터엔 불필요)
            page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf}",
                       lambda r: r.abort())
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")

            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=15000)
                except Exception:
                    pass          # 셀렉터가 안 나와도 현재 DOM은 반환

            if scroll:
                for _ in range(SCROLL_STEPS):
                    page.mouse.wheel(0, 5000)
                    page.wait_for_timeout(SCROLL_WAIT_MS)

            page.wait_for_timeout(600)
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        print(f"[renderer] 렌더 실패 {url}: {type(e).__name__}: {str(e)[:100]}")
        return ""


# 사이트별로 '상품이 나타났다'고 판단할 셀렉터
WAIT_SELECTORS = {
    "amazon.com":  "[data-testid='deal-card'], .DealGridItem-module__dealItem, [data-component-type]",
    "ebay.com":    ".dne-itemtile, [data-testid='item-tile'], .b-visualnav__tile",
    "woot.com":    "[class*='product'], [class*='offer'], main a[href*='/offers/']",
    "nike.com":    ".product-card, [data-testid='product-card']",
    "nordstrom.com": "article",
}

# 렌더링이 필요한 사이트 (정적 fetch로는 상품이 안 나오는 곳)
NEEDS_RENDER = set(WAIT_SELECTORS)


def needs_render(domain: str) -> bool:
    return domain in NEEDS_RENDER


# 렌더 결과가 '쓸모 있는지' 판정하는 신호.
# Amazon 딜 그리드는 가격을 지연 하이드레이션하는데, 같은 URL이라도
# 렌더 시점에 따라 가격 JSON이 붙기도 하고 안 붙기도 한다(실측 확인).
# 이 신호가 없으면 재시도한다.
CONTENT_SIGNAL = {
    "amazon.com": "priceToPay",
    "ebay.com": 'itemprop="price"',
    "woot.com": 'data-test-ui="offerItem"',
}


def fetch(domain: str, url: str, retries: int = 2) -> str:
    """
    렌더 후 CONTENT_SIGNAL이 없으면 재시도.
    끝내 못 얻으면 마지막 결과를 그대로 반환한다(파서가 0건을 내면 그만).
    """
    signal = CONTENT_SIGNAL.get(domain)
    html = ""
    for attempt in range(retries + 1):
        html = render(url, wait_selector=WAIT_SELECTORS.get(domain))
        if not signal or (html and signal in html):
            return html
        if attempt < retries:
            print(f"  [renderer] {domain}: 가격 미하이드레이션 → 재시도 {attempt+1}/{retries}")
    return html
