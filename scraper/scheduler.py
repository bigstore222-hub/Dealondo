"""
scheduler.py — 상주 스케줄러

티어별로 다른 주기로 워치리스트를 폴링하고,
FLASH 딜이 나오면 정기 슬롯을 기다리지 않고 즉시 알린다.

    T1 (Amazon/Woot/eBay)   15분
    T2 (Nordstrom Rack 등)  30분
    T3                      2시간
    T4                      6시간

발행 정책 (4년치 실측 시간 분포 기반):
  - FLASH(85점+ 또는 slickdeals frontpage) → 시각 무관 즉시 푸시
  - HOT/STEADY → 정기 슬롯(08~10, 13~16, 20~23시)에만 푸시.
    슬롯 밖에서 발견되면 큐에 담아뒀다가 다음 슬롯에 내보낸다.

실행:
    python scheduler.py               # 상주 루프
    python scheduler.py --once        # 1사이클만 (테스트용)
    python scheduler.py --once --tier T1
"""
from __future__ import annotations
import sys, time, json, os
from dataclasses import asdict
from datetime import datetime, timezone


# 화면과 로그 파일에 동시에 출력한다(Tee).
# 배치가 출력을 파일로만 빼돌리면 실행 중 화면이 비어 멈춘 것처럼 보인다.
# RADAR_LOG 가 지정되면 그 파일에도 남기고, 화면에도 즉시 보여준다.
class _Tee:
    def __init__(self, path):
        self.term = sys.__stdout__
        try:
            self.log = open(path, "a", encoding="utf-8")
        except Exception:
            self.log = None

    def write(self, s):
        try:
            self.term.write(s); self.term.flush()
        except Exception:
            pass
        if self.log:
            self.log.write(s); self.log.flush()

    def flush(self):
        try:
            self.term.flush()
        except Exception:
            pass
        if self.log:
            self.log.flush()


if os.environ.get("RADAR_LOG"):
    sys.stdout = sys.stderr = _Tee(os.environ["RADAR_LOG"])

import watchlist as wl
import sources
import filter_engine as fe
import store
import notify
import pricing
import membership

# 딜보드 JSON 출력 경로. GitHub Actions에서는 RADAR_WEB_JSON 으로
# 리포 루트의 deals.json(깃허브 페이지가 서빙)을 가리키게 한다.
WEB_JSON = (os.environ.get("RADAR_WEB_JSON")
            or os.path.join(os.path.dirname(__file__), "..", "web", "deals.json"))

# 정기 발행 슬롯 (로컬 시각 기준). 실측: 08~10시가 압도적 피크.
PUBLISH_SLOTS = [(8, 10), (13, 16), (20, 23)]

TIER_MINUTES = {"T1": 15, "T2": 30, "T3": 120, "T4": 360}


def in_publish_slot(now: datetime | None = None) -> bool:
    h = (now or datetime.now()).hour
    return any(a <= h < b for a, b in PUBLISH_SLOTS)


def run_tier(tier: str, con) -> tuple[list, list]:
    """한 티어를 수집→스코어링→중복제거. (발행대상, 전체) 반환."""
    print(f"\n{'='*54}\n[{tier}] 수집 시작  {datetime.now():%Y-%m-%d %H:%M:%S}\n{'='*54}")
    raw = sources.fetch_watchlist(tiers=(tier,))

    # DealsOfAmerica는 좋은 해외직구 딜이 자주 올라오는 애그리게이터다.
    # 리테일러 세일페이지에 안 잡히는 딜(아마존 코드 딜 포함)을 여기서 보완한다.
    # 제휴 상품피드(메이시스·아웃넷 등 봇차단 사이트)도 T2에서 함께 읽는다.
    # 둘 다 정적 다운로드라 빠르다.
    if tier == "T2":
        try:
            doa = sources.fetch_dealsofamerica()
            print(f"[{tier}] DealsOfAmerica {len(doa)}건 추가")
            raw = raw + doa
        except Exception as e:
            print(f"[{tier}] DoA 수집 오류: {type(e).__name__}: {e}")
        try:
            import sources_feed
            feed = sources_feed.fetch_feeds()
            if feed:
                print(f"[{tier}] 제휴 상품피드 {len(feed)}건 추가")
                raw = raw + feed
        except Exception as e:
            print(f"[{tier}] 제휴피드 수집 오류: {type(e).__name__}: {e}")

    # 이메일 뉴스레터(봇차단 사이트의 세일·코드)는 자주 안 바뀌므로 T4(6시간)에만.
    if tier == "T4":
        try:
            em = sources.fetch_email()
            if em:
                print(f"[{tier}] 이메일 뉴스레터 {len(em)}건 추가")
                raw = raw + em
        except Exception as e:
            print(f"[{tier}] 이메일 수집 오류: {type(e).__name__}: {e}")

    if not raw:
        print(f"[{tier}] 수집 0건")
        return [], []

    # 이메일로 확보한 프로모션 코드를 크롤링 상품에 적용.
    # "이 사이트 30% 추가할인" 공지가 "이 상품 실구매가 $24.49" 로 구체화된다.
    n = store.apply_promos(con, raw)
    if n:
        print(f"[코드] {n}건에 프로모션 코드 적용")

    # 가격 스냅샷 적재 → 축적된 이력으로 보강 (스펙 B항목 활성화 경로)
    store.record_prices(con, raw)
    raw = store.enrich_with_history(con, raw)

    # 환율 정규화 + 관부가세 + 국내 최저가 → H2 하드필터 활성화
    raw = pricing.enrich(raw)

    scored = fe.process(raw)

    # 제휴 추적 링크로 변환(수익화). 원본 url은 중복제거에 쓰이므로 보존하고,
    # 표시·발송용 buy_url 만 채운다. 제휴 설정이 없으면 원본과 동일하다.
    try:
        import affiliate
        for d in scored:
            d.buy_url = affiliate.wrap(d.url, d.source)
    except Exception as e:
        print(f"[제휴] 링크 변환 오류: {type(e).__name__}: {e}")

    publishable = [d for d in scored if fe.should_publish(d)]
    fresh = store.filter_new(con, publishable)

    print(f"[{tier}] 수집 {len(scored)} → 발행대상 {len(publishable)} → 신규 {len(fresh)}")
    return fresh, scored


def dispatch(fresh: list, con, pending: list, force: bool = False) -> list:
    """
    FLASH는 즉시, 나머지는 슬롯에 맞춰 발송. 남은 건 pending 으로 돌려준다.
    force=True 면 슬롯을 무시하고 즉시 발송한다(수동 테스트 실행용).
    """
    flash = [d for d in fresh if d.urgency == "FLASH"]
    rest = [d for d in fresh if d.urgency != "FLASH"]

    if flash:
        print(f"[FLASH]  {len(flash)}건 즉시 발송")
        membership.publish(flash, con)      # 유료 즉시 / 무료 지연
        store.mark_notified(con, flash)

    pending = pending + rest
    if pending and (force or in_publish_slot()):
        print(f"[발송] 대기 {len(pending)}건 발송"
              + ("  (테스트 실행이라 슬롯 무시)" if force and not in_publish_slot() else ""))
        membership.publish(pending, con)
        store.mark_notified(con, pending)
        pending = []
    elif rest:
        print(f"[대기] 슬롯 밖 - {len(rest)}건 대기열 적재 (누적 {len(pending)})")
    return pending


# 딜보드에 딜을 며칠 보여줄지. 이보다 오래된 건 새로고침 때 사라진다.
BOARD_TTL_HOURS = int(os.environ.get("RADAR_BOARD_TTL_HOURS", "48"))


def _deal_id(d: dict) -> str:
    return (d.get("url") or "").split("?")[0].rstrip("/") or f"{d.get('source')}:{d.get('title')}"


def write_board(all_deals: list) -> None:
    """
    딜보드용 JSON 갱신.

    중요: 각 티어(T1/T2/T3/T4)는 자동 실행 시 별도 프로세스로 돈다.
    그래서 이번 실행분만 쓰고 덮어쓰면, T1이 마지막에 돌 때 딜보드가
    아마존 몇 건으로 줄어든다(실제로 발생). 기존 파일과 **병합**하고
    오래된 것만 걷어내야 한다.
    """
    now = datetime.now(timezone.utc)
    fresh = {}
    for d in all_deals:
        if fe.should_publish(d):
            row = asdict(d)
            fresh[_deal_id(row)] = row

    # 기존 딜보드 읽어서 병합 (이번에 안 돈 티어의 딜을 살린다)
    merged = dict(fresh)
    try:
        with open(WEB_JSON, encoding="utf-8") as f:
            old = json.load(f)
        for row in old.get("deals", []):
            key = _deal_id(row)
            if key in merged:
                continue                      # 이번 수집분이 최신이므로 우선
            ts = row.get("detected_at", "")
            try:
                age_h = (now - datetime.fromisoformat(ts)).total_seconds() / 3600
            except (TypeError, ValueError):
                age_h = 0
            if age_h <= BOARD_TTL_HOURS:
                merged[key] = row
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    deals = sorted(merged.values(), key=lambda d: d.get("score", 0), reverse=True)
    payload = {"generated_at": now.isoformat(), "count": len(deals), "deals": deals}
    os.makedirs(os.path.dirname(WEB_JSON), exist_ok=True)
    tmp = WEB_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, WEB_JSON)                  # 원자적 교체 (중단돼도 파일 손상 없음)


def main() -> None:
    once = "--once" in sys.argv
    # --once 는 "한 사이클만 돌고 종료"(자동 실행 스케줄러가 이 방식).
    # --force-send 는 "발행 슬롯 무시하고 즉시 발송"(수동 테스트 전용).
    # 둘을 분리하지 않으면, 자동 실행이 슬롯을 무시해 아무 때나 알림이 쏟아진다.
    force_send = "--force-send" in sys.argv
    # --all 은 주기 무시하고 모든 티어를 강제로 돈다(첫 실행/수동 전량 점검용).
    force_all = "--all" in sys.argv
    only_tier = None
    if "--tier" in sys.argv:
        # 쉼표로 여러 티어 지정 가능: --tier T1,T2
        only_tier = sys.argv[sys.argv.index("--tier") + 1]

    con = store.connect()
    print(wl.summary())
    print(store.stats(con))
    print(membership.status(con))
    try:
        import renderer as _r
        mb = _r.free_mb()
        mark = "" if mb >= _r.MIN_FREE_MB * 2 else "  ← 여유가 빠듯합니다"
        print(f"[디스크] 여유 {mb:,}MB{mark}")
    except Exception:
        pass
    if not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")):
        print("[안내] 텔레그램 미설정 - 알림은 화면으로 출력됩니다 (SETUP.md 참고)")

    tiers = ([t.strip() for t in only_tier.split(",") if t.strip()]
             if only_tier else ["T1", "T2", "T3", "T4"])
    # 티어별 마지막 실행 시각을 DB에서 불러온다(GitHub Actions처럼 매번 새로 뜨는
    # 환경에서 '지금 돌 차례인 티어'만 골라 돌기 위해). 상주 모드에선 첫 사이클에만 영향.
    last_run = {t: 0.0 for t in tiers}
    last_run.update({t: v for t, v in store.get_tier_last_run(con).items() if t in tiers})
    if once and not only_tier and not force_all:
        due_now_list = [t for t in tiers
                        if time.time() - last_run.get(t, 0.0) >= TIER_MINUTES.get(t, 360) * 60]
        skipped = [t for t in tiers if t not in due_now_list]
        if skipped:
            print(f"[스케줄] 이번엔 {due_now_list or '없음'} 만 수집 "
                  f"(주기 안 된 티어 건너뜀: {', '.join(skipped)})")
        tiers = due_now_list
    pending: list = []
    board: dict = {}

    while True:
        # 지연 시간이 지난 무료 채널 딜 발송
        try:
            membership.flush_due(con)
        except Exception as e:
            print(f"[티어] 무료 발송 오류: {type(e).__name__}: {e}")

        now = time.time()
        for t in tiers:
            due = now - last_run[t] >= TIER_MINUTES.get(t, 360) * 60
            if not (due or once):
                continue
            try:
                fresh, scored = run_tier(t, con)
                pending = dispatch(fresh, con, pending, force=force_send)
                board[t] = scored
                write_board([d for lst in board.values() for d in lst])
            except OSError as e:
                # 디스크 부족(Errno 28)은 수집이 끝난 뒤 저장 단계에서 터진다.
                # 여기서 죽으면 애써 모은 딜을 통째로 잃으므로, 정리 후 안내만 남긴다.
                if getattr(e, "errno", None) == 28:
                    try:
                        import renderer
                        n = renderer.cleanup_temp()
                        print(f"[{t}] 디스크 부족 — 임시파일 {n}개 정리했습니다. "
                              f"현재 여유 {renderer.free_mb()}MB")
                    except Exception:
                        pass
                    print(f"[{t}] 디스크를 정리한 뒤 다시 실행해 주세요.")
                else:
                    print(f"[{t}] 파일 오류: {e}")
            except Exception as e:
                print(f"[{t}] 사이클 오류: {type(e).__name__}: {e}")
            last_run[t] = time.time()
            try:
                store.set_tier_last_run(con, t, last_run[t])   # 다음 실행이 주기를 안다
            except Exception:
                pass

        if once:
            # 이번에 아무 티어도 안 돌았어도(주기 미도래) 딜보드는 병합·갱신해 둔다.
            if not tiers:
                try:
                    write_board([])
                except Exception:
                    pass
            print(f"\n{store.stats(con)}")
            print("--once 완료")
            return

        time.sleep(60)


if __name__ == "__main__":
    main()
