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
    "shorts": "반바지", "skirt": "스커트", "dress": "원피스", "jumpsuit": "점프수트",
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
