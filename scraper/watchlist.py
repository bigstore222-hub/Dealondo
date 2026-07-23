"""
watchlist.py — 감시 대상 리테일러 레지스트리

data/watchlist.csv 는 두 소스를 병합해 만든 마스터 목록이다:
  1) 4년치 카톡 실측 — 사용자가 실제로 올린 링크의 리테일러 도메인 (posts = 게시 횟수)
  2) 딜공 몰 디렉토리 97개

각 행에는 robots.txt 사전 점검 결과(crawl_allowed)가 들어있어,
크롤러는 YES 인 사이트만 직접 감시하고 나머지는 제휴 피드/수동으로 라우팅한다.

티어 = 실제 게시 빈도 기반 우선순위 → 폴링 주기를 결정한다.
  T1 (100건+)  : 15분
  T2 (15~99)   : 30분
  T3 (2~14)    : 2시간
  T4 (0~1)     : 6시간
"""
from __future__ import annotations
import csv, os
from dataclasses import dataclass

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "watchlist.csv")

POLL_MINUTES = {"T1": 15, "T2": 30, "T3": 120, "T4": 360}

# 사이트별 세일/클리어런스 경로. 미등록 사이트는 공통 후보를 순차 시도.
SALE_PATHS = {
    # /coupons 를 먼저 본다.
    # 골드박스는 13~50% 할인 위주라 4년 기준(80%+)을 통과하는 딜이 드물지만,
    # 쿠폰 페이지는 세일가에 추가 쿠폰이 붙어 실구매가가 훨씬 내려간다.
    # 실측에서 아마존이 1위 소스(788건)였던 것도 이 쿠폰 딜들 때문이다.
    "amazon.com":        ["/coupons", "/gp/goldbox"],
    # /alldeals 가 전 서브도메인(sport/electronics/computers/sellout/home/tools)
    # 딜을 한 페이지에 모아준다. 홈(/)은 랜딩이라 상품이 거의 없다.
    "woot.com":          ["/alldeals"],
    "ebay.com":          ["/deals"],
    "nordstromrack.com": ["/clearance"],
    "nordstrom.com":     ["/browse/sale"],
    "nike.com":          ["/w/sale-3yaep"],
    "columbia.com":      ["/c/sale"],
    # 할인율 내림차순 정렬(.zso?s=percentOff/desc/) — 80%+ 딜을 첫 페이지로 끌어온다.
    # 기본 /sale 은 얕은 할인부터 노출돼 깊은 딜이 뒤로 밀린다(실측: 첫 100개 최대 54%).
    "zappos.com":        ["/sale/.zso?s=percentOff/desc/", "/sale"],
    # /sale.html 은 404. 시계 할인 목록이 그나마 유효(200).
    "jomashop.com":      ["/watches.html?discount=1", "/sale.html"],
    "rei.com":           ["/rei-garage"],
    "macys.com":         ["/shop/sale"],
    "theoutnet.com":     ["/en-us/shop/sale"],
    "adidas.com":        ["/us/sale"],

    # 13_세일주소탐색.bat 으로 확인된 실제 세일 주소.
    # 처음엔 전부 /sale 로 추측해 넣었는데 대부분 404였다(실측).
    "ssense.com":        ["/ko-kr/men/sale", "/en-us/men/sale"],
    # 6pm은 Zappos 계열. 할인율 내림차순 정렬로 80%+ 딜을 앞으로 끌어온다.
    "6pm.com":           ["/sale/.zso?s=percentOff/desc/"],
    "merrell.com":       ["/category/outlet/126"],
    "underarmour.com":   ["/en-us/c/outlet"],
    "ashford.com":       ["/collections/clearance", "/collections/weekly-deals"],
    "eddiebauer.com":    ["/c/sale"],
    "endclothing.com":   ["/us/sale"],
    "shoebacca.com":     ["/collections/sale"],
    "newegg.com":        ["/todays-deals"],
    "gapfactory.com":    ["/browse/division.do?cid=1127936"],
    # 2026-07-22 새 카톡 데이터에서 발견된 신규 리테일러
    "yoox.com":          ["/us/sale/shop", "/us/sale"],
    "victoriassecret.com": ["/us/sale"],
}
DEFAULT_SALE_PATHS = ["/sale", "/clearance", "/shop/sale", "/c/sale"]


@dataclass
class Site:
    tier: str
    domain: str
    name: str
    posts: int
    crawl_allowed: str      # YES / PARTIAL / NO / UNKNOWN
    note: str = ""

    @property
    def base_url(self) -> str:
        d = self.domain
        return f"https://{d}" if d.count(".") > 1 else f"https://www.{d}"

    @property
    def sale_urls(self) -> list[str]:
        paths = SALE_PATHS.get(self.domain, DEFAULT_SALE_PATHS)
        return [self.base_url + p for p in paths]

    @property
    def poll_minutes(self) -> int:
        return POLL_MINUTES.get(self.tier, 360)

    @property
    def strategy(self) -> str:
        """이 사이트를 어떻게 수집할지 결정."""
        if self.crawl_allowed == "YES":
            return "direct_crawl"
        if self.crawl_allowed in ("UNKNOWN", "PARTIAL"):
            return "affiliate_feed"    # 봇탐지/차단 → 제휴 네트워크 피드로 우회
        return "excluded"


def load(path: str = CSV_PATH) -> list[Site]:
    sites: list[Site] = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sites.append(Site(
                tier=row["tier"], domain=row["domain"], name=row["name"],
                posts=int(row["posts"] or 0),
                crawl_allowed=row.get("crawl_allowed", "UNKNOWN"),
                note=row.get("note", ""),
            ))
    return sites


def crawlable(sites: list[Site] | None = None) -> list[Site]:
    """직접 크롤 가능한 사이트만."""
    return [s for s in (sites or load()) if s.strategy == "direct_crawl"]


def due_now(elapsed_minutes: int, sites: list[Site] | None = None) -> list[Site]:
    """경과 시간 기준으로 지금 폴링해야 하는 사이트."""
    return [s for s in crawlable(sites) if elapsed_minutes % s.poll_minutes == 0]


def summary() -> str:
    sites = load()
    from collections import Counter
    t = Counter(s.tier for s in sites)
    st = Counter(s.strategy for s in sites)
    lines = [f"감시 대상 {len(sites)}개 사이트",
             "  티어: " + ", ".join(f"{k}={t[k]}" for k in sorted(t)),
             "  전략: " + ", ".join(f"{k}={v}" for k, v in st.most_common())]
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())
    print("\n=== T1/T2 직접 크롤 대상 ===")
    for s in crawlable():
        if s.tier in ("T1", "T2"):
            print(f"  [{s.tier}] {s.name:20s} {s.poll_minutes:4d}분  {s.sale_urls[0]}")
