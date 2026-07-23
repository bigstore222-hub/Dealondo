# Rakuten Advertising 제휴 가입 절차

이걸 통과하면 **Macy's(실측 84건)와 The Outnet(23건)** 의 개별 상품 데이터 피드를
정식으로 받을 수 있다. 봇 차단과 무관하게, 리테일러가 직접 제공하는 데이터다.

전체 소요: **가입 심사 2~3영업일 + 리테일러별 승인 며칠~수 주**

---

## 준비물 체크리스트

| 항목 | 상태 | 비고 |
|---|---|---|
| 라이브 웹사이트 URL | **필요** | 아래 0단계에서 해결 |
| 이메일 | 있음 | |
| 신분/사업자 정보 | 개인도 가능 | 사업자 없어도 개인으로 등록 가능 |
| 세금 정보 (W-8BEN) | 가입 중 입력 | 서류 업로드 불필요, 화면에서 입력만 |
| 해외 송금 계좌 | 나중에 | 수익 발생 시점에 등록해도 됨 |

> **핵심 관문은 "라이브 웹사이트"다.** "공사중"이거나 접속 안 되는 사이트는 거절된다.

---

## 0단계 — 딜보드를 웹사이트로 공개 (30분)

이미 만들어 둔 딜보드(`web/index.html`)를 GitHub Pages에 올리면
무료로 공개 URL이 생긴다. 이게 심사용 웹사이트가 된다.

1. https://github.com 가입 (무료)
2. 우측 상단 `+` → **New repository**
   - Repository name: `hotdeal-radar` (아무거나)
   - **Public** 선택 (Private은 Pages가 안 된다)
   - **Add a README file** 체크
   - Create repository
3. 생성된 페이지에서 **Add file → Upload files**
   - `web/index.html` 과 `web/deals.json` 두 파일을 끌어다 놓기
   - 아래 **Commit changes** 클릭
4. 상단 **Settings** 탭 → 좌측 **Pages** 메뉴
   - Source: `Deploy from a branch`
   - Branch: `main` / `/ (root)` 선택 → **Save**
5. 1~2분 뒤 새로고침하면 주소가 나온다

```
https://본인아이디.github.io/hotdeal-radar/
```

이 주소로 접속해서 딜보드가 실제로 보이는지 확인한다.
**보이지 않으면 다음 단계로 넘어가지 말 것** — 심사에서 거절된다.

> 참고: `deals.json` 은 수동 업로드라 자동 갱신되지 않는다.
> 심사 통과용으로는 충분하고, 나중에 자동 배포를 붙일 수 있다.

### 심사 통과율을 높이려면

빈 페이지보다는 사이트의 성격이 드러나는 게 좋다. README나 페이지에
한두 문단이라도 이런 내용을 넣어두면 도움이 된다.

- 어떤 사이트인지 (해외직구 딜 큐레이션)
- 어떤 카테고리를 다루는지 (패션·스포츠·전자기기)
- 누구를 위한 것인지

---

## 1단계 — Rakuten 가입 (15분)

1. https://rakutenadvertising.com 접속
2. 우측 상단 **Become a Publisher** 클릭
3. 이메일, 이름, 비밀번호 입력 → **Register**
4. **인증 메일 확인** — 받은 메일의 링크 클릭
5. **Get started** 클릭 후 순서대로 입력

| 항목 | 입력값 |
|---|---|
| Country | South Korea |
| Company name | 개인이면 본인 이름 |
| Business classification | Individual / Sole proprietor |
| Primary business model | **Content / Blog** 또는 **Deal / Coupon site** |
| Primary channel URL | 0단계에서 만든 GitHub Pages 주소 |
| Channel name | 사이트 이름 (예: Hotdeal Radar) |
| Content categories | Fashion, Apparel, Sports, Electronics 등 선택 |

6. 소셜 계정 연결은 **선택사항** — 없으면 건너뛴다
7. 세금 정보 입력 화면에서 **비미국 거주자(W-8BEN)** 로 진행
   - 별도 서류 업로드 없이 화면 입력으로 끝난다
   - 이걸 안 하면 커미션에서 30% 원천징수된다
8. Publisher Membership Agreement 동의 → **Submit Registration**

**심사는 보통 2~3영업일.** 승인되면 환영 메일이 온다.

---

## 2단계 — Macy's / The Outnet 개별 신청

Rakuten 가입 승인 ≠ 리테일러 승인이다. **광고주별로 따로 신청**해야 한다.

1. Rakuten 대시보드 로그인
2. **Advertisers → Find Advertisers** 메뉴
3. 검색창에 `Macy's` 입력 → 프로그램 선택 → **Apply**
4. 같은 방식으로 `The Outnet` 신청
   - 아웃넷은 지역별로 프로그램이 나뉜다. **US** 프로그램을 고른다
5. 신청 시 사이트 소개를 적는 칸이 있으면 성실히 채운다
   - 어떤 트래픽을 보내는지, 어떤 방식으로 소개하는지

**승인은 며칠~수 주.** 광고주가 직접 심사하며, 트래픽이 없는 신규 퍼블리셔는
거절될 수 있다. 거절돼도 사이트를 키운 뒤 재신청 가능하다.

---

## 3단계 — 데이터 피드 접근 신청

상품 데이터 피드(Product Catalog)는 **별도 승인**이 하나 더 필요하다.

1. 대시보드에서 **Ads → Product Catalog** 또는 **Data Feeds** 메뉴
2. 피드 접근 권한을 신청한다 (기술 구현 승인)
3. 승인되면 **SFTP 접속 정보**를 받는다
4. 광고주별로 피드 사용 승인을 또 받아야 한다

피드 형식은 CSV / TSV / Google Product Feed 등을 지원한다.

---

## 4단계 — 우리 시스템에 연결

여기까지 오면 알려주면 된다. `sources.py` 에 Rakuten 피드 어댑터를 붙이면
기존 하드필터·스코어링·텔레그램 알림이 **그대로 재사용**된다.
새로 만들 건 피드를 읽어 `Deal` 객체로 변환하는 부분뿐이다.

받게 될 데이터:
- 상품명, 브랜드, 카테고리
- 정가 / 세일가
- 재고 상태
- 상품 이미지, 상품 URL

지금 크롤링으로 얻는 것보다 **정확하고 안정적**이다.

---

## 솔직한 기대치

- **오늘 당장은 안 된다.** 최소 1~2주, 길면 한 달이다.
  오늘 아웃넷 85% 재입고 같은 건 직접 보시는 게 맞다.
- **거절될 수도 있다.** 트래픽 실적이 없는 신규 사이트는 광고주가 거절하기도 한다.
  Rakuten 네트워크 가입 자체는 통과율이 높지만, Macy's 같은 대형 광고주는 까다롭다.
- **커미션은 부수적이다.** 목적은 수익이 아니라 **데이터 피드 접근**이다.
  다만 제휴 링크를 통해 구매가 일어나면 커미션이 붙는 구조라,
  나중에 딜보드를 공개 운영한다면 운영비 정도는 나올 수 있다.

### 그래도 할 가치가 있는 이유

Macy's 84건 + The Outnet 23건 = **실측 107건**. 차단으로 잃은 물량의 상당 부분이다.
게다가 Rakuten에는 이 둘 말고도 차단된 사이트가 여럿 들어있어서,
한 번 뚫어두면 나머지도 같은 경로로 붙일 수 있다.

---

## 대안: Awin / Partnerize

The Outnet은 **Awin**과 **Partnerize**에도 있다.
Rakuten이 거절되면 이쪽을 시도해 볼 수 있다.

- Awin: 가입비 $5 (승인 시 환급) — 신규 퍼블리셔에게 상대적으로 관대한 편
- Partnerize: The Outnet UK/EU 담당

Macy's는 Rakuten 중심이라 대체재가 마땅치 않다.
