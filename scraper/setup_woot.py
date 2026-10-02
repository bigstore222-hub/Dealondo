"""
setup_woot.py — Woot 공식 Developer API 키 설정 도우미

키가 아직 없다면:
  1) https://forums.woot.com/t/request-developer-api-key/734283 접속
     (forums.woot.com 계정 필요 — 없으면 가입)
  2) 그 글에 댓글(답글)로 "API 키를 신청합니다" 정도만 남기면 된다.
     형식 제한은 없다. 이메일 주소나 키를 포럼에 올리지 말 것(공지 사항).
  3) 키는 **이메일이 아니라 포럼 쪽지(Private Message)**로 온다.
     우측 상단 봉투 아이콘(알림/쪽지함)을 확인할 것.
  4) 키는 매주 단위로 생성해서 보내준다고 공지돼 있다 — 하루 이틀 안 와도 정상.

키를 받았으면 이 스크립트를 실행:
  1) 간단한 조회(Featured 피드)로 키가 동작하는지 확인
  2) 윈도우 환경변수에 영구 저장 (setx WOOT_API_KEY)

실행: 16_WootAPI설정.bat
"""
from __future__ import annotations
import json, os, subprocess, sys, urllib.request, urllib.error

API_BASE = "https://developer.woot.com"


def _call(key: str, path: str, timeout: int = 10):
    req = urllib.request.Request(
        API_BASE + path,
        headers={"x-api-key": key, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def save_env(key: str, value: str) -> bool:
    """윈도우 사용자 환경변수에 영구 저장."""
    try:
        subprocess.run(["setx", key, value], check=True,
                       capture_output=True, text=True)
        os.environ[key] = value
        return True
    except Exception as e:
        print(f"   저장 실패 ({key}): {e}")
        return False


def main() -> int:
    print("=" * 54)
    print("  Woot 공식 API 키 설정")
    print("=" * 54)
    print("""
아직 키가 없다면:

  1. https://forums.woot.com/t/request-developer-api-key/734283 접속
     (포럼 계정 로그인 필요)
  2. 그 글에 댓글로 신청 (형식 자유 — "API 키 신청합니다" 정도면 충분)
  3. 키는 이메일이 아니라 포럼 쪽지(우측 상단 봉투 아이콘)로 온다.
  4. 매주 단위로 발급되니 하루 이틀 안에 안 와도 정상이다.
""")

    key = input("발급받은 API 키를 붙여넣고 엔터를 누르세요\n"
                "(마우스 오른쪽 클릭으로 붙여넣기 됩니다)\n\n키: ").strip()
    if not key:
        print("\n[!] 키를 입력하지 않았습니다.")
        return 1

    print("\n키 확인 중 (Featured 피드 조회)...")
    try:
        res = _call(key, "/feed/Featured?page=1")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        print(f"\n[!] 인증 실패 (HTTP {e.code})")
        print(f"    {body}")
        if e.code in (401, 403):
            print("    키가 틀렸거나 아직 승인 전일 수 있습니다. 포럼 쪽지함을 다시 확인해 주세요.")
        return 1
    except Exception as e:
        print(f"\n[!] 오류: {type(e).__name__}: {e}")
        return 1

    items = res.get("Items") if isinstance(res, dict) else res
    print(f"   확인 완료 (현재 진행 중인 딜 {len(items or [])}건)")
    if items:
        sample = items[0]
        print(f"   예시: {sample.get('Title', '?')[:50]}")

    print("\n환경변수에 저장 중...")
    if save_env("WOOT_API_KEY", key):
        print("   저장 완료 (다음부터 자동으로 적용됩니다)")

    print("\n" + "=" * 54)
    print("  설정 완료")
    print("  이제 열린 명령창을 모두 닫고 다시 실행하면")
    print("  Woot이 헤드리스 렌더링 대신 공식 API로 수집됩니다")
    print("=" * 54)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n취소되었습니다.")
        sys.exit(1)
