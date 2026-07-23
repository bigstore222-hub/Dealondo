"""
recheck_blocked.py — 차단(UNKNOWN) 사이트 재점검

watchlist.csv 를 만들 때의 robots.txt 점검은 클라우드(데이터센터) IP에서 수행됐다.
Macy's·Adidas 같은 대형 리테일러는 데이터센터 IP를 통째로 막는 경우가 많아서,
'실제로는 크롤링을 허용하는데 UNKNOWN으로 잘못 분류'된 사이트가 섞여 있다.

이 스크립트는 **사용자의 집 인터넷(가정용 IP)** 에서 다시 점검한다.
가정용 IP는 차단 대상이 아닌 경우가 많아 상당수가 재분류될 수 있다.

점검 순서:
  1) 일반 브라우저 UA로 robots.txt 읽기 시도
  2) 실패하면 Playwright(실제 브라우저)로 재시도
  3) 읽어낸 robots.txt 규칙으로 세일 경로 접근 가능 여부 판정
  4) 결과를 watchlist.csv 에 반영

robots.txt 를 읽는 것 자체는 '규칙을 확인하는' 정상적인 행위다.
규칙을 읽은 뒤에는 그 규칙을 그대로 따른다.

실행: 8_차단사이트재점검.bat
"""
from __future__ import annotations
import csv, os, sys, urllib.request, urllib.robotparser
from concurrent.futures import ThreadPoolExecutor

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "watchlist.csv")

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
OUR_UA = "HotdealRadar/0.1"
SALE_PATHS = ["/sale", "/clearance", "/shop/sale", "/c/sale", "/browse/sale"]


def base_url(domain: str) -> str:
    return f"https://{domain}" if domain.count(".") > 1 else f"https://www.{domain}"


def fetch_robots_http(domain: str, timeout: int = 12) -> str | None:
    try:
        req = urllib.request.Request(base_url(domain) + "/robots.txt",
                                     headers={"User-Agent": BROWSER_UA,
                                              "Accept": "text/plain,*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return None


def fetch_robots_browser(domain: str) -> str | None:
    """정적 요청이 막히면 실제 브라우저로 읽는다."""
    try:
        import renderer
        if not renderer.available():
            return None
        html = renderer.render(base_url(domain) + "/robots.txt",
                               wait_selector=None, scroll=False, timeout_ms=25000)
        if not html:
            return None
        import re
        txt = re.sub(r"<[^>]+>", "", html)
        return txt if "user-agent" in txt.lower() else None
    except Exception:
        return None


def judge(domain: str) -> tuple[str, str, str]:
    """(crawl_allowed, robots_status, note)"""
    txt = fetch_robots_http(domain)
    how = "http"
    if txt is None or "user-agent" not in txt.lower():
        txt = fetch_robots_browser(domain)
        how = "browser"
    if txt is None:
        return "UNKNOWN", "접근불가", "여전히 차단 - 제휴 피드 권장"

    rp = urllib.robotparser.RobotFileParser()
    rp.parse(txt.splitlines())
    b = base_url(domain)
    allowed = [p for p in SALE_PATHS if rp.can_fetch(OUR_UA, b + p)]
    if allowed:
        return "YES", f"200({how})", "allowed:" + ",".join(allowed[:2])
    if rp.can_fetch(OUR_UA, b + "/"):
        return "PARTIAL", f"200({how})", "루트만 허용, 세일경로 차단"
    return "NO", f"200({how})", "robots 전면 차단"


LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "재점검결과.txt")
_logfile = None


def out(msg: str = "") -> None:
    """화면과 로그 파일에 동시 출력 (화면이 멈춘 것처럼 보이지 않게)."""
    print(msg, flush=True)
    if _logfile:
        _logfile.write(msg + "\n")
        _logfile.flush()


def main() -> int:
    global _logfile
    _logfile = open(LOG_PATH, "w", encoding="utf-8")

    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8")))
    targets = [r for r in rows if r["crawl_allowed"] in ("UNKNOWN", "PARTIAL")]

    out("=" * 58)
    out("  차단 사이트 재점검")
    out("=" * 58)
    out(f"\n대상 {len(targets)}개를 집 인터넷에서 다시 확인합니다.")
    out("사이트당 최대 12초, 전체 2~4분 정도 걸립니다.")
    out("(진행 상황이 아래에 한 줄씩 나옵니다)\n")

    if not targets:
        out("재점검할 사이트가 없습니다.")
        return 0

    # 실측 게시 이력이 많은 순으로
    targets.sort(key=lambda r: -int(r["posts"]))
    posts_by = {r["domain"]: r["posts"] for r in targets}
    results = {}

    def work(r):
        v, st, note = judge(r["domain"])
        return r["domain"], v, st, note

    done = 0
    total = len(targets)
    with ThreadPoolExecutor(max_workers=8) as ex:
        for domain, v, st, note in ex.map(work, targets):
            results[domain] = (v, st, note)
            done += 1
            mark = {"YES": "[가능]", "PARTIAL": "[부분]",
                    "NO": "[금지]", "UNKNOWN": "[불가]"}[v]
            out(f"  ({done:2d}/{total}) {mark} {domain:26s} "
                f"실측{posts_by.get(domain,'0'):>4}건  {note[:30]}")

    # CSV 반영
    changed = 0
    for r in rows:
        if r["domain"] in results:
            v, st, note = results[r["domain"]]
            if v != r["crawl_allowed"]:
                changed += 1
            r["crawl_allowed"], r["robots_status"], r["note"] = v, st, note

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    newly = sum(1 for v, _, _ in results.values() if v == "YES")
    out("\n" + "=" * 58)
    out(f"  재점검 완료 - {changed}개 재분류")
    out(f"  새로 크롤 가능해진 사이트: {newly}개")
    if newly:
        out("\n  다음 실행부터 자동으로 이 사이트들도 감시합니다.")
        out("  (세일 페이지 주소는 watchlist.py 의 SALE_PATHS 에서 조정)")
    still = [d for d, (v, _, _) in results.items() if v == "UNKNOWN"]
    if still:
        out(f"\n  여전히 접근 불가: {len(still)}개")
        out("  -> 제휴 네트워크 피드가 필요합니다 (제휴가입안내.md 참고)")
        top = [d for d in still if int(posts_by.get(d, 0)) >= 10]
        if top:
            out(f"     이 중 실측 10건 이상: {', '.join(top)}")
    out("=" * 58)
    out(f"\n결과가 재점검결과.txt 에도 저장되었습니다.")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:
        import traceback
        print("\n[오류] 재점검 중 문제가 발생했습니다:")
        traceback.print_exc()
        code = 1
    finally:
        if _logfile:
            _logfile.close()
    sys.exit(code)
