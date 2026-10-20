"""
threads_api.py — 메타 공식 Threads API 최소 클라이언트 (표준 라이브러리만 사용)

문서: https://developers.facebook.com/documentation/threads
- 게시: 컨테이너 생성(POST /{uid}/threads) → 처리 대기 → 게시(POST /{uid}/threads_publish)
- 첫 답글: 같은 방식에 reply_to_id만 추가 (내 글에 다는 답글은 기본 권한으로 가능)
- 토큰: 단기(1시간) → 장기(60일) 교환, 장기 토큰은 24시간 이상 지난 뒤 60일 연장 가능
"""
from __future__ import annotations
import json, time, urllib.parse, urllib.request, urllib.error

GRAPH = "https://graph.threads.net"
VER = "v1.0"


class ThreadsError(RuntimeError):
    pass


def _req(method: str, path: str, params: dict, timeout: int = 30) -> dict:
    url = f"{GRAPH}/{path.lstrip('/')}"
    data = None
    if method == "GET":
        url += "?" + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            err = json.loads(body).get("error", {})
            msg = f"HTTP {e.code} · {err.get('type','')} {err.get('code','')}: {err.get('message', body[:200])}"
        except Exception:
            msg = f"HTTP {e.code} · {body[:200]}"
        raise ThreadsError(msg) from None


# ── 토큰 ──────────────────────────────────────────────
def exchange_long_lived(short_token: str, app_secret: str) -> dict:
    """단기 토큰 → 장기(60일) 토큰. 반환: {access_token, token_type, expires_in}"""
    return _req("GET", "access_token", {
        "grant_type": "th_exchange_token",
        "client_secret": app_secret,
        "access_token": short_token,
    })


def refresh_long_lived(token: str) -> dict:
    """장기 토큰 연장(발급 24시간 이후, 만료 전). 반환: {access_token, token_type, expires_in}"""
    return _req("GET", "refresh_access_token", {
        "grant_type": "th_refresh_token",
        "access_token": token,
    })


# ── 조회 ──────────────────────────────────────────────
def me(token: str) -> dict:
    return _req("GET", f"{VER}/me", {"fields": "id,username,name", "access_token": token})


def debug_token(token: str) -> dict:
    """토큰 정보: expires_at(유닉스 시각, 0이면 무기한), scopes, is_valid 등."""
    return _req("GET", f"{VER}/debug_token", {"input_token": token,
                                              "access_token": token}).get("data", {})


def my_posts(uid: str, token: str, limit: int = 50) -> list[dict]:
    res = _req("GET", f"{VER}/{uid}/threads", {
        "fields": "id,text,timestamp,permalink,media_type",
        "limit": str(limit), "access_token": token,
    })
    return res.get("data", [])


def permalink(media_id: str, token: str) -> str:
    try:
        return _req("GET", f"{VER}/{media_id}", {"fields": "permalink",
                                                 "access_token": token}).get("permalink", "")
    except ThreadsError:
        return ""


# ── 게시 ──────────────────────────────────────────────
def _create(uid: str, token: str, text: str | None, image_url: str | None = None,
            reply_to: str | None = None, topic_tag: str | None = None, *,
            carousel_item: bool = False, children: list[str] | None = None) -> str:
    p = {"access_token": token,
         "media_type": "CAROUSEL" if children else ("IMAGE" if image_url else "TEXT")}
    if text:
        p["text"] = text
    if image_url:
        p["image_url"] = image_url
    if carousel_item:
        p["is_carousel_item"] = "true"
    if children:
        p["children"] = ",".join(children)
    if reply_to:
        p["reply_to_id"] = reply_to
    if topic_tag:
        # 글 하나에 주제 태그 1개(1~50자, 마침표·& 불가). 그 주제 피드에 노출되고 계정 주제 파악에 쓰인다.
        p["topic_tag"] = topic_tag.replace(".", "").replace("&", "")[:50]
    return _req("POST", f"{VER}/{uid}/threads", p)["id"]


def _wait_ready(container_id: str, token: str, max_wait: int = 90) -> None:
    """컨테이너 처리 완료 대기. 메타 권장: 평균 30초 대기 후 게시."""
    waited = 0
    time.sleep(5)
    while waited < max_wait:
        st = _req("GET", f"{VER}/{container_id}",
                  {"fields": "status,error_message", "access_token": token})
        s = st.get("status")
        if s == "FINISHED":
            return
        if s in ("ERROR", "EXPIRED"):
            raise ThreadsError(f"컨테이너 처리 실패: {s} {st.get('error_message','')}")
        time.sleep(5)
        waited += 5
    raise ThreadsError("컨테이너 처리 대기 시간 초과")


def _publish(uid: str, token: str, container_id: str) -> str:
    return _req("POST", f"{VER}/{uid}/threads_publish",
                {"creation_id": container_id, "access_token": token})["id"]


def publish_post(uid: str, token: str, text: str, image_url: str | None = None,
                 reply_to: str | None = None, topic_tag: str | None = None,
                 images: list[str] | None = None) -> str:
    """
    글(또는 답글) 하나를 게시하고 게시물 ID를 돌려준다.
    images 에 2~20장을 주면 넘겨보는 이미지(캐러셀)로 올린다.
    주제 태그 때문에 거절되면 태그 없이 한 번 더 시도한다(게시 자체가 막히지 않게).
    """
    if len(text) > 500:
        raise ThreadsError(f"본문이 500자를 넘습니다 ({len(text)}자)")
    def make(tag):
        if images and len(images) >= 2:
            kids = [_create(uid, token, None, u, carousel_item=True) for u in images[:20]]
            for k in kids:
                _wait_ready(k, token)
            return _create(uid, token, text, None, reply_to, tag, children=kids)
        return _create(uid, token, text, image_url, reply_to, tag)
    try:
        cid = make(topic_tag)
    except ThreadsError as e:
        if not topic_tag:
            raise
        print(f"  [threads] 주제 태그 '{topic_tag}' 로 거절됨 → 태그 없이 재시도: {e}")
        cid = make(None)
    _wait_ready(cid, token)
    return _publish(uid, token, cid)
