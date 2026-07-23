"""
sources_email.py — 이메일 뉴스레터 기반 딜 수집

Macy's·The Outnet처럼 봇 차단이 강한 사이트를 합법적으로 잡는 경로.

원리:
  사용자가 해당 리테일러의 세일 알림 메일을 직접 구독하고,
  그 메일함을 본인 자격증명으로 읽어 딜 정보를 추출한다.
  사이트에 자동 접근하는 게 아니라 '내가 받은 내 메일을 내가 읽는' 것이므로
  리테일러 약관과 무관하다.

장점:
  - 승인 대기 없이 즉시 사용 가능
  - 플래시 세일은 리테일러가 메일로 먼저 알리므로 세일 페이지 크롤보다 빠를 수 있다
  - 봇 차단과 완전히 무관

한계:
  - 개별 상품가가 아니라 '세일 공지'(예: 추가 30% 할인) 수준인 경우가 많다
  - 메일 구독을 해둬야 한다

설정 (Gmail 기준):
  1) 전용 Gmail 계정을 하나 만든다 (기존 메일과 섞이지 않게)
  2) 그 계정으로 Macy's / The Outnet 뉴스레터를 구독한다
  3) Google 계정 > 보안 > 2단계 인증 활성화 > '앱 비밀번호' 발급
  4) 환경변수 설정:
       RADAR_IMAP_USER=your@gmail.com
       RADAR_IMAP_PASS=앱비밀번호16자리
       (RADAR_IMAP_HOST 는 기본값 imap.gmail.com)

실행: 9_이메일수집.bat
"""
from __future__ import annotations
import email, imaplib, os, re, html as htmlmod
from email.header import decode_header, make_header
from datetime import datetime, timedelta, timezone

from filter_engine import Deal

IMAP_HOST = os.environ.get("RADAR_IMAP_HOST", "imap.gmail.com")

# 발신 도메인 → 리테일러 도메인 매핑.
# 여기에 없는 발신자는 무시하므로, 개인 메일이 섞여도 안전하다.
# 4년 실측 게시 이력 순으로 등록.
# 서브도메인(email.xxx.com, e.xxx.com 등)은 자동 매칭되므로 루트 도메인만 적으면 된다.
#
# 코드는 구독한 곳에서만 온다. 등록만 해두고 구독을 안 하면 아무것도 안 잡힌다.
# 반대로 여기 없는 곳을 구독하면 메일이 와도 무시된다 — 필요하면 여기 추가할 것.
SENDER_MAP = {d: d for d in [
    # 차단 사이트 — 이메일이 유일한 경로라 우선순위 최상
    "macys.com",            # 실측 84건
    "adidas.com",           # 40건
    "theoutnet.com",        # 23건
    "rei.com",              # 18건
    "levi.com",             # 14건
    "asos.com",             # 12건
    "dickssportinggoods.com",  # 11건
    "saksfifthavenue.com",  # 7건
    "bloomingdales.com",    # 4건
    "gilt.com", "revolve.com", "urbanoutfitters.com", "lululemon.com",
    "neimanmarcus.com", "newbalance.com", "lacoste.com", "converse.com",
    "finishline.com", "timberland.com", "patagonia.com", "clinique.com",
    "net-a-porter.com", "mrporter.com", "mytheresa.com", "ssense.com",
    "harrods.com", "selfridges.com", "farfetch.com",

    # 크롤 가능한 곳도 구독 가치가 있다 — 코드는 페이지에 안 뜨고 메일로만 온다
    "amazon.com",           # 788건
    "woot.com",             # 206건
    "ebay.com",             # 160건
    "nordstromrack.com",    # 42건
    "nike.com",             # 40건
    "columbia.com",         # 27건
    "zappos.com",           # 23건
    "jomashop.com",         # 20건
    "nordstrom.com",        # 15건
    "ashford.com", "merrell.com", "underarmour.com", "adorama.com",
    "bhphotovideo.com", "eddiebauer.com", "shopbop.com", "jcrew.com",
    "madewell.com", "backcountry.com", "sierra.com", "6pm.com",
    "footlocker.com", "asics.com", "arcteryx.com", "carhartt.com",
    "huckberry.com", "bestbuy.com", "ae.com", "gap.com", "oldnavy.com",
    "yoox.com", "victoriassecret.com",   # 2026-07-22 신규
]}

# 제목/본문에서 할인 정보 뽑기
# '추가 할인'은 extra/additional 이 명시된 것만 인정한다.
# 이걸 느슨하게 두면 제목의 "Up to 80% Off"를 추가할인으로 오인해
# 기본 세일폭과 같은 값이 되어 합성이 무력화된다(실제로 발생).
_EXTRA = re.compile(r"(?:extra|additional|추가)\s*(\d{1,2})\s*%", re.I)
_UPTO = re.compile(r"up\s*to\s*(\d{1,2})\s*%", re.I)
_ANYPCT = re.compile(r"(\d{1,2})\s*%\s*(?:off|할인)", re.I)
_CODE = re.compile(r"\b(?:code|promo\s*code|coupon)\s*:?\s*([A-Z0-9]{4,15})\b")
_PRICE_PAIR = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)\s*(?:->|→|from)\s*\$\s?([\d,]+(?:\.\d{2})?)", re.I)


def _decode(s) -> str:
    """
    메일 헤더 디코딩.
    보통은 MIME 인코딩(=?UTF-8?B?...?=)이라 make_header 로 풀리지만,
    일부 발신자는 헤더에 UTF-8 바이트를 그대로 박아 넣는다.
    그 경우 파이썬이 latin-1 로 읽어 한글이 깨지고, 깨진 제목으로는
    '환영' 같은 신호를 못 읽어 코드 분류가 틀어진다.
    """
    if not s:
        return ""
    try:
        out = str(make_header(decode_header(s)))
    except Exception:
        out = str(s)
    # 깨진 UTF-8(latin-1 로 잘못 읽힌 것)을 복원 시도
    if re.search(r"[À-ÿ]{3,}", out):
        try:
            fixed = out.encode("latin-1", "strict").decode("utf-8", "strict")
            if fixed.strip():
                return fixed
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return out


def _body_text(msg) -> str:
    """메일 본문을 평문으로."""
    parts = []
    if msg.is_multipart():
        for p in msg.walk():
            ctype = p.get_content_type()
            if ctype in ("text/plain", "text/html"):
                try:
                    raw = p.get_payload(decode=True) or b""
                    parts.append(raw.decode(p.get_content_charset() or "utf-8", "replace"))
                except Exception:
                    continue
    else:
        try:
            raw = msg.get_payload(decode=True) or b""
            parts.append(raw.decode(msg.get_content_charset() or "utf-8", "replace"))
        except Exception:
            pass
    text = "\n".join(parts)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", htmlmod.unescape(text))


def _sender_domain(msg) -> str | None:
    frm = _decode(msg.get("From", ""))
    m = re.search(r"@([\w.\-]+)", frm)
    if not m:
        return None
    host = m.group(1).lower()
    if host in SENDER_MAP:
        return SENDER_MAP[host]
    # 서브도메인 대응 (email.macys.com 등)
    for k, v in SENDER_MAP.items():
        if host.endswith("." + k) or host == k:
            return v
    return None


def _first_link(msg_text_html: str, domain: str) -> str:
    m = re.search(rf'https?://[^\s"\'<>]*{re.escape(domain)}[^\s"\'<>]*', msg_text_html)
    return m.group(0)[:300] if m else f"https://www.{domain}"


def parse_message(msg) -> Deal | None:
    domain = _sender_domain(msg)
    if not domain:
        return None

    subject = _decode(msg.get("Subject", ""))
    body = _body_text(msg)
    blob = f"{subject} {body[:3000]}"

    # 할인율 계산.
    #
    # 메일은 보통 "Extra 30% off Sale & Clearance / Up to 70% off" 형태다.
    # 여기서 30%만 보면 실제 할인폭을 크게 과소평가한다.
    # 이미 70% 내려간 상품에 추가 30%면 실효 할인은 79%다.
    #
    #   실효할인 = 1 - (1 - 기본할인) x (1 - 추가할인)
    #
    # 다만 "up to"는 마케팅 문구라 전 품목에 적용되지 않으므로,
    # 스택 계산 결과에 상한(85%)을 둬서 과대평가를 막는다.
    m = _EXTRA.search(blob)
    extra = int(m.group(1)) if m else None
    m2 = _UPTO.search(blob)
    upto = int(m2.group(1)) if m2 else None
    if extra is None and upto is None:
        m3 = _ANYPCT.search(blob)
        upto = int(m3.group(1)) if m3 else None

    # 결제 프로모션 코드 추출 (핵심)
    # 리테일러는 코드를 구독자 메일로 먼저 뿌린다. 그 시점이 슬릭딜보다 앞선다.
    import promocode
    codes = promocode.extract(blob, subject=subject)
    pc = promocode.best(codes)

    # 기본 세일폭과 추가 할인을 한 번만 합성한다.
    #
    # 주의: 코드 할인(pc.percent)과 본문의 'extra N%'는 대개 같은 것을 가리킨다.
    #       둘을 각각 적용하면 이중 계산이 된다(실제로 96%까지 부풀려짐).
    #       그래서 '추가 할인'은 하나의 값으로만 취급한다.
    base = upto                      # "up to 70% off" — 기본 세일폭
    add = extra                      # "extra 30% off" — 추가 할인
    # 신규가입·개인 전용 코드는 가격 계산에 넣지 않는다.
    # 내 계정에서만 쓸 수 있는 코드로 계산한 가격을 발행하면
    # 구독자는 그 가격에 살 수 없다.
    if pc and pc.percent and pc.shareable:
        add = pc.percent if add is None else max(add, pc.percent)

    stacked = False
    if base and add and base != add:
        # 이미 base% 내려간 가격에 add%가 더 붙는 구조
        pct = round((1 - (1 - base / 100) * (1 - add / 100)) * 100)
        pct = min(pct, 85)           # "up to"는 마케팅 문구라 상한을 둔다
        stacked = True
    else:
        pct = add or base

    if not pct or not (10 <= pct <= 95):
        return None        # 할인 정보가 없으면 딜로 보지 않는다

    # 가격 쌍이 있으면 사용
    cur = lst = None
    pm = _PRICE_PAIR.search(blob)
    if pm:
        a, b = float(pm.group(1).replace(",", "")), float(pm.group(2).replace(",", ""))
        lst, cur = max(a, b), min(a, b)
    else:
        # 세일 공지형: 가상 가격으로 할인율만 표현 (점수 계산용)
        lst, cur = 100.0, round(100 * (1 - pct / 100), 2)

    # 정액 할인 코드($25 off 등)는 위 퍼센트 합성에 안 잡히므로 여기서 반영
    if pc and pc.shareable and not pc.percent and pc.amount:
        cur = promocode.apply_to_price(cur, pc)

    date_hdr = msg.get("Date", "")

    title = subject[:120] or f"{domain} 세일"
    if pc and pc.shareable:
        title = f"[코드 {pc.code}] {title}"
    elif pc:
        # 공유 불가 코드는 있다는 사실만 알리고 가격에는 반영하지 않는다
        label = "신규가입" if pc.kind == "welcome" else ("개인전용" if pc.kind == "personal" else "범위미확인")
        title = f"[{label} 코드] {title}"
    elif stacked:
        # 개별 상품가가 아니라 '세일 공지'임을 제목에 밝혀둔다.
        title = f"[세일공지] {title}"

    d = Deal(
        source=domain,
        source_tier="T2",
        url=_first_link(body, domain),
        title=title,
        brand=domain.split(".")[0].title(),
        price_current=cur,
        price_list=lst,
        price_baseline=lst,
        coupon_code=pc.describe() if pc else "",
        coupon_stackable=bool(pc and pc.shareable),
        collection_method="email",
        code_kind=pc.kind if pc else "",
    )
    return d


def _save_codes(deal: Deal, msg) -> None:
    """메일에서 뽑은 코드를 리테일러별로 DB에 저장."""
    try:
        import promocode, store
        subj = _decode(msg.get("Subject", ""))
        codes = promocode.extract(f"{subj} {_body_text(msg)[:3000]}", subject=subj)
        if not codes:
            return
        con = store.connect()
        for pc in codes:
            if pc.percent or pc.amount:
                store.save_promo(con, deal.source, pc)
        con.close()
    except Exception as e:
        print(f"[email] 코드 저장 실패: {type(e).__name__}")


def fetch_email_deals(days: int = 2, limit: int = 60) -> list[Deal]:
    user = os.environ.get("RADAR_IMAP_USER")
    pw = os.environ.get("RADAR_IMAP_PASS")
    if not (user and pw):
        print("[email] RADAR_IMAP_USER/PASS 미설정 → 건너뜀 (9_이메일수집.bat 참고)")
        return []

    try:
        M = imaplib.IMAP4_SSL(IMAP_HOST)
        M.login(user, pw)
    except Exception as e:
        print(f"[email] 로그인 실패: {type(e).__name__}: {str(e)[:120]}")
        print("        Gmail은 '앱 비밀번호'가 필요합니다 (일반 비밀번호 아님)")
        return []

    deals: list[Deal] = []
    try:
        M.select("INBOX")
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
        typ, data = M.search(None, f'(SINCE {since})')
        ids = data[0].split()[-limit:] if data and data[0] else []
        print(f"[email] 최근 {days}일 메일 {len(ids)}통 검사")

        for i in ids:
            typ, msg_data = M.fetch(i, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            d = parse_message(msg)
            if d:
                deals.append(d)
                # 확보한 코드를 저장해 둔다.
                # 이 코드는 나중에 같은 리테일러의 '크롤링 상품'에 적용되어
                # 사이트 단위 공지가 상품 단위 실구매가로 바뀐다.
                _save_codes(d, msg)
    except Exception as e:
        print(f"[email] 읽기 오류: {type(e).__name__}: {str(e)[:120]}")
    finally:
        try:
            M.close(); M.logout()
        except Exception:
            pass

    by_src: dict[str, int] = {}
    for d in deals:
        by_src[d.source] = by_src.get(d.source, 0) + 1
    for s, n in sorted(by_src.items(), key=lambda x: -x[1]):
        print(f"  [email] {s:24s} {n}건")
    print(f"[email] 총 {len(deals)}건 추출")
    return deals


if __name__ == "__main__":
    import filter_engine as fe, store
    ds = fetch_email_deals()
    if not ds:
        print("\n추출된 딜이 없습니다.")
        print("구독 메일이 아직 없거나, 온 메일에 할인 정보가 없는 경우입니다.")
    else:
        print(f"\n{'='*66}")
        print("  추출 결과 (코드 분류 포함)")
        print(f"{'='*66}")
        for d in fe.process(ds):
            pub = "발행" if fe.should_publish(d) else "미발행"
            print(f"\n[{pub}] {d.source} · {d.discount_pct:.0f}% · {d.score}점 {d.urgency or ''}")
            print(f"   {d.title[:60]}")
            if d.coupon_code:
                print(f"   코드: {d.coupon_code}")
            else:
                print(f"   코드: 없음")

        con = store.connect()
        print(f"\n{'='*66}")
        print(" ", store.promo_summary(con))
        print(f"{'='*66}")
        print("\n[안내] 신규가입·개인전용 코드는 가격 계산과 상품 적용에서 제외됩니다.")
        print("       내 계정에서만 쓸 수 있는 코드로 계산한 가격을 발행하면")
        print("       구독자가 그 가격에 살 수 없기 때문입니다.")
