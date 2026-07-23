"""
setup_telegram.py — 텔레그램 알림 설정 도우미

봇 토큰만 붙여넣으면 나머지를 자동으로 처리한다:
  1) 토큰 유효성 확인
  2) chat id 자동 탐지 (봇에게 보낸 메시지에서)
  3) 윈도우 환경변수에 영구 저장 (setx)
  4) 테스트 메시지 발송

실행: 5_텔레그램설정.bat
"""
from __future__ import annotations
import json, os, subprocess, sys, time, urllib.request, urllib.parse

API = "https://api.telegram.org/bot{token}/{method}"


def call(token: str, method: str, params: dict | None = None, timeout: int = 10):
    url = API.format(token=token, method=method)
    data = urllib.parse.urlencode(params).encode() if params else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data),
                                    timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}


def find_chat_id(token: str, wait_sec: int = 90) -> str | None:
    """
    봇에게 보낸 메시지에서 chat id를 찾는다.
    아직 메시지가 없으면 보낼 때까지 기다린다.
    """
    print("\n텔레그램 앱에서 방금 만든 봇을 찾아 아무 메시지나 보내주세요.")
    print("(예: 안녕  또는  /start)")
    print(f"\n기다리는 중... 최대 {wait_sec}초\n")

    deadline = time.time() + wait_sec
    dots = 0
    while time.time() < deadline:
        res = call(token, "getUpdates")
        if res.get("ok"):
            for upd in reversed(res.get("result", [])):
                msg = upd.get("message") or upd.get("channel_post") or {}
                chat = msg.get("chat") or {}
                if chat.get("id"):
                    name = chat.get("first_name") or chat.get("title") or ""
                    print(f"\n메시지를 받았습니다! (보낸 사람: {name})")
                    return str(chat["id"])
        dots = (dots + 1) % 4
        print("\r   대기 중" + "." * dots + "   ", end="", flush=True)
        time.sleep(3)
    return None


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
    print("=" * 50)
    print("  텔레그램 알림 설정")
    print("=" * 50)

    token = input("\n봇 토큰을 붙여넣고 엔터를 누르세요\n"
                  "(마우스 오른쪽 클릭으로 붙여넣기 됩니다)\n\n토큰: ").strip()
    if not token or ":" not in token:
        print("\n[!] 토큰 형식이 아닙니다. 123456789:AAH... 형태여야 합니다.")
        return 1

    print("\n토큰 확인 중...")
    me = call(token, "getMe")
    if not me.get("ok"):
        print(f"\n[!] 토큰이 유효하지 않습니다: {me.get('error') or me.get('description')}")
        print("    BotFather에서 받은 토큰을 정확히 붙여넣었는지 확인해 주세요.")
        return 1
    bot_name = me["result"].get("username", "?")
    print(f"   확인 완료: @{bot_name}")

    chat_id = find_chat_id(token)
    if not chat_id:
        print("\n[!] 메시지를 받지 못했습니다.")
        print(f"    텔레그램에서 @{bot_name} 을 검색해 대화를 시작한 뒤")
        print("    이 파일을 다시 실행해 주세요.")
        return 1
    print(f"   대화 ID: {chat_id}")

    print("\n환경변수에 저장 중...")
    ok1 = save_env("TELEGRAM_BOT_TOKEN", token)
    ok2 = save_env("TELEGRAM_CHAT_ID", chat_id)
    if ok1 and ok2:
        print("   저장 완료 (다음부터 자동으로 적용됩니다)")

    print("\n테스트 메시지 발송 중...")
    res = call(token, "sendMessage", {
        "chat_id": chat_id,
        "parse_mode": "HTML",
        "text": ("🛰 <b>핫딜 레이더 연결 완료</b>\n\n"
                 "이제 조건에 맞는 딜이 나오면 여기로 알려드립니다.\n\n"
                 "🔴 FLASH — 즉시 알림\n"
                 "🟠 HOT / 🟡 STEADY — 08~10시, 13~16시, 20~23시"),
    })
    if res.get("ok"):
        print("   발송 성공! 텔레그램을 확인해 보세요.")
    else:
        print(f"   발송 실패: {res.get('description') or res.get('error')}")
        return 1

    print("\n" + "=" * 50)
    print("  설정이 모두 끝났습니다")
    print("  이제 3_상시감시시작.bat 을 실행하면")
    print("  딜을 찾을 때마다 텔레그램으로 알려드립니다")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n취소되었습니다.")
        sys.exit(1)
