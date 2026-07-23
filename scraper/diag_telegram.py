"""텔레그램 알림이 안 오는 원인 진단 + 초기화"""
import os, sys, sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store, notify, filter_engine as fe
from datetime import datetime

print("=" * 52)
print("  텔레그램 알림 진단")
print("=" * 52)

tok = os.environ.get("TELEGRAM_BOT_TOKEN")
cid = os.environ.get("TELEGRAM_CHAT_ID")
print(f"\n[1] 환경변수")
print(f"    토큰   : {'설정됨 (' + tok[:12] + '...)' if tok else '없음 <-- 문제!'}")
print(f"    대화ID : {cid if cid else '없음 <-- 문제!'}")
if not (tok and cid):
    print("\n    -> 이 창을 닫고 5_텔레그램설정.bat 을 먼저 실행하세요.")
    print("       이미 했다면, 모든 명령창을 닫고 이 파일을 다시 실행하세요.")
    print("       (환경변수는 새로 여는 창부터 적용됩니다)")

con = store.connect()
n = con.execute("SELECT COUNT(*) FROM notified").fetchone()[0]
print(f"\n[2] 중복 방지 기록")
print(f"    이미 '알림 보냄'으로 기록된 딜: {n}건")
if n:
    print("    -> 테스트로 이미 처리된 딜은 다시 알리지 않습니다.")
    print("       그래서 '신규 0건'이 나와 메시지가 안 올 수 있습니다.")

h = datetime.now().hour
slots = [(8,10),(13,16),(20,23)]
inslot = any(a <= h < b for a,b in slots)
print(f"\n[3] 발행 시간대")
print(f"    현재 {h}시 / 정기 슬롯(8-10,13-16,20-23) 안에 있나: {'예' if inslot else '아니오'}")
if not inslot:
    print("    -> 슬롯 밖이면 FLASH가 아닌 딜은 대기열에 쌓입니다.")

if tok and cid:
    print(f"\n[4] 실제 발송 테스트")
    ok = notify.send("<b>진단 테스트</b>\n이 메시지가 보이면 연결은 정상입니다.")
    print(f"    발송 결과: {'성공 - 텔레그램을 확인하세요' if ok else '실패'}")

print("\n" + "=" * 52)
ans = input("중복 방지 기록을 초기화할까요? (딜을 다시 받아보려면 y) [y/N]: ").strip().lower()
if ans == "y":
    con.execute("DELETE FROM notified")
    con.commit()
    print("초기화 완료. 이제 2_테스트실행.bat 을 실행하면 딜이 다시 알림으로 갑니다.")
else:
    print("유지했습니다.")
print("=" * 52)
