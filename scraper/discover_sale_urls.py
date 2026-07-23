"""
discover_sale_urls.py — 사이트별 실제 세일 페이지 주소 자동 탐색

왜 필요한가:
    워치리스트에 123개 사이트가 '크롤 가능'으로 등록돼 있는데
    실제로 딜이 나오는 건 5곳뿐이었다. 진단해보니 원인이 명확했다.

        ashford.com/sale        → HTTP 404
        merrell.com/sale        → HTTP 404
        underarmour.com/sale    → HTTP 404
        ssense.com/sale         → HTTP 404

    내가 세일 URL을 /sale, /clearance 로 '찍어서' 넣었는데
    사이트마다 주소 구조가 다르다. 대부분 404였다.

해결:
    홈페이지를 가져와 네비게이션에서 세일/클리어런스/아울렛 링크를 찾아낸다.
    찾은 후보를 실제로 열어보고, 상품이 실제로 추출되는 주소만 채택한다.

    추측이 아니라 검증된 주소만 저장하므로, 이후 크롤 수율이 올라간다.

실행: 13_세일주소탐색.bat
"""
from __future__ import annotations
import csv, os, re, sys, urllib.parse
from concurrent.futures import ThreadPoolExecutor

import sources
import watchlist as wl

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "watchlist.csv")
OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "sale_urls.csv")

# 링크 텍스트/주소에서 세일 페이지를 가리키는 신호
_SALE_WORDS = re.compile(
    r"sale|clearance|outlet|markdown|deals?|discount|reduced|final[- ]?sale"
    r"|promo|special|bargain|last[- ]?chance", re.I)

# 세일이 아닌데 걸리기 쉬운 것들
_SKIP = re.compile(
    r"gift|card|policy|terms|privacy|help|faq|contact|about|career|store[- ]?locator"
    r"|account|login|signin|register|cart|checkout|wishlist|blog|news|press"
    r"|shipping|return|track|size[- ]?guide|newsletter|unsubscribe", re.I)

_LINK = re.compile(r'<a[^>]+href="(?P<href>[^"#]{2,200})"[^>]*>(?P<text>.{0,120}?)</a>', re.S | re.I)


def _candidates(html: str, base: str) -> list[str]:
    """홈페이지에서 세일 페이지 후보 URL을 뽑는다."""
    seen: dict[str, int] = {}
    host = urllib.parse.urlparse(base).netloc
    for m in _LINK.finditer(html):
        href = m.group("href").strip()
        text = re.sub(r"<[^>]+>", " ", m.group("text"))
        text = re.sub(r"\s+", " ", text).strip()
        blob = f"{href} {text}"
        if not _SALE_WORDS.search(blob) or _SKIP.search(blob):
            continue

        url = urllib.parse.urljoin(base, href)
        p = urllib.parse.urlparse(url)
        if p.netloc and host.split(".")[-2:] != p.netloc.split(".")[-2:]:
            continue                      # 외부 도메인 제외
        url = url.split("?")[0].rstrip("/")
        if not url or url == base.rstrip("/"):
            continue

        # 점수: 링크 텍스트에 세일 단어가 있으면 가산(네비 메뉴일 확률 높음)
        score = 2 if _SALE_WORDS.search(text) else 1
        if re.search(r"/(sale|clearance|outlet)(/|$)", p.path, re.I):
            score += 3
        seen[url] = max(seen.get(url, 0), score)

    return [u for u, _ in sorted(seen.items(), key=lambda x: -x[1])][:6]


def _yield_of(url: str, site) -> int:
    """이 주소에서 실제로 상품이 몇 개 추출되는지."""
    try:
        html = sources._http_get(url, timeout=10)
    except Exception:
        return 0
    parser = sources.PARSERS.get(site.domain, sources.generic_sale_parser)
    try:
        return len(parser(html, site))
    except Exception:
        return 0


def discover(site) -> tuple[str, int]:
    """(가장 좋은 세일 URL, 추출 건수). 못 찾으면 ('', 0)."""
    # 1) 현재 등록된 주소가 이미 잘 되면 그대로 둔다
    for u in site.sale_urls[:2]:
        n = _yield_of(u, site)
        if n >= 3:
            return u, n

    # 2) 홈페이지에서 세일 링크 탐색
    try:
        home = sources._http_get(site.base_url, timeout=10)
    except Exception:
        return "", 0

    best_url, best_n = "", 0
    for cand in _candidates(home, site.base_url):
        if not sources.check_robots(cand):
            continue
        n = _yield_of(cand, site)
        if n > best_n:
            best_url, best_n = cand, n
        if best_n >= 15:
            break                          # 충분하면 조기 종료
    return best_url, best_n


def main() -> int:
    tiers = sys.argv[1].split(",") if len(sys.argv) > 1 else ["T2", "T3"]
    sites = [s for s in wl.crawlable() if s.tier in tiers]

    print("=" * 62)
    print("  세일 페이지 주소 탐색")
    print("=" * 62)
    print(f"\n대상 {len(sites)}곳 ({','.join(tiers)})")
    print("사이트당 최대 1분, 전체 5~15분 걸립니다. 창을 닫지 마세요.\n")

    results = []
    done = 0

    def work(s):
        try:
            return s, *discover(s)
        except Exception:
            return s, "", 0

    with ThreadPoolExecutor(max_workers=6) as ex:
        for site, url, n in ex.map(work, sites):
            done += 1
            if n:
                print(f"  ({done:3d}/{len(sites)}) [{n:3d}건] {site.domain:24s} {url[len(site.base_url):][:40]}")
                results.append((site.domain, url, n))
            else:
                print(f"  ({done:3d}/{len(sites)}) [  0건] {site.domain:24s} 세일 페이지 못 찾음")

    results.sort(key=lambda r: -r[2])
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["domain", "sale_url", "items"])
        w.writerows(results)

    print("\n" + "=" * 62)
    print(f"  탐색 완료 — {len(results)}곳에서 세일 페이지 확인")
    print(f"  결과 저장: data/sale_urls.csv")
    if results:
        print(f"\n  수집량 상위:")
        for d, u, n in results[:10]:
            print(f"    {n:3d}건  {d}")
        print("\n  watchlist.py 의 SALE_PATHS 에 자동 반영하려면")
        print("  아래 줄을 복사해 붙여넣으세요:\n")
        for d, u, n in results[:20]:
            path = u.replace(f"https://{d}", "").replace(f"https://www.{d}", "")
            print(f'    "{d}": ["{path}"],')
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
