"""
diag_sources.py — 소스별 발행 실태 진단

"왜 노드스트롬랙만 나오지?" 를 데이터로 답한다.

두 가지를 보여준다:
  1) 현재 딜보드(deals.json)에 실제로 있는 소스 분포
  2) 지금까지 알림 보낸 이력의 소스 분포 (중복방지 DB)

그리고 --reset 를 주면 알림 이력을 비운다.
개발/테스트로 이력이 오염되면 새 딜이 계속 '이미 봄'으로 걸러져
노드스트롬랙만 반복되는 것처럼 보인다. 초기화하면 다음 실행부터
모든 소스가 다시 신규로 잡힌다.

실행: 14_소스진단.bat  (또는  python diag_sources.py --reset)
"""
from __future__ import annotations
import json, os, re, sys, collections

import store

WEB_JSON = os.path.join(os.path.dirname(__file__), "..", "web", "deals.json")


def _domain(url: str) -> str:
    m = re.search(r"https?://(?:www\.|m\.)?([^/]+)", url or "")
    return m.group(1) if m else "?"


def main() -> int:
    reset = "--reset" in sys.argv
    con = store.connect()

    print("=" * 58)
    print("  소스별 발행 실태 진단")
    print("=" * 58)

    # 1) 현재 딜보드
    print("\n[1] 현재 딜보드(deals.json)에 있는 딜")
    try:
        d = json.load(open(WEB_JSON, encoding="utf-8"))
        c = collections.Counter(x.get("source", "?") for x in d.get("deals", []))
        print(f"    총 {d.get('count', 0)}건 · 갱신 {d.get('generated_at', '')[:19]}")
        for s, n in c.most_common():
            print(f"      {n:4d}  {s}")
    except Exception as e:
        print(f"    딜보드 없음 ({e})")

    # 2) 알림 이력
    print("\n[2] 지금까지 알림 보낸 이력 (중복방지 DB)")
    rows = con.execute("SELECT url, urgency, first_seen FROM notified").fetchall()
    c = collections.Counter(_domain(u) for u, _, _ in rows)
    print(f"    총 {len(rows)}건")
    for s, n in c.most_common():
        print(f"      {n:4d}  {s}")

    if rows and not reset:
        first = min((r[2] for r in rows if r[2]), default="")
        print(f"\n    가장 오래된 기록: {first[:19]}")
        print("    이 이력에 있는 딜은 '이미 봄'으로 걸러져 다시 알리지 않습니다.")
        print("    개발/테스트로 쌓인 이력이면 아래로 초기화하세요:")
        print("      14_소스진단.bat 을 다시 실행할 때 초기화 옵션 선택")

    # 3) 초기화
    if reset:
        n1 = con.execute("SELECT COUNT(*) FROM notified").fetchone()[0]
        con.execute("DELETE FROM notified")
        con.commit()
        print(f"\n[초기화] 알림 이력 {n1}건 삭제 완료.")
        print("    다음 수집부터 모든 소스가 다시 신규로 잡힙니다.")
        print("    (가격 이력은 보존됩니다 — 점수 계산에 계속 쓰입니다)")

    print("\n" + "=" * 58)
    print("  참고: 소스가 편중돼 보이는 흔한 이유")
    print("  - 딜보드가 특정 티어만 반영 → 병합 로직으로 해결됨")
    print("  - 오래된 알림 이력이 새 딜을 계속 걸러냄 → --reset 로 해결")
    print("  - 사이트마다 세일 깊이가 다름 → 노드스트롬랙은 85%+ 상시")
    print("=" * 58)
    return 0


if __name__ == "__main__":
    sys.exit(main())
