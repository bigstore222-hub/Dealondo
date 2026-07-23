"""
setup_naver.py — 네이버 쇼핑 API 설정 도우미

Client ID / Secret 을 붙여넣으면:
  1) 실제 검색을 걸어 키가 동작하는지 확인
  2) 윈도우 환경변수에 영구 저장
  3) 실제 딜로 H2 판정(국내가 역전) 시연

실행: 7_네이버설정.bat
"""
from __future__ import annotations
import json, os, subprocess, sys, urllib.request, urllib.parse

SHOP_API = "https://openapi.naver.com/v1/search/shop.json"


def search(cid: str, secret: str, query: str, display: int = 5):
    url = f"{SHOP_API}?{urllib.parse.urlencode({'query': query, 'display': display})}"
    req = urllib.request.Request(url, headers={
        "X-Naver-Client-Id": cid,
        "X-Naver-Client-Secret": secret,
    })
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def save_env(key: str, value: str) -> bool:
    try:
        subprocess.run(["setx", key, value], check=True, capture_output=True, text=True)
        os.environ[key] = value
        return True
    except Exception as e:
        print(f"   저장 실패 ({key}): {e}")
        return False


def main() -> int:
    print("=" * 54)
    print("  네이버 쇼핑 API 설정")
    print("=" * 54)
    print("""
아직 키가 없다면 먼저 발급받으세요 (무료, 3분):

  1. https://developers.naver.com/apps/#/register  접속
     (네이버 로그인 필요)
  2. 애플리케이션 이름: 아무거나 (예: hotdeal-radar)
  3. 사용 API: '검색' 선택
  4. 비로그인 오픈 API 서비스 환경: 'WEB 설정' 선택
     웹 서비스 URL 칸에  http://localhost  입력
  5. 등록하면 Client ID 와 Client Secret 이 나옵니다
""")

    cid = input("Client ID: ").strip()
    secret = input("Client Secret: ").strip()
    if not (cid and secret):
        print("\n[!] 둘 다 입력해야 합니다.")
        return 1

    print("\n키 확인 중...")
    try:
        res = search(cid, secret, "뉴발란스 990v6")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        print(f"\n[!] 인증 실패 (HTTP {e.code})")
        print(f"    {body}")
        print("\n    Client ID/Secret 을 다시 확인해 주세요.")
        print("    '검색' API 사용 설정이 되어 있는지도 확인이 필요합니다.")
        return 1
    except Exception as e:
        print(f"\n[!] 오류: {type(e).__name__}: {e}")
        return 1

    items = res.get("items", [])
    print(f"   확인 완료 (검색 결과 {res.get('total', 0):,}건)")
    if items:
        import re
        name = re.sub(r"<[^>]+>", "", items[0]["title"])
        print(f"   예시: {name[:40]} / {int(items[0]['lprice']):,}원")

    print("\n환경변수에 저장 중...")
    ok1 = save_env("NAVER_CLIENT_ID", cid)
    ok2 = save_env("NAVER_CLIENT_SECRET", secret)
    if ok1 and ok2:
        print("   저장 완료")

    # 실제 파이프라인에 적용해 시연
    print("\n" + "=" * 54)
    print("  H2 필터 시연 (관부가세 포함 실질가 vs 국내 최저가)")
    print("=" * 54)
    try:
        import pricing, filter_engine as fe
        fx = pricing.usd_krw()
        print(f"\n환율: 1달러 = {fx:,.0f}원\n")
        demo = [
            ("New Balance", "990v6", 210.0, 260.0, "sports_outdoor"),
            ("Kate Spade New York", "Kara Loafer", 34.99, 229.0, "premium_fashion"),
        ]
        for brand, title, cur, lst, cat in demo:
            d = fe.Deal(source="demo", source_tier="T2", url="http://x",
                        title=title, brand=brand, category=cat,
                        price_current=cur, price_list=lst, price_baseline=lst)
            d.krw_effective = pricing.landed_cost_krw(cur, cat)
            d.kr_lowest_price = pricing.kr_lowest_price(brand, title)
            fe.score_deal(d)
            print(f"[{brand} {title}]")
            print(f"   해외가 ${cur:,.2f}  ->  관부가세 포함 {d.krw_effective:,}원")
            if d.kr_lowest_price:
                save = (1 - d.krw_effective / d.kr_lowest_price) * 100
                print(f"   국내 최저가 {int(d.kr_lowest_price):,}원  ->  절감 {save:+.1f}%")
            else:
                print("   국내 최저가: 검색 결과 없음 (H2 판정 생략)")
            print(f"   판정: {d.reject_reason or f'통과 {d.score}점 {d.urgency}'}\n")
    except Exception as e:
        print(f"시연 중 오류: {type(e).__name__}: {e}")

    print("=" * 54)
    print("  설정 완료")
    print("  이제 열린 명령창을 모두 닫고 다시 실행하면")
    print("  국내가 비교가 자동으로 적용됩니다")
    print("=" * 54)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n취소되었습니다.")
        sys.exit(1)
