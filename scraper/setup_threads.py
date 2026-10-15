"""
setup_threads.py — 스레드(@deal_ondo) 자동 게시 토큰 설정 도우미

준비물(메타 개발자 앱에서 받은 것):
  - Threads App Secret  (Use cases > Access the Threads API > Customize > Settings)
  - 액세스 토큰          (같은 화면 맨 아래 User Token Generator, 또는 Graph API Explorer)

하는 일:
  1) 토큰을 60일짜리 장기 토큰으로 교환 (이미 장기 토큰이면 그대로 사용)
  2) 계정 확인 (사용자 ID·아이디 조회) — 글은 올리지 않는다
  3) 이 PC 환경변수에 저장 (setx THREADS_ACCESS_TOKEN / THREADS_USER_ID)
  4) GitHub Secrets 에 넣을 값을 하나씩 클립보드로 복사해 준다
  5) 오늘 무엇이 올라갈지 미리보기(--dry-run)

실행: 17_스레드설정.bat
"""
from __future__ import annotations
import datetime as dt, os, subprocess, sys

import threads_api as T


def save_env(key: str, value: str) -> bool:
    try:
        subprocess.run(["setx", key, value], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.environ[key] = value
        return True
    except Exception as e:
        print(f"   저장 실패 ({key}): {e}")
        os.environ[key] = value
        return False


def to_clipboard(text: str) -> bool:
    try:
        subprocess.run(["clip"], input=text.encode("utf-16le"), check=True)
        return True
    except Exception:
        return False


def check_token(token: str) -> bool:
    """만료일·권한을 출력. 게시 가능한 상태면 True."""
    try:
        d = T.debug_token(token)
    except T.ThreadsError as e:
        print(f"   토큰 정보 조회 실패: {e}")
        return True  # 조회만 실패 — 게시 가능 여부는 알 수 없으니 막지 않는다
    exp = int(d.get("expires_at") or 0)
    scopes = d.get("scopes") or []
    if exp:
        left = dt.datetime.fromtimestamp(exp) - dt.datetime.now()
        when = dt.datetime.fromtimestamp(exp).strftime("%Y-%m-%d %H:%M")
        if left.total_seconds() < 6 * 3600:
            print(f"   [!] 단기 토큰입니다 — {when} 만료 (약 {int(left.total_seconds()//60)}분 남음)")
            print("       App Secret 칸에 'Threads App Secret'(일반 App Secret 아님)을 넣고 다시 실행하세요.")
        else:
            print(f"   장기 토큰 — {when} 만료 (약 {left.days}일 남음)")
    else:
        print("   만료일 정보 없음")
    print(f"   권한: {', '.join(scopes) or '(알 수 없음)'}")
    ok = (not scopes) or ("threads_content_publish" in scopes)
    if not ok:
        print("   [!] threads_content_publish 권한이 없습니다 → 앱 Permissions에서 Add 후 토큰을 다시 발급하세요.")
    return ok


def main() -> int:
    if "--check" in sys.argv:
        tok = os.environ.get("THREADS_ACCESS_TOKEN", "")
        if not tok:
            print("이 PC에 저장된 토큰이 없습니다. 17_스레드설정.bat 을 먼저 실행하세요.")
            return 1
        print("저장된 스레드 토큰 확인 중...")
        try:
            info = T.me(tok)
            print(f"   @{info.get('username')} (사용자 ID {info.get('id')})")
        except T.ThreadsError as e:
            print(f"   [!] 토큰이 동작하지 않습니다: {e}")
            return 1
        check_token(tok)
        return 0

    print("=" * 58)
    print("  스레드(@deal_ondo) 자동 게시 설정")
    print("=" * 58)
    print("\n(붙여넣기는 마우스 오른쪽 클릭)\n")
    secret = input("1) Threads App Secret (모르면 그냥 엔터): ").strip()
    token = input("2) 액세스 토큰 (TH로 시작): ").strip()
    if not token:
        print("\n[!] 토큰을 입력하지 않았습니다.")
        return 1

    expires = None
    if secret:
        print("\n장기 토큰(60일)으로 교환 중...")
        try:
            res = T.exchange_long_lived(token, secret)
            token = res["access_token"]
            expires = int(res.get("expires_in", 0))
            print("   교환 완료")
        except T.ThreadsError as e:
            print(f"   교환하지 않고 그대로 씁니다 ({e})")
            print("   → 이미 장기 토큰이면 정상입니다. 1시간 뒤 인증 오류가 나면 App Secret을 넣고 다시 실행하세요.")
    else:
        print("\nApp Secret 없이 진행 — 토큰이 1시간짜리라면 곧 만료됩니다.")

    print("\n계정 확인 중...")
    try:
        info = T.me(token)
    except T.ThreadsError as e:
        print(f"\n[!] 토큰 확인 실패: {e}")
        print("    토큰을 다시 발급받거나, 스레드에서 테스터 초대를 수락했는지 확인해 주세요.")
        return 1
    uid, uname = info.get("id", ""), info.get("username", "")
    print(f"   @{uname} (사용자 ID {uid})")
    if uname and uname != "deal_ondo":
        print("   [주의] @deal_ondo 가 아닌 계정입니다. 이 계정에 글이 올라갑니다.")
    check_token(token)

    print("\n이 PC 환경변수에 저장 중...")
    save_env("THREADS_ACCESS_TOKEN", token)
    save_env("THREADS_USER_ID", uid)

    print("\n" + "-" * 58)
    print("  GitHub Secrets 등록")
    print("  github.com/bigstore222-hub/Dealondo → Settings →")
    print("  Secrets and variables → Actions → New repository secret")
    print("-" * 58)
    for name, val in (("THREADS_USER_ID", uid), ("THREADS_ACCESS_TOKEN", token)):
        copied = to_clipboard(val)
        print(f"\n  Name: {name}")
        if copied:
            print("  Secret: (클립보드에 복사됨 — Secret 칸에 Ctrl+V)")
        else:
            print(f"  Secret: {val}")
        input("  등록했으면 엔터 ▶ ")
    to_clipboard(" ")  # 토큰이 클립보드에 남지 않게

    print("\n" + "=" * 58)
    print("  미리보기: 다음 실행 때 올라갈 글 (실제로 올리지 않음)")
    print("=" * 58)
    try:
        import threads_post
        threads_post.run_post(dry=True, force=False)
    except Exception as e:
        print(f"(미리보기 실패: {e})")

    print("\n" + "=" * 58)
    print("  설정 완료 — 매일 20:20(한국시간) 무렵 자동으로 올라갑니다")
    print("  바로 올려보려면: GitHub → Actions → Dealondo Threads → Run workflow")
    print("=" * 58)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n취소되었습니다.")
        sys.exit(1)
