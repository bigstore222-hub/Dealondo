"""
run.py — 파이프라인 엔트리포인트
수집(sources.collect_all) → 스코어링(filter_engine.process) → 발행 큐(deals.json) 저장.

사용:
    python run.py            # 실제 소스 수집 (DoA RSS 등, 네트워크 필요)
    python run.py --sample   # 네트워크 없이 샘플 데이터로 파이프라인 시연

발행 결과는 ../web/deals.json 에 저장되어 딜보드(web/index.html)가 읽는다.
"""
from __future__ import annotations
import json, sys, os
from dataclasses import asdict
from datetime import datetime, timezone

from filter_engine import Deal, process, should_publish

WEB_JSON = os.path.join(os.path.dirname(__file__), "..", "web", "deals.json")


def load_sample() -> list[Deal]:
    """실제 4년치 데이터에서 관찰된 도메인/브랜드/할인 패턴을 반영한 시연용 딜."""
    return [
        Deal(source="slickdeals_api", source_tier="T1",
             url="https://slickdeals.net/f/example-northface-nuptse",
             title="The North Face 2000 Retro Nuptse Jacket",
             brand="The North Face", price_current=160, price_list=320,
             price_baseline=320, price_alltime_low=160, frontpage=True,
             community_votes=180, ships_to_kr=True, collection_method="api"),
        Deal(source="dealsofamerica", source_tier="T2",
             url="https://www.dealsofamerica.com/example-nb990",
             title="New Balance Made in USA 990v6",
             brand="New Balance", price_current=114, price_list=220,
             price_baseline=200, price_alltime_low=110,
             community_votes=70, collection_method="rss"),
        Deal(source="nordstromrack", source_tier="T2",
             url="https://www.nordstromrack.com/example-tb-shirt",
             title="Thom Browne Classic Shirt Clearance",
             brand="Thom Browne", price_current=169.99, price_list=490,
             price_baseline=490, coupon_code="EXTRA20", coupon_stackable=True,
             rating=4.6, review_count=120, collection_method="scrape"),
        Deal(source="woot", source_tier="T2",
             url="https://electronics.woot.com/example-ssd",
             title="Samsung 990 Pro 2TB NVMe SSD",
             brand="Samsung", category="electronics",
             price_current=129, price_list=249, price_baseline=210,
             price_alltime_low=125, rating=4.8, review_count=3400,
             collection_method="scrape"),
        Deal(source="macys", source_tier="T2",
             url="https://www.macys.com/example-polo",
             title="Polo Ralph Lauren Kids Cotton Cardigan",
             brand="Polo Ralph Lauren", price_current=34.9, price_list=89.5,
             price_baseline=89.5, coupon_stackable=True, off_season=True,
             rating=4.7, review_count=210, collection_method="scrape"),
        # 하드필터 탈락 예시 (국내가 역전)
        Deal(source="ebay", source_tier="T2",
             url="https://www.ebay.com/example-earbuds",
             title="Generic Wireless Earbuds",
             brand="Generic", price_current=25, price_list=30,
             price_baseline=30, krw_effective=41000, kr_lowest_price=39000,
             rating=3.8, review_count=12, collection_method="scrape"),
    ]


def main():
    use_sample = "--sample" in sys.argv
    if use_sample:
        raw = load_sample()
        print(f"[sample] {len(raw)}건 로드")
    else:
        from sources import collect_all
        raw = collect_all()

    scored = process(raw)
    published = [d for d in scored if should_publish(d)]
    published.sort(key=lambda d: d.score, reverse=True)

    logged = [d for d in scored if not should_publish(d)]
    print(f"\n=== 결과 ===")
    print(f"수집 {len(scored)} → 발행 {len(published)} / 미발행·탈락 {len(logged)}")
    for d in published:
        print(f"  [{d.urgency:6s}] {d.score:3d}점  {d.title[:40]}  ({d.discount_pct}% off)")
    for d in logged:
        if d.reject_reason:
            print(f"  [탈락 ] {d.title[:40]} — {d.reject_reason}")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(published),
        "deals": [asdict(d) for d in published],
    }
    os.makedirs(os.path.dirname(WEB_JSON), exist_ok=True)
    with open(WEB_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n발행 큐 저장 → {os.path.relpath(WEB_JSON)}")


if __name__ == "__main__":
    main()
