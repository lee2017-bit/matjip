#!/usr/bin/env python3
"""Build the static site: fetch restaurant rankings per area, then render HTML pages.

Usage:
  python3 build.py                 # use cached data when it is younger than 6 days
  python3 build.py --refresh       # fetch everything again
  BASE_URL=https://example.github.io/eatpl python3 build.py   # absolute URLs for sitemap / share previews
  LOOKUP_API=https://matjip-lookup.example.workers.dev python3 build.py   # enable live lookups (worker/)

Data: eat.pl 잇플 search (BC카드 결제 데이터 기반) via the public k-skill proxy.
Python standard library only.
"""
import html
import json
import math
import os
import pathlib
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).parent
CACHE = ROOT / "data" / "cache"
PICKS = ROOT / "data" / "picks.json"
OUT = ROOT / "site"
SRC = ROOT / "src"
PROXY = os.environ.get("EATPL_PROXY", "https://k-skill-proxy.nomadamas.org/v1/bccard-eatpl/search")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
LOOKUP_API = os.environ.get("LOOKUP_API", "").strip().rstrip("/")
SITE_NAME = "매출맛집"
TAGLINE = "별점 말고, 실제로 많이 팔리는 집"
KST = timezone(timedelta(hours=9))
MAX_AGE = timedelta(days=6)

# category groups (eat.pl 업종명 → 사이트 분류). Order = order of the filter chips.
GROUPS = [
    ("korean", "한식", ("찜/탕/찌개", "백반/한정식", "기타한식", "국수/면/만두", "전/부침개", "도시락/죽", "뷔페", "반찬가게", "비건/채식/사찰")),
    ("meat", "고기", ("고기구이", "곱창전골/구이", "족발/보쌈", "양꼬치")),
    ("seafood", "해산물", ("횟집", "해산물구이/찜")),
    ("japanese", "일식", ("기타일식", "스시/초밥", "일식돈가스", "일식라멘")),
    ("chinese", "중식", ("기타중식", "마라탕")),
    ("western", "양식·아시안", ("기타외국식", "이탈리아음식", "아시안", "멕시칸", "프랑스음식", "브런치카페/샐러드")),
    ("quick", "분식·간편식", ("분식", "김밥전문점", "떡볶이전문점", "치킨", "버거", "피자", "샌드위치/토스트", "푸드코트", "휴게소")),
    ("drink", "술집", ("호프", "기타주점", "이자카야", "바(bar)")),
    ("cafe", "카페·디저트", ("카페", "기타베이커리", "빵/도넛", "아이스크림/요거트/빙수", "떡/한과", "체험카페")),
]
GROUP_OF = {cat: key for key, _, cats in GROUPS for cat in cats}
# 업종 아이콘 (사진 대신). 세부 업종 → 없으면 분류 아이콘. app.js 의 ICONS 와 같은 값.
CAT_ICON = {"찜/탕/찌개": "🍲", "국수/면/만두": "🍜", "일식라멘": "🍜", "마라탕": "🌶️", "치킨": "🍗", "피자": "🍕", "버거": "🍔",
            "분식": "🍢", "떡볶이전문점": "🍢", "김밥전문점": "🍙", "도시락/죽": "🍱", "샌드위치/토스트": "🥪", "브런치카페/샐러드": "🥗",
            "족발/보쌈": "🍖", "곱창전골/구이": "🍖", "양꼬치": "🍢", "스시/초밥": "🍣", "일식돈가스": "🍛", "이탈리아음식": "🍝",
            "멕시칸": "🌮", "아시안": "🍜", "프랑스음식": "🍷", "바(bar)": "🍸", "이자카야": "🏮", "호프": "🍺", "기타주점": "🍶",
            "빵/도넛": "🥐", "기타베이커리": "🥐", "아이스크림/요거트/빙수": "🍨", "떡/한과": "🍡", "뷔페": "🍽️", "해산물구이/찜": "🦀", "비건/채식/사찰": "🥗"}
GROUP_ICON = {"korean": "🍚", "meat": "🥩", "seafood": "🐟", "japanese": "🍣", "chinese": "🥟", "western": "🍝",
              "quick": "🍔", "drink": "🍺", "cafe": "☕", "etc": "🍽️"}


def icon_of(cat) -> str:
    return CAT_ICON.get(cat) or GROUP_ICON.get(GROUP_OF.get(cat, "etc"), "🍽️")
GROUP_NAME = {key: name for key, name, _ in GROUPS}
MER_PREFIX = "https://web.paybooc.ai/mer/web/profile/"  # shortened in places.json, restored by app.js
SKIP_CATS = {"영화관", "기타식품소매업", "주류판매점", "유흥주점", "매점", "만화방", "스터디카페", "음식/식자재배달", "건어물/젓갈"}  # not restaurants

# 매출점수: percentile (lower = sells more) → 0..100, linear in log10(p) between anchors.
# Keep in sync with SCORE_ANCHORS in src/app.js and worker/lookup.js.
# 주인장 픽 640곳(중간값 상위 17.6%)이 같은 건물의 다른 가게들(중간값 30.6%)보다 확실히 높게 나오도록 맞춘 곡선:
# 픽 중간값 71점, 같은 건물 이웃 64점, 동네 순위(도로별 매출 상위) 78점.
SCORE_ANCHORS = [(0.01, 99), (0.1, 96), (0.5, 92), (1, 89), (3, 84), (5, 81), (10, 77),
                 (20, 71), (40, 61), (70, 48), (100, 35)]


# ------------------------------------------------------------------ fetch
def fetch(location: str) -> list:
    qs = urllib.parse.urlencode({"location": location})
    req = urllib.request.Request(f"{PROXY}?{qs}", headers={"User-Agent": "maechul-matjip/1.0"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        payload = json.load(resp)
    return payload.get("items") or []


def area_items(area: dict, refresh: bool) -> tuple[list, str]:
    path = CACHE / f"{area['slug']}.json"
    if path.exists() and not refresh:
        cached = json.loads(path.read_text())
        fetched = datetime.fromisoformat(cached["fetched_at"])
        if datetime.now(KST) - fetched < MAX_AGE:
            return cached["items"], cached["fetched_at"]
    merged, seen = [], set()
    for q in area["q"]:
        rows = None
        for wait in (0, 10, 30, 60):  # polite retries on rate limits / network errors
            if wait:
                time.sleep(wait)
            try:
                rows = fetch(q)
                break
            except Exception as exc:
                print(f"  ! {area['slug']} '{q}': {exc} (retry after {wait or 10}s)", file=sys.stderr)
        if rows is None:  # give up: keep the previous cache, never save a partial result
            if path.exists():
                cached = json.loads(path.read_text())
                return cached["items"], cached["fetched_at"]
            return [], ""
        for r in rows:
            url = r.get("merUrl") or ""
            if not url.startswith("https://web.paybooc.ai/") or url in seen:
                continue
            seen.add(url)
            merged.append(r)
        time.sleep(2.5)
    now = datetime.now(KST).isoformat(timespec="seconds")
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched_at": now, "queries": area["q"], "items": merged}, ensure_ascii=False))
    return merged, now


# ------------------------------------------------------------------ helpers
def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


CORP = r"\(주\)|㈜|주식회사|\(유\)|유한회사|\(사\)|농업회사법인|영농조합법인"


def clean_name(n) -> str:
    """Readable store name from eat.pl's merNm ('(주)미진/광화문미진', '주식회사 디에프아이(꿉당 성수점)')."""
    raw = str(n or "")
    parts = []
    for seg in raw.split("/"):
        s = re.sub(CORP, " ", seg)
        m = re.search(r"\(([^)]{2,})\)\s*$", s)  # '(주)회사명(브랜드 지점)' → '브랜드 지점'
        if m and re.search(CORP, seg):
            s = m.group(1)
        s = re.sub(r"\s+", " ", s).strip(" ,·-")
        if s:
            parts.append(s)
    if not parts:
        return raw.strip()
    if len(parts[0]) <= 2 and len(parts) > 1:
        return max(parts[1:], key=len)
    return parts[0]


def score_of(r: dict) -> int:
    def curve(p):
        try:
            p = float(p)
        except (TypeError, ValueError):
            return None
        p = min(max(p, SCORE_ANCHORS[0][0]), SCORE_ANCHORS[-1][0])
        x = math.log10(p)
        for (p0, s0), (p1, s1) in zip(SCORE_ANCHORS, SCORE_ANCHORS[1:]):
            if p <= p1:
                t = (x - math.log10(p0)) / (math.log10(p1) - math.log10(p0))
                return s0 + (s1 - s0) * t
        return SCORE_ANCHORS[-1][1]
    a, c = curve(r.get("allSaleRnk")), curve(r.get("ccgSaleRnk"))
    if a is None:
        return 0
    return round(0.75 * a + 0.25 * (c if c is not None else a))


def grade(s: int) -> str:
    if s >= 90:
        return "전국구"
    if s >= 80:
        return "찐맛집"
    if s >= 70:
        return "동네 강자"
    if s >= 60:
        return "꾸준한 집"
    return "숨은 집"


def pct(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    return f"{f:.2f}" if f < 1 else f"{f:.1f}"


def hhmm(s: str) -> str:
    return f"{s[:2]}:{s[2:]}" if s and len(s) == 4 else ""


def hours(srt: str, end: str) -> str:
    a, b = hhmm(srt), hhmm(end)
    if not a or not b:
        return ""
    return f"{a}–익일 {b}" if end < srt else f"{a}–{b}"


def open_for_lunch(srt: str, end: str) -> bool:
    if not srt or not end:
        return False
    if end < srt:  # overnight
        return srt <= "1130"
    return srt <= "1130" and end >= "1300"


def short_addr(a) -> str:
    a = re.sub(r"\(.*?\)", "", str(a or "")).split(",")[0]
    return re.sub(r"\s+", " ", a).strip()


def city_of(addr) -> str:
    return (str(addr or "").split() or ["기타"])[0]


def holidays(days) -> str:
    days = [d for d in (days or []) if d]
    return "휴무 " + "·".join(days) + "요일" if days else "휴무일 없음"


def is_busy(r: dict) -> bool:
    """아주 붐비는 집: 전국 같은 업종 매출 상위 0.5% 이내, 또는 1% 이내이면서 회식(단체) 결제가 있는 곳 (전체의 약 5%).
    Keep in sync with isBusy() in src/app.js."""
    try:
        p = float(r.get("allSaleRnk"))
    except (TypeError, ValueError):
        return False
    return p <= 0.5 or (p <= 1 and bool(r.get("empyVsitYn")))


BUSY_NOTE = "아주 붐비는 집 · 홀은 대화하기엔 시끄러울 수 있어요"
BUSY_ROOM_GROUP = "단체석·룸이 있을 가능성이 높으니 예약 때 물어보세요"
BUSY_ROOM = "조용한 자리라면 룸이 있는지 확인해 보세요"

# 대화하기 좋은 곳: 결제 데이터로 추정한 '조용히 이야기 나누기 좋은 집' (전체의 약 6%)
TALK_CATS = {"이탈리아음식", "프랑스음식", "기타외국식", "아시안", "멕시칸", "스시/초밥", "기타일식", "브런치카페/샐러드", "바(bar)"}
TALK_CHAIN = re.compile(r"(?<!본)점(\)|$)|지점|신세계|현대백화점|롯데|몰|타임스퀘어|스타필드|아이파크|코엑스")
TALK_KW = re.compile(r"와인|wine|비스트로|bistro|다이닝|dining|오마카세|omakase|트라토리아|오스테리아|osteria|trattoria", re.I)


def is_talk(r: dict) -> bool:
    """양식·일식·바 등 업종(또는 와인·다이닝 등 이름) + 아주 붐비는 집 아님 + 단체 회식 결제 없음
    + 저녁 영업(20:30 이후까지) + 아침 장사 아님 + 주 고객이 20~30대 + 같은 업종 매출 상위 1~60% (최상위 1%는 붐벼서 제외) + 체인 지점·쇼핑몰 매장 아님."""
    try:
        p = float(r.get("allSaleRnk"))
    except (TypeError, ValueError):
        return False
    srt, end = r.get("srtTime") or "", r.get("endTime") or ""
    if not srt or not end or p < 1 or p > 60 or r.get("empyVsitYn"):  # 상위 1% 안은 손님이 몰리는 집이라 제외
        return False
    if not (r.get("merTpbuzNm") in TALK_CATS or TALK_KW.search(r.get("merNm") or "")):
        return False
    if TALK_CHAIN.search(clean_name(r.get("merNm")).replace(" ", "")):  # 체인 지점·쇼핑몰 매장은 대체로 북적여서 제외
        return False
    cust = r.get("vistCstmrRnkList") or {}
    young = any(("20대" in (v or "") or "30대" in (v or "")) for v in (cust.get("rk01"), cust.get("rk02")))
    return (end < srt or end >= "2030") and srt >= "1030" and young


# 고급·가격대 높음(추정): 가격 정보가 없어 이름·업종·영업 패턴으로 추정한다.
PREMIUM_KW = re.compile(r"오마카세|omakase|우마카세|다이닝|dining|비스트로|bistro|프렌치|french|(^|[^타])코스|한우|와규|wagyu|"
                        r"스테이크하우스|steak ?house|가이세키|갓포|鮨|철판|teppan|트라토리아|오스테리아|리스토란테|ristorante|"
                        r"셰프|chef|chez|레스토랑|restaurant|스키야키|한정식", re.I)
PREMIUM_NOT = re.compile(r"정육|해장|곱창|국밥|순대|분식|뷔페|식당$")
PREMIUM_BAD_CATS = {"카페", "분식", "김밥전문점", "치킨", "버거", "피자", "호프", "기타주점", "빵/도넛", "기타베이커리",
                    "샌드위치/토스트", "도시락/죽", "브런치카페/샐러드", "푸드코트", "휴게소", "뷔페"}


def is_premium(r: dict) -> bool:
    """이름에 오마카세·다이닝·비스트로·한우·스테이크하우스 등이 있거나 프랑스음식, '스시○○' 같은 단독 스시집이고,
    체인 지점·쇼핑몰이 아니며, 점심~저녁 위주로 영업(11시 이후 시작, 23시 반 전 마감)하고,
    같은 업종 매출 상위 70% 안에 드는 곳. 확인된 목록(data/tags.json)이 있으면 그것도 포함."""
    if r.get("_tag_premium"):
        return True
    name = clean_name(r.get("merNm"))
    cat = r.get("merTpbuzNm") or ""
    if cat in PREMIUM_BAD_CATS or TALK_CHAIN.search(name.replace(" ", "")) or PREMIUM_NOT.search(name.replace(" ", "")):
        return False
    try:
        p = float(r.get("allSaleRnk"))
    except (TypeError, ValueError):
        return False
    srt, end = r.get("srtTime") or "", r.get("endTime") or ""
    if not srt or not end or srt < "1100" or end < srt or end > "2330" or p > 70:
        return False
    sushi = cat in ("스시/초밥", "기타일식") and re.match(r"^스시\s?\S{1,4}$", name)
    return bool(PREMIUM_KW.search(name) or cat == "프랑스음식" or sushi)  # 법인명(예: ○○다이닝)이 아닌 간판 이름으로 판단


def age_tags(r: dict) -> str:
    """주 고객 1·2위 연령: '20' '30' '40' '50'(50대·60대 이상), 둘 다 20~30대면 'young'."""
    cust = r.get("vistCstmrRnkList") or {}
    ages = []
    for k in ("rk01", "rk02"):
        m = re.match(r"(\d+)대", cust.get(k) or "")
        if m:
            ages.append(min(int(m.group(1)), 50))
    tags = {str(a) for a in ages if a >= 20}
    if len(ages) == 2 and all(a in (20, 30) for a in ages):
        tags.add("young")
    return " ".join(sorted(tags))


def sex_tag(r: dict) -> str:
    """주 고객 1·2위 성별: 둘 다 여성 'f', 둘 다 남성 'm', 섞여 있으면 'mix'."""
    cust = r.get("vistCstmrRnkList") or {}
    g = ["f" if "여성" in (cust.get(k) or "") else "m" if "남성" in (cust.get(k) or "") else "" for k in ("rk01", "rk02")]
    if "" in g:
        return ""
    return g[0] if g[0] == g[1] else "mix"


def tm128_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """eat.pl 좌표(TM128/KATEC, Bessel) → WGS84 위경도. 카카오 좌표와 비교해 오차 수십 m 이내."""
    a, f = 6377397.155, 1 / 299.1528128
    e2 = 2 * f - f * f
    ep2 = e2 / (1 - e2)
    k0, lat0, lon0, fe, fn = 0.9999, math.radians(38), math.radians(128), 400000, 600000

    def meridian(phi):
        return a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
                    - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
                    + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi)
                    - (35 * e2 ** 3 / 3072) * math.sin(6 * phi))
    mu = (meridian(lat0) + (y - fn) / k0) / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    n1 = a / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    t1, c1 = math.tan(phi1) ** 2, ep2 * math.cos(phi1) ** 2
    r1 = a * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    d = (x - fe) / (n1 * k0)
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
                                               + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = lon0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6 + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120) / math.cos(phi1)
    # Bessel(Tokyo) → WGS84: 지심 좌표로 바꿔 한국 지역 이동량 적용
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    X = n * math.cos(lat) * math.cos(lon) - 146.43
    Y = n * math.cos(lat) * math.sin(lon) + 507.89
    Z = n * (1 - e2) * math.sin(lat) + 681.46
    a2, f2 = 6378137.0, 1 / 298.257223563
    e22 = 2 * f2 - f2 * f2
    p = math.hypot(X, Y)
    lat2 = math.atan2(Z, p * (1 - e22))
    for _ in range(6):
        n2 = a2 / math.sqrt(1 - e22 * math.sin(lat2) ** 2)
        lat2 = math.atan2(Z + e22 * n2 * math.sin(lat2), p)
    return math.degrees(lat2), math.degrees(math.atan2(Y, X))


def latlng(r: dict):
    try:
        lat, lng = tm128_to_wgs84(float(r.get("xaxsVal")), float(r.get("yaxsVal")))
    except (TypeError, ValueError):
        return None
    return (round(lat, 5), round(lng, 5)) if 33 < lat < 39 and 124 < lng < 132 else None


def mer_id(url) -> str:
    return str(url or "").rstrip("/").rsplit("/", 1)[-1]


# 프랜차이즈: 이 사이트가 모은 가게 중 같은 상호가 4곳 이상이거나 잘 알려진 체인 (main 에서 FRANCHISE 를 채운다)
CHAIN_MIN = 4
CHAIN_KNOWN = ("스타벅스", "메가엠지씨", "메가커피", "컴포즈", "빽다방", "이디야", "투썸", "할리스", "커피빈", "폴바셋", "파스쿠찌",
               "공차", "더벤티", "매머드", "바나프레소", "텐퍼센트", "맥도날드", "롯데리아", "버거킹", "케이에프씨", "kfc", "맘스터치",
               "써브웨이", "서브웨이", "파리바게뜨", "뚜레쥬르", "배스킨", "던킨", "크리스피크림", "이삭토스트", "본죽", "교촌",
               "비에이치씨", "bhc", "비비큐", "bbq", "굽네", "도미노", "피자헛", "김밥천국", "한솥", "본도시락", "새마을식당",
               "홍콩반점", "역전할머니", "신전떡볶이", "엽기떡볶이", "죠스떡볶이", "설빙", "아웃백", "빕스", "애슐리")
FRANCHISE: set = set()


def brand_of(r: dict) -> str:
    n = re.sub(r"\(.*?\)", "", clean_name(r.get("merNm"))).strip()
    t = n.split()
    if len(t) >= 2 and re.search(r"(점|지점|호점|직영점|본점)$", t[-1]):
        t = t[:-1]
    b = "".join(t)
    if len(t) == 1:  # '투썸플레이스을지트윈타워점' 처럼 붙여 쓴 지점명
        b = re.sub(r"([가-힣A-Za-z0-9]{2,}?)([가-힣0-9]{1,8}(역|대|동|로|타워|센터|빌딩|몰|광장|사거리|점)?점)$", r"\1", b)
    return b.lower()


def is_chain(r: dict) -> bool:
    b = brand_of(r)
    return b in FRANCHISE or any(b.startswith(k) for k in CHAIN_KNOWN)


def card(r: dict, rank: int) -> str:
    cat = r.get("merTpbuzNm") or "기타"
    g = GROUP_OF.get(cat, "etc")
    lunch = open_for_lunch(r.get("srtTime") or "", r.get("endTime") or "") and g not in ("drink", "cafe")
    s = r["_score"]
    tags = []
    if r.get("_pick"):
        tags.append('<span class="tag pick">주인장 픽</span>')
    premium = is_premium(r)
    chain = is_chain(r)
    talk = is_talk(r) or (premium and not is_busy(r)) or bool(r.get("_tag_room"))
    if r.get("_tag_room"):
        tags.append('<span class="tag room">룸 있음</span>')
    if premium:
        tags.append(f'<span class="tag premium">{"고급" if r.get("_tag_premium") else "고급 추정"}</span>')
    if talk:
        tags.append('<span class="tag talk">대화하기 좋은 곳</span>')
    for on, t in ((r.get("empyVsitYn"), "회식 적합"), (r.get("loclPrsnPpltYn"), "동네 맛집"), (r.get("frnrVsitYn"), "외국인 방문")):
        if on:
            tags.append(f'<span class="tag">{t}</span>')
    if chain:
        tags.append('<span class="tag chain">프랜차이즈</span>')
    cust = r.get("vistCstmrRnkList") or {}
    cust_txt = ", ".join(v for v in (cust.get("rk01"), cust.get("rk02"), cust.get("rk03")) if v)
    peak = " ".join(x for x in (f"{r.get('congestionDwk')}요일" if r.get("congestionDwk") else "", r.get("congestionTizn") or "") if x)
    busy = is_busy(r)
    flags = " ".join(f for f, on in (("lunch", lunch), ("group", r.get("empyVsitYn")), ("local", r.get("loclPrsnPpltYn")),
                                    ("foreign", r.get("frnrVsitYn")), ("pick", r.get("_pick")), ("busy", busy), ("talk", talk), ("premium", premium)) if on)
    gs = g + ("" if g in ("drink", "cafe") else " meal")
    hrs = hours(r.get("srtTime") or "", r.get("endTime") or "")
    notes = []
    if busy:
        room = "" if r.get("_tag_room") else (BUSY_ROOM_GROUP if r.get("empyVsitYn") else BUSY_ROOM)
        notes.append(f'<p class="note busy">{BUSY_NOTE}{(" · " + room) if room else ""}</p>')
    if premium and s < 75:
        rk = r.get("_prem_rank")
        notes.append('<p class="note premium">가격대가 높은 소규모 업장으로 보여요 · 손님 수가 적어 매출점수가 낮게 나오는 유형이에요'
                     + (f' (고급 추정 {rk[1]}곳 중 매출 {rk[0]}위)' if rk else '') + '</p>')
    note = "".join(notes)
    return f"""<li class="card" id="p-{esc(mer_id(r.get('merUrl')))}" data-f="{esc(flags)}" data-g="{gs}" data-a="{age_tags(r)}" data-s="{sex_tag(r)}" data-k="{'chain' if chain else 'indie'}" data-c="{esc(city_of(r.get('merAddr')))}">
  <div class="card-head"><span class="rank">{rank}</span><span class="ico" aria-hidden="true">{icon_of(cat)}</span><h3>{esc(r.get('_name') or clean_name(r.get('merNm')))}</h3><span class="cat">{esc(cat)}</span>
  <span class="score" title="매출점수"><b>{s}</b>점<small>{grade(s)}</small></span></div>
  <p class="nums"><strong>전국 같은 업종 매출 상위 {pct(r.get('allSaleRnk'))}%</strong> · 구 안에서 상위 {pct(r.get('ccgSaleRnk'))}%</p>
  {note}<p class="meta">{esc(short_addr(r.get('merAddr')))}</p>
  <p class="meta">{'영업 예상 ' + esc(hrs) + ' · ' if hrs else ''}{esc(holidays(r.get('hldyDwk')))}{(' · 붐비는 때 ' + esc(peak)) if peak else ''}</p>
  {f'<p class="meta">주 고객 {esc(cust_txt)}</p>' if cust_txt else ''}
  {''.join(tags)}
  <div class="actions"><a class="go" href="{esc(r.get('merUrl'))}" target="_blank" rel="noopener">eat.pl에서 메뉴·리뷰 요약 보기 →</a>{'<button type="button" class="map-btn">지도</button>' if latlng(r) else ''}<button type="button" class="share">공유</button></div>
</li>"""


def group_chips(present: set, default: str = "meal") -> str:
    chips = [("meal", "식사"), ("", "전체")] + [(k, n) for k, n, _ in GROUPS if k in present]
    on = ' class="on"'
    return "".join(f'<button type="button" data-v="{k}"{on if k == default else ""}>{n}</button>' for k, n in chips)


def page(title: str, desc: str, body: str, path: str, depth: int) -> str:
    up = "../" * depth
    canonical = f"{BASE_URL}/{path}" if BASE_URL else ""
    og_img = f"{BASE_URL}/assets/og.png" if BASE_URL else f"{up}assets/og.png"
    return f"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="strict-origin-when-cross-origin">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
{f'<link rel="canonical" href="{esc(canonical)}">' if canonical else ''}
<meta property="og:type" content="website"><meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}">
<meta property="og:image" content="{esc(og_img)}">{f'<meta property="og:url" content="{esc(canonical)}">' if canonical else ''}
<meta name="twitter:card" content="summary_large_image">
<link rel="stylesheet" href="{up}assets/style.css">
</head><body data-root="{up}"{f' data-api="{esc(LOOKUP_API)}"' if LOOKUP_API else ''}>
<div id="inapp" class="inapp" hidden></div>
<header class="top"><a class="brand" href="{up}index.html">{SITE_NAME}</a><span class="tagline">{TAGLINE}</span>
<nav class="topnav"><a href="{up}index.html#search">점수 검색</a><a href="{up}map.html">지도</a><a href="{up}picks.html">주인장 픽</a></nav></header>
<main>{body}</main>
<footer>
<p>데이터: <strong>eat.pl 잇플 · BC카드 결제 데이터 기반</strong> (최근 1개월 매출 순위). 백분위는 같은 업종 안에서의 매출 순위이며 낮을수록 상위입니다.</p>
<p><strong>매출점수</strong>는 이 백분위(전국 75% + 구 안 25%)를 0~100점으로 바꾼 값입니다. 맛 평가가 아니라 얼마나 많이 팔리는지를 보여 주므로, 좌석이 적거나 예약제인 집은 낮게 나올 수 있어요.</p>
<p>영업 예상 시간은 최근 1개월 결제 승인 시각으로 추정한 값이라 공식 영업시간과 다를 수 있습니다. 이 사이트는 BC카드나 eat.pl의 공식 서비스가 아닙니다.</p>
</footer>
<script src="{up}assets/app.js"></script>
</body></html>"""


AGE_NAV = ('<nav class="filters" data-key="a" aria-label="주 고객">'
           '<button type="button" data-v="" class="on">모든 손님</button>'
           '<button type="button" data-v="young">20~30대가 많이 가는 곳</button>'
           '<button type="button" data-v="20">20대</button>'
           '<button type="button" data-v="30">30대</button>'
           '<button type="button" data-v="40">40대</button>'
           '<button type="button" data-v="50">50대 이상</button></nav>'
           '<nav class="filters" data-key="s" aria-label="주 고객 성별">'
           '<button type="button" data-v="" class="on">모든 성별</button>'
           '<button type="button" data-v="f">여성 손님 위주</button>'
           '<button type="button" data-v="m">남성 손님 위주</button>'
           '<button type="button" data-v="mix">남녀 고루</button></nav>')


def list_section(cards: str, present: set, cities: list | None = None, purpose: bool = True, default: str = "meal",
                 chain_default: str = "indie") -> str:
    city_nav = ""
    if cities and len(cities) > 1:
        city_nav = ('<nav class="filters" data-key="c" aria-label="지역">'
                    + '<button type="button" data-v="" class="on">전 지역</button>'
                    + "".join(f'<button type="button" data-v="{esc(c)}">{esc(c)}</button>' for c in cities) + "</nav>")
    purpose_nav = ""
    if purpose:
        purpose_nav = ('<nav class="filters" data-key="f" aria-label="상황">'
                       '<button type="button" data-v="" class="on">모든 상황</button>'
                       '<button type="button" data-v="lunch">점심 영업</button>'
                       '<button type="button" data-v="group">회식</button>'
                       '<button type="button" data-v="talk">대화하기 좋은 곳</button>'
                       '<button type="button" data-v="premium">고급</button>'
                       '<button type="button" data-v="local">동네 맛집</button>'
                       '<button type="button" data-v="foreign">외국인 많이 가는 곳</button>'
                       '<button type="button" data-v="pick">주인장 픽</button></nav>')
    return f"""{city_nav}{AGE_NAV}<div class="sticky">
<nav class="filters" data-key="g" aria-label="종류">{group_chips(present, default)}</nav>
{purpose_nav}</div>
<div class="listhead"><p class="count" id="count"></p>
<nav class="filters sortnav" data-key="k" aria-label="프랜차이즈"><button type="button" data-v="indie"{' class="on"' if chain_default == "indie" else ''}>프랜차이즈 빼고</button><button type="button" data-v=""{' class="on"' if chain_default != "indie" else ''}>포함</button></nav></div>
<ol class="list" id="list">
{cards}
</ol>"""


# ------------------------------------------------------------------ build
def main():
    refresh = "--refresh" in sys.argv
    areas = json.loads((ROOT / "areas.json").read_text())
    picks = json.loads(PICKS.read_text()) if PICKS.exists() else {"picks": [], "items": []}
    pick_name = {p["merUrl"]: p["name"] for p in picks.get("picks", [])}
    tag_file = ROOT / "data" / "tags.json"
    tdata = json.loads(tag_file.read_text()) if tag_file.exists() else {}
    tags = {k: set(tdata.get(k) or []) for k in ("room", "premium")}  # merUrl 또는 가게 이름

    OUT.mkdir(exist_ok=True)
    for d in ("area", "cat", "assets"):
        (OUT / d).mkdir(exist_ok=True)
    for f in ("style.css", "app.js", "map.js"):
        (OUT / "assets" / f).write_text((SRC / f).read_text())
    if (SRC / "og.png").exists():
        (OUT / "assets" / "og.png").write_bytes((SRC / "og.png").read_bytes())

    places = {}  # merUrl → item (every place we know: area pages + picks lookups)

    def prep(r):
        url = r.get("merUrl") or ""
        if not url.startswith("https://web.paybooc.ai/"):
            return None
        if url in places:
            return places[url]
        r = dict(r)
        nm = {clean_name(r.get("merNm")), pick_name.get(url, "")}
        r["_tag_room"] = url in tags["room"] or bool(nm & tags["room"])
        r["_tag_premium"] = url in tags["premium"] or bool(nm & tags["premium"])
        if r.get("merTpbuzNm") in SKIP_CATS and not is_premium(r):
            return None
        r["_score"] = score_of(r)
        r["_pick"] = url in pick_name
        r["_name"] = pick_name.get(url) or clean_name(r.get("merNm"))
        r["_area"] = ""
        places[url] = r
        return r

    index_cards, latest, built_areas, area_rows = {}, "", [], []
    for a in areas:
        raw, fetched = area_items(a, refresh)
        items = [x for x in (prep(r) for r in raw) if x]
        if not items:
            print(f"{a['slug']:16s}   - skipped (no data yet)")
            continue
        built_areas.append(a)
        for r in items:
            r["_area"] = r["_area"] or a["slug"]
        latest = max(latest, fetched)
        area_rows.append((a, items, fetched))
    for r in picks.get("items", []):
        prep(r)
    brand_count = {}
    for r in places.values():
        b = brand_of(r)
        brand_count[b] = brand_count.get(b, 0) + 1
    FRANCHISE.update(b for b, n in brand_count.items() if n >= CHAIN_MIN and len(b) >= 2)
    prem = sorted((r for r in places.values() if is_premium(r)), key=lambda r: float(r.get("allSaleRnk") or 999))
    for i, r in enumerate(prem):
        r["_prem_rank"] = (i + 1, len(prem))

    for a, items, fetched in area_rows:
        items.sort(key=lambda r: (-r["_score"], float(r.get("allSaleRnk") if r.get("allSaleRnk") is not None else 999)))
        print(f"{a['slug']:16s} {len(items):3d} places")
        updated = fetched[:10]
        cards = "\n".join(card(r, i + 1) for i, r in enumerate(items))
        meals = [r for r in items if GROUP_OF.get(r.get("merTpbuzNm")) not in ("drink", "cafe")]
        top3 = ", ".join(r["_name"] for r in [m for m in meals if not is_chain(m)][:3])
        present = {GROUP_OF.get(r.get("merTpbuzNm"), "etc") for r in items}
        body = f"""
<section class="hero small">
  <p class="crumb"><a href="../index.html">전체 지역</a> › {esc(a['city'])}</p>
  <h1>{esc(a['name'])} 점심·회식 맛집 <span>매출점수 순위</span></h1>
  <p class="sub">BC카드 최근 1개월 결제 기준 매출 상위 {len(items)}곳 · {esc(updated)} 업데이트</p>
  <button type="button" class="share-page">이 동네 순위 공유하기</button>
</section>
<section class="picker" id="picker">
  <h2>오늘 점심 뭐 먹지?</h2>
  <p>이 동네 점심 영업하는 매출 상위 집 중에서 3곳을 골라 드려요.</p>
  <button type="button" id="pick">3곳 뽑기</button>
  <ol id="picked" class="picked"></ol>
  <button type="button" id="pick-share" class="share-page" hidden>이 3곳 팀에 공유하기</button>
</section>
{list_section(cards, present)}"""
        title = f"{a['name']} 점심·회식 맛집 매출 순위 TOP {len(items)} | {SITE_NAME}"
        desc = f"{a['name']} 맛집을 별점이 아닌 BC카드 결제 매출점수로. {top3} 등. 한식·고기·일식·술집 등 종류별, 영업 예상 시간, 회식 적합 여부까지."
        (OUT / "area" / f"{a['slug']}.html").write_text(page(title, desc, body, f"area/{a['slug']}.html", 1))
        index_cards.setdefault(a["city"], []).append((a, len(items), top3))

    area_name = {a["slug"]: a["name"] for a in built_areas}

    ranked = sorted(places.values(), key=lambda r: (-r["_score"], float(r.get("allSaleRnk") or 999)))

    # ---- category pages (all known places)
    cat_links = []
    for key, name, _ in GROUPS:
        rows = [r for r in ranked if GROUP_OF.get(r.get("merTpbuzNm")) == key][:200]
        if len(rows) < 10:
            continue
        cities = sorted({city_of(r.get("merAddr")) for r in rows}, key=lambda c: -sum(1 for r in rows if city_of(r.get("merAddr")) == c))
        cards = "\n".join(card(r, i + 1) for i, r in enumerate(rows))
        body = f"""
<section class="hero small">
  <p class="crumb"><a href="../index.html">홈</a> › 종류별 순위</p>
  <h1>{esc(name)} 맛집 <span>매출점수 TOP {len(rows)}</span></h1>
  <p class="sub">이 사이트가 모은 {len(places):,}곳 중 {esc(name)} 업종을 매출점수 순으로 모았습니다.</p>
  <button type="button" class="share-page">이 순위 공유하기</button>
</section>
{list_section(cards, {key}, cities, default=key)}"""
        title = f"{name} 맛집 매출 순위 TOP {len(rows)} | {SITE_NAME}"
        desc = f"별점 말고 BC카드 결제 매출로 고른 {name} 맛집 순위. " + ", ".join(r["_name"] for r in rows[:3]) + " 등."
        (OUT / "cat" / f"{key}.html").write_text(page(title, desc, body, f"cat/{key}.html", 1))
        cat_links.append((key, name, len(rows)))

    # ---- picks page
    prows = [r for r in ranked if r["_pick"]]
    if prows:
        cities = sorted({city_of(r.get("merAddr")) for r in prows}, key=lambda c: -sum(1 for r in prows if city_of(r.get("merAddr")) == c))
        cards = "\n".join(card(r, i + 1) for i, r in enumerate(prows))
        avg = round(sum(r["_score"] for r in prows) / len(prows))
        body = f"""
<section class="hero small">
  <p class="crumb"><a href="index.html">홈</a> › 주인장 픽</p>
  <h1>주인장 픽 <span>{len(prows)}곳</span></h1>
  <p class="sub">이 사이트를 만든 사람이 오래전부터 지도에 모아 온 맛집 중 eat.pl 데이터가 있는 곳입니다. 가성비 집, 데이트 코스, 유명한 집이 섞여 있어요. 평균 매출점수 {avg}점.</p>
  <button type="button" class="share-page">주인장 픽 공유하기</button>
</section>
{list_section(cards, {GROUP_OF.get(r.get('merTpbuzNm'), 'etc') for r in prows}, cities, default="", chain_default="")}"""
        (OUT / "picks.html").write_text(page(f"주인장 픽 — 직접 모은 맛집 {len(prows)}곳의 매출점수 | {SITE_NAME}",
                                             "사이트 주인이 지도에 모아 온 전국 맛집을 BC카드 결제 매출점수로 다시 봤습니다.",
                                             body, "picks.html", 0))

    # ---- 대화하기 좋은 곳
    trows = [r for r in ranked if is_talk(r) or (is_premium(r) and not is_busy(r)) or r["_tag_room"]]
    if trows:
        cities = sorted({city_of(r.get("merAddr")) for r in trows}, key=lambda c: -sum(1 for r in trows if city_of(r.get("merAddr")) == c))
        cards = "\n".join(card(r, i + 1) for i, r in enumerate(trows))
        body = f"""
<section class="hero small">
  <p class="crumb"><a href="index.html">홈</a> › 대화하기 좋은 곳</p>
  <h1>조용히 대화하기 좋은 곳 <span>{len(trows)}곳</span></h1>
  <p class="sub">소개팅, 데이트, 오랜만의 친구 모임처럼 대화가 중요한 자리에. 결제 데이터로 골랐어요: 양식·일식·바 같은 업종, 단체 회식 결제가 없고, 손님이 몰리는 매출 최상위 집과 체인 지점·쇼핑몰 매장은 빼고, 저녁까지 영업하며 20~30대 손님이 많은 곳입니다.</p>
  <p class="sub small-note">실제 소음을 잰 것은 아니라서 분위기는 다를 수 있어요. eat.pl 상세 페이지에서 붐비는 시간대를 꼭 확인해 보세요.</p>
  <button type="button" class="share-page">이 목록 공유하기</button>
</section>
{list_section(cards, {GROUP_OF.get(r.get('merTpbuzNm'), 'etc') for r in trows}, cities, purpose=False, default="")}"""
        (OUT / "talk.html").write_text(page(f"조용히 대화하기 좋은 맛집 {len(trows)}곳 — 소개팅·데이트 장소 | {SITE_NAME}",
                                            "단체 회식이 없고 너무 붐비지 않는, 저녁에 조용히 대화하기 좋은 양식·일식·바. BC카드 결제 데이터로 골랐습니다.",
                                            body, "talk.html", 0))

    # ---- 고급 추정
    if prem:
        cities = sorted({city_of(r.get("merAddr")) for r in prem}, key=lambda c: -sum(1 for r in prem if city_of(r.get("merAddr")) == c))
        cards = "\n".join(card(r, i + 1) for i, r in enumerate(prem))
        body = f"""
<section class="hero small">
  <p class="crumb"><a href="index.html">홈</a> › 고급·가격대 있는 집</p>
  <h1>가격대 있는 고급 맛집 <span>{len(prem)}곳</span></h1>
  <p class="sub">오마카세, 파인다이닝, 비스트로, 한우·스테이크하우스처럼 가격대가 있는 집을 모았어요. 이런 곳은 손님 수가 적어 매출점수가 낮게 나오기 쉬워서, 여기서는 고급 업장끼리 매출 순으로 줄 세웠습니다.</p>
  <p class="sub small-note">결제 데이터에는 가격 정보가 없어서 가게 이름·업종·영업 시간 패턴으로 추정했어요. 체인 지점과 쇼핑몰 매장은 뺐습니다. 실제 가격은 eat.pl이나 예약 앱에서 확인해 주세요.</p>
  <button type="button" class="share-page">이 목록 공유하기</button>
</section>
{list_section(cards, {GROUP_OF.get(r.get('merTpbuzNm'), 'etc') for r in prem}, cities, purpose=False, default="")}"""
        (OUT / "premium.html").write_text(page(f"오마카세·파인다이닝 등 고급 맛집 {len(prem)}곳 매출 순위 | {SITE_NAME}",
                                               "오마카세, 파인다이닝, 비스트로, 한우·스테이크하우스 등 가격대 있는 집을 BC카드 결제 매출로 줄 세웠습니다.",
                                               body, "premium.html", 0))

    # ---- 지도
    body = """
<section class="hero small map-hero">
  <p class="crumb"><a href="index.html">홈</a> › 지도</p>
  <h1>지도로 보는 <span>매출맛집</span></h1>
  <p class="sub">지도를 움직이면 그 화면 안에서 매출점수가 높은 집을 보여 드려요. <b>내 위치</b>를 누르면 주변 동네 매출 상위 가게를 실시간으로 불러오고, 점수순·가까운 순으로 볼 수 있어요.</p>
</section>
<div class="map-tools">
  <button type="button" id="locate" class="locate">◎ 내 위치</button>
  <button type="button" id="here" class="here" hidden>이 지역 실시간으로 찾기</button>
  <button type="button" id="chain-toggle" class="chain-toggle on">✓ 프랜차이즈 빼고</button>
  <span id="map-status" class="map-status" aria-live="polite"></span>
</div>
<nav class="filters" id="map-filter" aria-label="보기">
  <button type="button" data-v="meal" class="on">식사</button><button type="button" data-v="">전체</button>
  <button type="button" data-v="korean">한식</button><button type="button" data-v="meat">고기</button>
  <button type="button" data-v="seafood">해산물</button><button type="button" data-v="japanese">일식</button>
  <button type="button" data-v="chinese">중식</button><button type="button" data-v="western">양식·아시안</button>
  <button type="button" data-v="quick">분식·간편식</button><button type="button" data-v="drink">술집</button>
  <button type="button" data-v="cafe">카페·디저트</button><button type="button" data-v="talk">대화하기 좋은 곳</button>
  <button type="button" data-v="premium">고급</button><button type="button" data-v="pick">주인장 픽</button>
</nav>
<div id="map" class="map" role="region" aria-label="맛집 지도"></div>
<div class="map-listhead"><p class="count" id="map-count"></p>
<nav class="filters sortnav" id="map-sort" aria-label="정렬" hidden><button type="button" data-v="score" class="on">점수순</button><button type="button" data-v="dist">가까운 순</button></nav></div>
<ol class="list" id="map-list"></ol>
<p class="small-note sub">지도 © OpenStreetMap 기여자. 가게 위치는 결제 데이터의 좌표라 실제 입구와 수십 m 다를 수 있어요.</p>
"""
    html_map = page(f"내 주변 맛집 지도 — 매출점수로 보는 동네 맛집 | {SITE_NAME}",
                    "지도에서 화면 안 매출점수 높은 집을 보고, 내 위치 주변 맛집을 가까운 순으로. BC카드 결제 데이터 기반.",
                    body, "map.html", 0)
    html_map = html_map.replace('<link rel="stylesheet" href="assets/style.css">',
                                '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">\n<link rel="stylesheet" href="assets/style.css">')
    html_map = html_map.replace('<script src="assets/app.js"></script>',
                                '<script src="assets/app.js"></script>\n<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>\n<script src="assets/map.js"></script>')
    (OUT / "map.html").write_text(html_map)

    # ---- search index (loaded lazily by the home page search box)
    index = [[r["_name"], GROUP_OF.get(r.get("merTpbuzNm"), "etc"), r.get("merTpbuzNm") or "", short_addr(r.get("merAddr")),
              r["_score"], r.get("allSaleRnk"), r.get("ccgSaleRnk"), r.get("merUrl").replace(MER_PREFIX, "", 1), r["_area"], 1 if r["_pick"] else 0, 1 if is_busy(r) else 0,
              1 if (is_talk(r) or (is_premium(r) and not is_busy(r)) or r["_tag_room"]) else 0, 1 if is_premium(r) else 0,
              1 if r.get("empyVsitYn") else 0, *(latlng(r) or (0, 0)), 1 if is_chain(r) else 0]
             for r in ranked]
    (OUT / "assets" / "places.json").write_text(json.dumps(
        {"areas": area_name, "groups": GROUP_NAME, "places": index,
         "chains": sorted(FRANCHISE), "chainKnown": list(CHAIN_KNOWN)}, ensure_ascii=False, separators=(",", ":")))

    # ---- home
    groups = []
    for city, rows in index_cards.items():
        tiles = "".join(
            f'<a class="tile" href="area/{esc(a["slug"])}.html" data-name="{esc(a["name"])} {esc(city)}">'
            f'<strong>{esc(a["name"])}</strong><span>{n}곳 · {esc(top3)}</span></a>'
            for a, n, top3 in rows)
        groups.append(f'<section class="city"><h2>{esc(city)}</h2><div class="tiles">{tiles}</div></section>')
    cats = "".join(f'<a class="chip" href="cat/{k}.html">{esc(n)}</a>' for k, n, _ in cat_links)
    body = f"""
<section class="hero" id="search">
  <h1>내가 아는 그 집, <span>매출점수</span>는 몇 점?</h1>
  <p class="sub">별점이나 광고 말고, BC카드 최근 1개월 결제 데이터로 실제로 얼마나 팔리는 집인지 0~100점으로 보여 드려요. 가게 이름이나 동네를 검색해 보세요.</p>
  <input id="q" type="search" placeholder="가게 이름이나 동네 (예: 진주회관, 을지로)" aria-label="가게 이름이나 동네 검색" autocomplete="off">
  <a class="near-cta" href="map.html#near">◎ 내 주변 맛집 지도로 보기</a>
  <div id="results" class="results" aria-live="polite"></div>
</section>
<section class="browse">
  <h2>종류별 매출점수 순위</h2>
  <div class="chips">{cats}<a class="chip talk" href="talk.html">조용히 대화하기 좋은 곳</a><a class="chip premium" href="premium.html">고급·가격대 있는 집</a><a class="chip pick" href="picks.html">주인장 픽 {len(prows)}곳</a></div>
</section>
<div id="areas"><h2 class="section-title">동네별 점심·회식 맛집</h2>
{''.join(groups)}</div>
<section class="how">
  <h2>점수는 이렇게 읽어요</h2>
  <ul>
    <li><strong>매출점수</strong> — 전국 같은 업종 가게 중 매출 순위(75%)와 같은 구 안 순위(25%)를 0~100점으로 바꾼 값입니다. 상위 0.1%면 96점, 1%면 89점, 10%면 77점, 40%면 61점 정도예요.</li>
    <li><strong>전국구 90+ · 찐맛집 80+ · 동네 강자 70+ · 꾸준한 집 60+</strong> — 맛 평가가 아니라 판매량 기준이라, 좌석이 적은 집이나 예약제 식당은 낮게 나올 수 있어요.</li>
    <li><strong>손님 연령 필터</strong> — 결제 고객 중 많은 연령·성별 1·2위를 기준으로 걸러요. "20~30대가 많이 가는 곳"은 1·2위가 모두 20~30대인 곳(전체의 약 38%)이에요. 성별도 같은 방식으로, 1·2위가 모두 여성이면 "여성 손님 위주"(약 20%), 모두 남성이면 "남성 손님 위주"(약 44%), 섞여 있으면 "남녀 고루"예요.</li>
    <li><strong>회식 적합</strong> — 저녁 시간대 단체 결제가 있었던 곳. <strong>동네 맛집</strong> — 동네 손님 비율이 구 평균보다 높은 곳.</li>
    <li><strong>아주 붐비는 집</strong> — 전국 같은 업종 매출 상위 0.5% 안(회식 적합이면 1% 안)인 곳이에요. 인기는 확실하지만 조용히 대화할 자리라면 붐비는 시간을 피하는 게 좋아요.</li>
    <li><strong>대화하기 좋은 곳</strong> — 양식·일식·바 같은 업종 중 단체 회식 결제가 없고, 아주 붐비는 집이 아니며, 저녁까지 영업하고 20~30대 손님이 많은 곳이에요. 고급 업장과 룸이 있는 집도 포함해요. <a href="talk.html">모아 보기</a></li>
    <li><strong>고급 추정</strong> — 오마카세·파인다이닝·비스트로·한우 등 가격대가 있어 보이는 집이에요. 손님 수가 적어 매출점수가 낮게 나오기 쉬워서 고급 업장끼리 순위를 따로 보여 드려요. <a href="premium.html">모아 보기</a></li>
  </ul>
</section>"""
    title = f"{SITE_NAME} — 우리 동네 맛집 매출점수 검색, 점심·회식 맛집 순위"
    desc = f"별점 말고 실제 결제 매출로 매긴 맛집 점수. {len(places):,}곳 검색, 한식·고기·일식 등 종류별 순위, 을지로·강남·여의도·판교 등 {len(built_areas)}개 동네 점심·회식 맛집."
    (OUT / "index.html").write_text(page(title, desc, body, "index.html", 0))

    if BASE_URL:
        urls = ([f"{BASE_URL}/index.html", f"{BASE_URL}/picks.html", f"{BASE_URL}/talk.html", f"{BASE_URL}/premium.html", f"{BASE_URL}/map.html"]
                + [f"{BASE_URL}/area/{a['slug']}.html" for a in built_areas]
                + [f"{BASE_URL}/cat/{k}.html" for k, _, _ in cat_links])
        sm = "".join(f"<url><loc>{esc(u)}</loc><lastmod>{latest[:10]}</lastmod></url>" for u in urls)
        (OUT / "sitemap.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sm}</urlset>')
        (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n")
    else:
        (OUT / "robots.txt").write_text("User-agent: *\nAllow: /\n")
    print(f"built {len(built_areas)} area pages, {len(cat_links)} category pages, {len(prows)} picks, {len(places)} places into {OUT}")


if __name__ == "__main__":
    random.seed(0)
    main()
