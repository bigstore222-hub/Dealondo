"""
affiliate.py — 제휴 링크 래퍼 (수익화)

딜온도가 뿌리는 딜 링크는 지금 전부 '맨 링크'라 수익이 0이다.
사용자가 공유한 예시(bit.ly → Mytheresa)를 분석해 보면, 그 링크는
CJ Affiliate의 클릭추적(cjevent)과 스마트링크(slink_id)로 감싸여 있었다.
즉 클릭·구매가 일어나면 퍼블리셔가 커미션을 받는 구조다.

이 모듈은 outbound 상품 URL을 '제휴 추적 링크'로 바꿔준다. 두 가지 방식을 지원한다.

1) 머천트별 딥링크 템플릿 (data/affiliate.csv)
   - CJ·Rakuten·Impact에서 특정 머천트에 승인받았을 때. 커미션이 가장 높다.
   - 도메인별로 {url} 자리표시자를 가진 템플릿을 등록한다.

2) 스마트링크 전역 폴백 (Skimlinks / Sovrn Commerce)
   - 한 번 연동으로 4만8천+ 머천트를 자동 제휴. 머천트별 승인 없이 커버.
   - 승인된 머천트가 없을 때의 안전망. 환경변수로 ID만 넣으면 된다.

**원본 url 은 절대 바꾸지 않는다**(중복제거 키로 쓰므로). 표시·발송용
`buy_url` 만 새로 만든다.

환경변수:
    RADAR_SKIMLINKS_ID   Skimlinks 퍼블리셔 ID (스마트링크)
    RADAR_SOVRN_KEY      Sovrn Commerce(구 VigLink) 키 (대안)
    RADAR_AFF_SUBID      클릭 서브ID(선택) — 유입 분석용
"""
from __future__ import annotations
import csv
import os
import re
import urllib.parse

_CFG = os.path.join(os.path.dirname(__file__), "..", "data", "affiliate.csv")

_templates_cache: dict | None = None


def _domain(url: str) -> str:
    m = re.search(r"https?://(?:www\.|m\.)?([^/]+)", url or "")
    return m.group(1).lower() if m else ""


def _load_templates() -> dict[str, str]:
    global _templates_cache
    if _templates_cache is not None:
        return _templates_cache
    out: dict[str, str] = {}
    try:
        with open(_CFG, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                dom = (row.get("domain") or "").strip().lower()
                tmpl = (row.get("template") or "").strip()
                if not dom or dom.startswith("#") or "{url}" not in tmpl:
                    continue
                out[dom] = tmpl
    except FileNotFoundError:
        pass
    _templates_cache = out
    return out


def _enc(url: str) -> str:
    return urllib.parse.quote(url, safe="")


def _skimlinks(url: str) -> str | None:
    sid = os.environ.get("RADAR_SKIMLINKS_ID")
    if not sid:
        return None
    sub = os.environ.get("RADAR_AFF_SUBID", "dealondo")
    return (f"https://go.skimresources.com/?id={sid}&xs=1"
            f"&xcust={_enc(sub)}&url={_enc(url)}")


def _sovrn(url: str) -> str | None:
    key = os.environ.get("RADAR_SOVRN_KEY")
    if not key:
        return None
    return f"https://redirect.viglink.com/?format=go&key={key}&u={_enc(url)}"


def wrap(url: str, source: str = "") -> str:
    """
    상품 URL을 제휴 추적 링크로 변환. 설정이 없으면 원본을 그대로 돌려준다
    (수익화 전에도 시스템은 정상 동작).
    우선순위: 머천트 딥링크 템플릿 > Skimlinks > Sovrn > 원본.
    """
    if not url or not url.startswith("http"):
        return url
    dom = _domain(url) or (source or "").lower()
    # 등록도메인 단위로 매칭 (sub.macys.com → macys.com)
    tmpl = _load_templates().get(dom)
    if not tmpl:
        parts = dom.split(".")
        if len(parts) > 2:
            tmpl = _load_templates().get(".".join(parts[-2:]))
    if tmpl:
        return tmpl.replace("{url}", _enc(url)).replace("{url_raw}", url)
    return _skimlinks(url) or _sovrn(url) or url


def enabled() -> bool:
    """제휴 수익화가 하나라도 설정돼 있는가 (고지 문구 표시용)."""
    return bool(_load_templates()
                or os.environ.get("RADAR_SKIMLINKS_ID")
                or os.environ.get("RADAR_SOVRN_KEY"))


if __name__ == "__main__":
    tests = [
        ("https://www.mytheresa.com/kr/ko/women/the-row-astra-bag-p00999131", "mytheresa.com"),
        ("https://www.nordstromrack.com/s/kate-spade-loafer/8725791", "nordstromrack.com"),
    ]
    print("제휴 설정:", "있음" if enabled() else "없음(원본 반환)")
    for u, s in tests:
        print(" ", s, "->", wrap(u, s)[:90])
