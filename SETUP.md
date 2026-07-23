# 사용자 설정 체크리스트

지금 코드는 **아무것도 설정하지 않아도 돌아간다**(알림은 콘솔 출력, 국내가 비교는 생략).
아래는 기능을 하나씩 켜는 순서다. 위에서부터 하시면 된다.

---

## 필수 1 — 파이썬 & Playwright 설치

Amazon·eBay·Woot(4년치 실측 1,194건, 최대 소스)는 헤드리스 브라우저가 없으면 수집이 안 된다.

```bash
cd hotdeal_radar/scraper
pip install playwright
playwright install chromium
```

확인:

```bash
python watchlist.py          # 감시 대상 185개 요약이 나오면 OK
python scheduler.py --once --tier T2
```

> 리눅스 서버에 올릴 때만 추가로 `playwright install-deps chromium` 필요.
> 윈도우는 불필요.

---

## 필수 2 — 텔레그램 알림 (5분)

이걸 안 하면 FLASH 딜을 실시간으로 못 받는다. 콘솔만 봐야 함.

1. 텔레그램에서 **@BotFather** 검색 → `/newbot` → 봇 이름 입력 → **토큰** 복사
2. 방금 만든 봇과 대화 시작 → 아무 메시지나 전송 (이걸 안 하면 봇이 나에게 못 보냄)
3. 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates` 열기 → `"chat":{"id":123456789` 에서 **chat id** 복사
4. 환경변수 설정

```bash
:: 윈도우 (현재 창에만 적용)
set TELEGRAM_BOT_TOKEN=123456:ABCdef...
set TELEGRAM_CHAT_ID=123456789

:: 영구 적용
setx TELEGRAM_BOT_TOKEN "123456:ABCdef..."
setx TELEGRAM_CHAT_ID "123456789"
```

확인: `python scheduler.py --once --tier T2` 실행 후 텔레그램에 딜이 오면 성공.

---

## 권장 3 — 네이버 쇼핑 API (국내 최저가 비교, 무료)

이걸 켜야 **H2 하드필터**가 작동한다. 즉 "싸 보이지만 관부가세 붙으면 국내가보다 비싼 딜"이 걸러진다.
실측에서 $210짜리가 면세한도 초과로 국내가보다 79% 비싸지는 사례를 잡아냈다.

1. https://developers.naver.com/apps/#/register 접속 (네이버 로그인)
2. 애플리케이션 이름 아무거나 입력
3. **사용 API: 검색** 선택
4. 환경: **WEB 설정** 선택 → URL은 `http://localhost` 입력
5. 등록하면 **Client ID / Client Secret** 발급 (무료, 하루 25,000회)

```bash
setx NAVER_CLIENT_ID "발급받은ID"
setx NAVER_CLIENT_SECRET "발급받은시크릿"
```

---

## 선택 4 — 취향에 맞게 조정할 것들

### (a) 발행 기준선 — `filter_engine.py`

지금은 **50점 이상**만 발행한다. 며칠 돌려보고 알림이 너무 많으면 올리고, 적으면 내리면 된다.

```python
def should_publish(deal) -> bool:
    return deal.passed_hard_filter and deal.score >= 50   # ← 이 숫자
```

### (b) 알림 시간대 — `scheduler.py`

4년치 실측 기준으로 넣어놨다. 생활 패턴에 맞게 바꾸시면 된다.

```python
PUBLISH_SLOTS = [(8, 10), (13, 16), (20, 23)]
```

> FLASH(85점+)는 이 슬롯과 무관하게 즉시 발송된다.

### (c) 관심 카테고리 가중치 — `filter_engine.py`

4년간 실제로 올린 비중으로 정했다. 요즘 관심사가 달라졌으면 조정.

```python
CATEGORY_SCORES = {
    "premium_fashion": 20,   # 프리미엄 패션
    "sports_outdoor": 17,
    "electronics": 15,
    "kids": 15,              # 유아/키즈
    "watch_misc": 10,
    "etc": 5,
}
```

브랜드가 `etc`(5점)로 빠지면 발행이 잘 안 되므로, 자주 사는 브랜드가 빠져 있으면
`CATEGORY_KEYWORDS` 에 추가하시면 된다.

### (d) 폴링 주기 — `watchlist.py`

```python
POLL_MINUTES = {"T1": 15, "T2": 30, "T3": 120, "T4": 360}
```

> 너무 짧게 잡으면 차단 위험이 커진다. T1 15분은 충분히 보수적인 값이다.

### (e) 감시 대상 추가/제외 — `data/watchlist.csv`

185개가 등록돼 있다. 안 쓰는 사이트는 `crawl_allowed` 를 `NO` 로 바꾸면 제외된다.
새 사이트를 넣으려면 행을 추가하고 `tier`, `domain`, `crawl_allowed=YES` 를 채우면 된다.

---

## 상시 실행

설정이 끝나면 이렇게 띄워두면 된다.

```bash
cd hotdeal_radar/scraper
set RADAR_SCROLL_WAIT_MS=700
python scheduler.py
```

딜보드는 따로 열면 된다:

```bash
cd hotdeal_radar/web
python -m http.server 8000      # http://localhost:8000
```

---

## 내가 못 해둔 것 (직접 판단이 필요한 부분)

| 항목 | 왜 남았나 |
|---|---|
| **Slickdeals 공식 API** | `corp-site.slickdeals.net/api-sales` 에서 파트너 신청이 필요하다. 승인되면 `SLICKDEALS_API_KEY` 만 넣으면 코드는 이미 준비돼 있다. |
| **어필리에이트 제휴 가입** | 워치리스트 185개 중 **65개가 봇 차단**(Macy's·Adidas 등)이라 직접 크롤이 안 된다. CJ Affiliate·Rakuten·Impact 중 하나에 퍼블리셔로 가입하면 이 사이트들의 공식 딜 피드를 받을 수 있다. 사업자/사이트 정보가 필요해 대신 못 한다. |
| **Amazon PA-API** | Associates 계정이 필요하다. 지금은 스크래핑으로 15건씩 잡히지만, 아마존이 가격을 불규칙하게 내려줘서 재시도로 버티는 중이다. 장기적으로는 API가 맞다. |
| **Nike 파서** | 4년 40건으로 우선순위가 낮아 남겨뒀다. 필요하면 말씀해 주시면 추가한다. |
| **오탐 튜닝** | 며칠 돌려봐야 "이건 왜 알림 왔지" 싶은 게 나온다. 그때 조정하는 게 맞다. |
