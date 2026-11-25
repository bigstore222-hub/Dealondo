"""
kr_names.py — 영어 상품명을 한국 사람이 피드에서 바로 알아보는 이름으로 바꾼다.

  "Theory" + "Carissa Verve Stretch Virgin Wool Blazer"  →  "띠어리 울 블레이저"

스레드 첫 줄은 '무엇인지'가 한눈에 보여야 멈춰서 읽는다. 영어 모델명(Carissa Verve…)은
한국 독자에게 정보가 아니므로 브랜드 한글명 + 소재 + 품목만 남긴다.
사전에 없는 브랜드는 영어 그대로, 품목을 못 찾으면 영어 상품명을 짧게 쓴다.
새 브랜드·품목은 아래 표에 한 줄씩 추가하면 된다.
"""
from __future__ import annotations
import re

BRAND_KR = {
    "gucci": "구찌", "prada": "프라다", "balenciaga": "발렌시아가", "burberry": "버버리",
    "saint laurent": "생로랑", "bottega veneta": "보테가 베네타", "moncler": "몽클레르", "loewe": "로에베",
    "celine": "셀린느", "givenchy": "지방시", "valentino": "발렌티노", "ferragamo": "페라가모",
    "salvatore ferragamo": "페라가모", "maison margiela": "메종 마르지엘라", "acne studios": "아크네 스튜디오",
    "thom browne": "톰 브라운", "stone island": "스톤아일랜드", "canada goose": "캐나다구스",
    "montblanc": "몽블랑", "tumi": "투미", "rimowa": "리모와", "polo ralph lauren": "폴로 랄프로렌",
    "ralph lauren": "랄프로렌", "lauren ralph lauren": "로렌 랄프로렌", "theory": "띠어리",
    "allsaints": "올세인츠", "all saints": "올세인츠", "vince": "빈스", "reiss": "리스", "ganni": "가니",
    "a.p.c.": "아페쎄", "apc": "아페쎄", "coach": "코치", "kate spade": "케이트 스페이드",
    "kate spade new york": "케이트 스페이드", "tory burch": "토리버치", "michael kors": "마이클 코어스",
    "michael michael kors": "마이클 코어스", "marc jacobs": "마크 제이콥스", "longchamp": "롱샴",
    "stuart weitzman": "스튜어트 와이츠먼", "cole haan": "콜한", "sandro": "산드로", "maje": "마쥬",
    "veja": "베자", "arc'teryx": "아크테릭스", "arcteryx": "아크테릭스", "patagonia": "파타고니아",
    "the north face": "노스페이스", "north face": "노스페이스", "hoka": "호카", "hoka one one": "호카",
    "on running": "온러닝", "on": "온러닝", "salomon": "살로몬", "dr. martens": "닥터마틴",
    "dr martens": "닥터마틴", "birkenstock": "버켄스탁", "ugg": "어그", "herschel": "허쉘",
    "herschel supply co.": "허쉘", "nike": "나이키", "adidas": "아디다스", "new balance": "뉴발란스",
    "asics": "아식스", "brooks": "브룩스", "saucony": "써코니", "puma": "푸마", "reebok": "리복",
    "under armour": "언더아머", "columbia": "컬럼비아", "merrell": "머렐", "keen": "킨",
    "timberland": "팀버랜드", "crocs": "크록스", "vans": "반스", "converse": "컨버스", "clarks": "클락스",
    "ecco": "에코", "sperry": "스페리", "marmot": "마모트", "fjallraven": "피엘라벤",
    "fjällräven": "피엘라벤", "carhartt": "칼하트", "carhartt wip": "칼하트 WIP", "levi's": "리바이스",
    "levis": "리바이스", "champion": "챔피온", "fila": "휠라", "skechers": "스케쳐스", "vionic": "바이오닉",
    "tommy hilfiger": "타미힐피거", "calvin klein": "캘빈클라인", "lacoste": "라코스테", "diesel": "디젤",
    "guess": "게스", "ted baker": "테드베이커", "paul smith": "폴스미스", "free people": "프리피플",
    "vince camuto": "빈스 카무토", "steve madden": "스티브 매든", "sam edelman": "샘 에델만",
    "samsonite": "샘소나이트", "jansport": "잔스포츠", "osprey": "오스프리", "eastpak": "이스트팩",
    "seiko": "세이코", "citizen": "시티즌", "casio": "카시오", "g-shock": "지샥", "tissot": "티쏘",
    "hamilton": "해밀턴", "movado": "모바도", "fossil": "파슬", "bulova": "부로바", "orient": "오리엔트",
    "swarovski": "스와로브스키", "pandora": "판도라", "lululemon": "룰루레몬", "nautica": "노티카",
    "jimmy choo": "지미추", "ray-ban": "레이밴", "oakley": "오클리", "kenneth cole": "케네스콜",
    "vera bradley": "베라브래들리", "dkny": "DKNY", "anne klein": "앤클라인", "reef": "리프",
    "maison kitsune": "메종 키츠네", "isabel marant": "이자벨 마랑", "max mara": "막스마라",
    "palm angels": "팜엔젤스", "tom ford": "톰포드", "versace": "베르사체", "mango": "망고",
    "barbour": "바버", "hunter": "헌터", "camper": "캠퍼", "toms": "탐스", "rockport": "락포트",
    "gobi cashmere": "고비 캐시미어", "villeroy & boch": "빌레로이앤보흐", "lenox": "레녹스",
    "allbirds": "올버즈", "apple": "애플", "samsung": "삼성", "sony": "소니", "bose": "보스",
    "beats": "비츠", "anker": "앤커", "jbl": "JBL", "dyson": "다이슨", "garmin": "가민",
    "le creuset": "르크루제", "staub": "스타우브", "zwilling": "즈윌링", "yeti": "예티",
    "stanley": "스탠리", "hydro flask": "하이드로플라스크", "kith": "키스", "stussy": "스투시",
    "j.crew": "제이크루", "j crew": "제이크루", "banana republic": "바나나리퍼블릭", "gap": "갭",
    "uniqlo": "유니클로", "frye": "프라이", "sorel": "소렐", "hugo boss": "휴고보스", "boss": "보스",
    "michael stars": "마이클 스타스", "rag & bone": "랙앤본", "frame": "프레임", "agolde": "아골디",
    "madewell": "메이드웰", "everlane": "에버레인", "mackage": "맥케이지", "woolrich": "울리치",
}

# 품목 — 긴 표현이 먼저 맞도록 길이순으로 찾는다. 영어 상품명은 보통 품목 명사가 맨 끝에 온다.
ITEM_KR = {
    "blazer": "블레이저", "sport coat": "블레이저", "suit jacket": "수트 재킷", "suit set": "수트 세트",
    "suit": "수트", "puffer jacket": "패딩", "puffer coat": "롱패딩", "puffer": "패딩",
    "down jacket": "다운 재킷", "down coat": "다운 코트", "parka": "파카", "trench coat": "트렌치코트",
    "trench": "트렌치코트", "peacoat": "피코트", "overcoat": "오버코트", "coat": "코트",
    "jacket": "재킷", "shacket": "셔츠 재킷", "vest": "베스트", "gilet": "베스트",
    "cardigan": "가디건", "sweater": "스웨터", "pullover": "풀오버", "turtleneck": "터틀넥",
    "hoodie": "후드티", "sweatshirt": "맨투맨", "crewneck": "맨투맨", "sweatpants": "스웨트팬츠",
    "joggers": "조거팬츠", "jogger": "조거팬츠", "leggings": "레깅스", "jeans": "청바지",
    "jean": "청바지", "chinos": "치노팬츠", "trousers": "슬랙스", "pants": "바지", "pant": "바지",
    "shorts": "반바지", "skirt": "스커트", "miniskirt": "미니스커트", "mini skirt": "미니스커트", "midi skirt": "미디스커트", "dress": "원피스", "jumpsuit": "점프수트",
    "polo shirt": "폴로셔츠", "polo": "폴로셔츠", "t-shirt": "티셔츠", "tee": "티셔츠",
    "button-down shirt": "셔츠", "shirt": "셔츠", "blouse": "블라우스", "top": "상의",
    "tank": "나시", "bodysuit": "바디수트", "pajama set": "잠옷 세트", "robe": "로브",
    "running shoe": "러닝화", "running shoes": "러닝화", "trail running shoe": "트레일 러닝화",
    "sneaker": "스니커즈", "sneakers": "스니커즈", "trainer": "스니커즈", "trainers": "스니커즈",
    "hiking boot": "등산화", "hiking shoe": "등산화", "chelsea boot": "첼시부츠",
    "knee high boot": "롱부츠", "bootie": "앵클부츠", "booties": "앵클부츠", "boot": "부츠",
    "boots": "부츠", "loafer": "로퍼", "loafers": "로퍼", "oxford": "옥스퍼드화", "derby": "더비슈즈",
    "pump": "펌프스", "pumps": "펌프스", "mary jane": "메리제인 슈즈", "ballet flat": "발레 플랫",
    "flat": "플랫슈즈", "flats": "플랫슈즈", "mule": "뮬", "mules": "뮬", "clog": "클로그",
    "clogs": "클로그", "sandal": "샌들", "sandals": "샌들", "slide": "슬리퍼", "slides": "슬리퍼",
    "slipper": "슬리퍼", "slippers": "슬리퍼", "heel": "힐", "wedge": "웨지힐",
    "crossbody bag": "크로스백", "crossbody": "크로스백", "shoulder bag": "숄더백", "tote bag": "토트백",
    "tote": "토트백", "hobo bag": "호보백", "hobo": "호보백", "satchel": "사첼백", "clutch": "클러치",
    "belt bag": "벨트백", "backpack": "백팩", "duffel": "더플백", "weekender": "여행 가방",
    "carry-on": "기내용 캐리어", "luggage": "캐리어", "suitcase": "캐리어", "bag": "가방",
    "wallet": "지갑", "card case": "카드지갑", "cardholder": "카드지갑", "belt": "벨트",
    "scarf": "머플러", "beanie": "비니", "cap": "모자", "hat": "모자", "gloves": "장갑",
    "sunglasses": "선글라스", "watch": "시계", "necklace": "목걸이", "bracelet": "팔찌",
    "earrings": "귀걸이", "ring": "반지", "earbuds": "무선 이어폰", "headphones": "헤드폰",
    "speaker": "스피커", "smartwatch": "스마트워치", "tablet": "태블릿", "laptop": "노트북",
    "monitor": "모니터", "ssd": "SSD", "keyboard": "키보드", "mouse": "마우스",
    "mock neck": "모크넥 상의", "crew": "크루넥 상의", "henley": "헨리넥 티", "maxi": "맥시 원피스",
    "maxi dress": "맥시 원피스", "shoes": "운동화", "shoe": "신발", "cleats": "축구화", "runner": "스니커즈",
    "runners": "스니커즈", "daypack": "백팩", "xbody": "크로스백", "rain jacket": "레인 재킷",
    "windbreaker": "바람막이", "fleece jacket": "플리스 재킷", "quarter zip": "반집업", "1/4 zip": "반집업",
    "half zip": "반집업", "track jacket": "트랙 재킷", "anorak": "아노락",
    "socks": "양말", "sock": "양말", "crew socks": "양말", "no show socks": "양말",
    "boxer briefs": "속옷", "boxer brief": "속옷", "briefs": "속옷", "boxers": "속옷", "undershirt": "속옷 상의",
}
MATERIAL_KR = {"cashmere": "캐시미어", "merino": "메리노 울", "wool": "울", "leather": "가죽",
               "suede": "스웨이드", "shearling": "시어링", "linen": "린넨", "silk": "실크",
               "down": "다운", "fleece": "플리스", "denim": "데님", "corduroy": "코듀로이"}
# 품목 자체에 소재가 들어간 경우 소재를 중복으로 붙이지 않는다
_NO_MATERIAL = {"청바지", "다운 재킷", "다운 코트"}

_ITEMS = sorted(ITEM_KR, key=len, reverse=True)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().replace("’", "'")).strip()


def brand_kr(brand: str) -> str:
    b = _norm(brand)
    hit = BRAND_KR.get(b) or BRAND_KR.get(b.replace("-", " "))
    if hit:
        return hit
    # "Calvin Klein Two", "Kenneth Cole Reaction" 처럼 하위 라인 이름이 붙은 경우 앞부분으로 찾는다
    for k in sorted(BRAND_KR, key=len, reverse=True):
        if len(k) > 3 and b.startswith(k + " "):
            return BRAND_KR[k]
    return (brand or "").strip()


def item_kr(title: str) -> str:
    t = _norm(title)
    best = None   # (끝 위치, 길이, 품목)
    for term in _ITEMS:
        for m in re.finditer(rf"(?<![a-z]){re.escape(term)}(?![a-z])", t):
            key = (m.end(), len(term))
            if best is None or key > best[:2]:
                best = (m.end(), len(term), ITEM_KR[term])
    if not best:
        return ""
    item = best[2]
    if item in _NO_MATERIAL:
        return item
    head = t[: best[0] - best[1]]
    for mat, kr in MATERIAL_KR.items():
        if re.search(rf"(?<![a-z]){mat}(?![a-z])", head):
            return f"{kr} {item}"
    return item


def gender(*texts: str) -> str:
    t = " ".join(_norm(x) for x in texts if x)
    if re.search(r"(?<![a-z])(women|womens|women's|woman|ladies)(?![a-z])", t):
        return "여성용"
    if re.search(r"(?<![a-z])(men|mens|men's|man)(?![a-z])", t):
        return "남성용"
    if re.search(r"(?<![a-z])(kids?|kids'|big kid|little kid|toddler|infant|baby|boys|girls)(?![a-z])", t):
        return "아동용"
    return ""


def kind(title: str) -> str:
    """질문 문구를 고르기 위한 큰 분류: clothes / shoes / bags / electronics / etc"""
    it = item_kr(title)
    if not it:
        return "etc"
    shoes = {"운동화", "신발", "축구화", "러닝화", "트레일 러닝화", "스니커즈", "등산화", "첼시부츠", "롱부츠", "앵클부츠", "부츠", "로퍼",
             "옥스퍼드화", "더비슈즈", "펌프스", "메리제인 슈즈", "발레 플랫", "플랫슈즈", "뮬", "클로그", "샌들",
             "슬리퍼", "힐", "웨지힐"}
    bags = {"크로스백", "숄더백", "토트백", "호보백", "사첼백", "클러치", "벨트백", "백팩", "더플백", "여행 가방",
            "기내용 캐리어", "캐리어", "가방", "지갑", "카드지갑", "벨트", "머플러", "비니", "모자", "장갑",
            "선글라스", "시계", "목걸이", "팔찌", "귀걸이", "반지"}
    elec = {"무선 이어폰", "헤드폰", "스피커", "스마트워치", "태블릿", "노트북", "모니터", "SSD", "키보드", "마우스"}
    base = it.split(" ")[-1] if it not in shoes | bags | elec else it
    if it in shoes or base in shoes:
        return "shoes"
    if it in bags or base in bags:
        return "bags"
    if it in elec:
        return "electronics"
    return "clothes"


def headline_name(brand: str, title: str) -> str:
    """첫 줄용 이름: '띠어리 울 블레이저'. 품목을 못 찾으면 브랜드 + 영어 상품명(짧게)."""
    b = brand_kr(brand)
    if brand and (title or "").lower().startswith(brand.lower()):
        title = title[len(brand):].lstrip(" -·,")
    it = item_kr(title)
    if it:
        return f"{b} {it}".strip()
    words = (title or "").split()
    short = " ".join(words[:5]) + ("…" if len(words) > 5 else "")
    return f"{b} {short}".strip()


# ---------------------------------------------------------------------------
# 딜보드용 한글 제목 (2026-11-25)
#   스레드용 ITEM_KR(패션 중심)은 kind() 판정에도 쓰이므로 건드리지 않고,
#   전자·홈·생활·자동차 품목은 아래 GENERAL_KR 에만 둔다. 딜보드 카드 제목 전용.
# ---------------------------------------------------------------------------
GENERAL_KR = {
    # 전자·IT
    "earbuds": "무선 이어폰", "earphones": "이어폰", "headphones": "헤드폰", "headset": "헤드셋",
    "soundbar": "사운드바", "bluetooth speaker": "블루투스 스피커", "speaker": "스피커",
    "smartwatch": "스마트워치", "smart watch": "스마트워치", "fitness tracker": "피트니스 밴드",
    "tablet": "태블릿", "ipad": "아이패드", "kindle": "킨들 전자책", "e-reader": "전자책 리더기",
    "laptop": "노트북", "chromebook": "크롬북", "macbook": "맥북", "desktop": "데스크톱 PC",
    "gaming monitor": "게이밍 모니터", "monitor": "모니터", "4k tv": "4K TV", "smart tv": "스마트 TV",
    "oled tv": "OLED TV", "tv": "TV", "projector": "프로젝터", "streaming stick": "스트리밍 스틱",
    "ssd": "SSD", "hard drive": "외장하드", "microsd": "마이크로SD 카드", "flash drive": "USB 메모리",
    "memory card": "메모리 카드", "router": "공유기", "mesh wi-fi": "메시 와이파이", "keyboard": "키보드",
    "mouse": "마우스", "webcam": "웹캠", "microphone": "마이크", "docking station": "도킹 스테이션",
    "usb-c hub": "USB-C 허브", "hub": "허브", "charger": "충전기", "wall charger": "충전기",
    "wireless charger": "무선 충전기", "charging station": "충전 스테이션", "power bank": "보조배터리",
    "portable charger": "보조배터리", "battery pack": "보조배터리", "power station": "파워뱅크(캠핑용)",
    "cable": "케이블", "camera": "카메라", "lens": "카메라 렌즈", "dash cam": "블랙박스",
    "security camera": "보안 카메라", "video doorbell": "스마트 초인종", "smart plug": "스마트 플러그",
    "smart outlet": "스마트 플러그", "wi-fi outlet": "스마트 플러그", "smart bulb": "스마트 전구",
    "controller": "게임 컨트롤러", "console": "게임기", "nintendo switch": "닌텐도 스위치",
    "gift card": "기프트카드", "printer": "프린터", "graphics card": "그래픽카드", "gpu": "그래픽카드",
    "phone case": "휴대폰 케이스", "screen protector": "액정 보호필름",
    # 홈·주방
    "air fryer": "에어프라이어", "pressure cooker": "전기압력솥", "multi-cooker": "멀티쿠커",
"rice cooker": "밥솥", "slow cooker": "슬로우쿠커",
    "countertop oven": "오븐", "toaster oven": "토스터 오븐", "oven": "오븐", "microwave": "전자레인지",
    "blender": "블렌더", "mixer": "믹서", "food processor": "푸드 프로세서", "coffee maker": "커피메이커",
    "espresso machine": "에스프레소 머신", "kettle": "전기포트", "toaster": "토스터",
    "cookware set": "냄비 세트", "cookware": "조리도구", "dutch oven": "무쇠 냄비", "skillet": "프라이팬",
    "frying pan": "프라이팬", "knife set": "칼 세트", "knife": "칼", "cutting board": "도마",
    "food storage": "밀폐용기", "tumbler": "텀블러", "water bottle": "물병", "travel bottle": "보온병",
    "bottle": "보온병", "mug": "머그", "lunch box": "도시락통", "cooler": "아이스박스",
    "robot vacuum": "로봇청소기", "stick vacuum": "무선 청소기", "vacuum": "청소기",
    "air purifier": "공기청정기", "humidifier": "가습기", "dehumidifier": "제습기",
    "space heater": "전기히터", "heater": "히터", "fan": "선풍기", "mattress": "매트리스",
    "pillow": "베개", "comforter": "이불", "duvet": "이불", "sheet set": "침대 시트",
    "towel": "수건", "lamp": "조명", "desk": "책상", "chair": "의자", "storage": "수납함",
    "electric toothbrush": "전동칫솔", "water flosser": "구강세정기", "hair dryer": "헤어드라이어",
    "shaver": "면도기", "trimmer": "트리머", "massager": "마사지기", "massage gun": "마사지건",
    "scale": "체중계", "heating pad": "온열 패드", "charging pad": "무선 충전기",
    "energy drink": "에너지 음료", "energy drinks": "에너지 음료", "sports drink": "스포츠 음료",
    "juice": "주스", "coffee": "커피", "protein powder": "단백질 보충제", "pens": "펜", "gel pens": "젤펜",
    "markers": "마커", "smart ring": "스마트링",
    # 공구·자동차 (배대지·통관 주의 품목이 많다)
    "jump starter": "점프스타터", "tire inflator": "타이어 공기주입기", "air compressor": "에어컴프레서",
    "drill": "드릴", "impact driver": "임팩트 드라이버", "tool set": "공구 세트", "flashlight": "손전등",
    # 장난감·유아
    "lego": "레고", "playset": "놀이 세트", "play set": "놀이 세트", "building set": "블록 세트",
    "magnetic tiles": "자석 블록", "doll": "인형", "plush": "봉제인형", "puzzle": "퍼즐",
    "board game": "보드게임", "toy": "장난감", "stroller": "유모차", "car seat": "카시트",
    "diapers": "기저귀", "high chair": "아기 의자",
    # 패션 보강 (ITEM_KR 에 없는 것)
    "bra": "브라", "lace-up": "레이스업 슈즈", "boat shoes": "보트슈즈", "boat shoe": "보트슈즈",
    "lug": "러그솔 슈즈", "jersey": "저지", "swimsuit": "수영복", "pajamas": "잠옷",
    "perfume": "향수", "fragrance": "향수", "cologne": "향수", "eau de parfum": "향수",
}
_GENERAL = sorted(GENERAL_KR, key=len, reverse=True)

# 제목에 붙는 가격·배송·회원 조건 문구 — 한글 제목에선 뺀다(가격은 카드에 따로 나온다)
_PRICE_NOISE = re.compile(
    r"(\s*(?:for|at|@)?\s*\$\s?[\d,.]+(?:\s*(?:ea|each))?|\+\s*free\s+s&h|\+\s*free\s+shipping|"
    r"free\s+shipping|\bw/\s*prime\b|\bvia\b.*$|\(\s*various[^)]*\)?|\bprime\s+members?:|"
    r"\b[A-Z]{2,}/[A-Z]{2,}:|\bselect\s+(?:amazon\s+)?(?:accounts|accts|members)[^:]*:|^\s*\|\s*|^\s*(?:ymmv|today only|lightning deal)\s*:?)", re.I)


def clean_title(title: str) -> str:
    t = _PRICE_NOISE.sub(" ", title or "")
    t = re.sub(r"\s*[-–—:|]\s*$", "", t)
    return re.sub(r"\s+", " ", t).strip(" -–—,:;+|")


def _last_match(t: str, terms: list, table: dict):
    best = None   # (끝 위치, 길이, 한글)
    for term in terms:
        for m in re.finditer(rf"(?<![a-z]){re.escape(term)}(?![a-z])", t):
            key = (m.end(), len(term))
            if best is None or key > best[:2]:
                best = (m.end(), len(term), table[term])
    return best


def general_item(title: str) -> str:
    b = _last_match(_norm(title), _GENERAL, GENERAL_KR)
    return b[2] if b else ""


# 본품 뒤에 붙는 구성품·옵션 설명('+ Desk Mount', 'w/ Sport Band', 'with Spinner Wheels')은
# 품목 판정에서 뺀다 — 영어 상품명의 본품 명사는 이 앞부분의 맨 끝에 온다.
_ACCESSORY_TAIL = re.compile(r"\s(?:\+|w/|with|&\s*more|incl\.?|including|bundle)\s.*$", re.I)


_WEAK_FASHION = {"벨트", "모자", "상의", "플랫슈즈", "크루넥 상의", "나시", "반지", "슬리퍼", "스니커즈", "가방"}


def board_title(brand: str, title: str) -> str:
    """딜보드 카드 제목: '스튜어트 와이츠먼 레이스업 슈즈', '스탠리 텀블러'.
    품목을 못 찾으면 브랜드 한글명 + 짧은 영어 상품명(가격 문구 제거)."""
    t = clean_title(title)
    if brand and t.lower().startswith(brand.lower()):
        t = t[len(brand):].lstrip(" -·,")
    head = _ACCESSORY_TAIL.sub("", t) or t
    b = brand_kr(brand) if brand else ""
    n = _norm(head)
    fa = _last_match(n, _ITEMS, ITEM_KR)
    ge = _last_match(n, _GENERAL, GENERAL_KR)
    # 두 사전 중 '더 뒤에서 끝나는'(=본품 명사) 쪽을 쓴다. 'Laptop Backpack' → 백팩
    pick = max((x for x in (fa, ge) if x), key=lambda x: x[:2], default=None)
    # 패션 사전의 짧은 일반 단어(belt·cap·top…)가 생활용품 제목 끝에 붙은 경우는 생활 품목을 쓴다
    if pick is fa and ge and fa[2] in _WEAK_FASHION:
        pick = ge
    if pick:
        g = gender(title) if pick is fa else ""
        return " ".join(x for x in (b, g, pick[2]) if x).strip()
    words = t.split()
    short = " ".join(words[:6]) + ("…" if len(words) > 6 else "")
    return f"{b} {short}".strip() if b else short
