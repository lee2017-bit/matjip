#!/usr/bin/env python3
"""Build the static site: fetch restaurant rankings per area, then render HTML pages.

Usage:
  python3 build.py                 # use cached data; fetch only areas that have no cache yet
  python3 build.py --refresh       # fetch everything again
  python3 build.py --rotate        # weekly schedule: fetch again only a quarter of the areas
  BASE_URL=https://example.github.io/eatpl python3 build.py   # absolute URLs for sitemap / share previews
  LOOKUP_API=https://matjip-lookup.example.workers.dev python3 build.py   # enable live lookups (worker/)

Data: eat.pl 잇플 search (BC카드 결제 데이터 기반) via the public k-skill proxy.
Python standard library only.
"""
import hashlib
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
# 식당이 아닌 등록: 업종 미상 묶음(○○(구)_NICE), 단체급식·케이터링 업체
NOT_RESTAURANT = re.compile(r"_NICE|\(구\)_|케이터링|급식|위탁")
# 데이터 이상치 신호 (검토용). 이름이 식당 같지 않으면서 다른 신호가 하나 더 있으면 싣지 않는다.
NONFOOD_NAME = re.compile(r"오락실|PC|피씨|노래|코인|마트|편의점|약국|병원|의원|학원|주유|세차|세탁|모텔|호텔|숙박|부동산|헬스|미용|네일|"
                          r"안경|통신|휴대폰|상사|무역|물류|유통|건설|기획|컨설팅|에이전시|엔터|스튜디오|공사|센터$", re.I)
ALLDAY_OK = {"찜/탕/찌개", "국수/면/만두", "분식", "김밥전문점", "호프", "기타주점", "카페", "푸드코트", "휴게소"}


def anomaly_reasons(r: dict) -> list:
    """결제 데이터가 보통 식당과 다르게 보이는 이유들 (판정이 아니라 검토용 신호)."""
    out = []
    sc = score_of(r)
    s_, e_ = r.get("srtTime") or "", r.get("endTime") or ""
    if len(s_) == 4 and len(e_) == 4 and s_.isdigit() and e_.isdigit():
        sh, eh = int(s_[:2]) + int(s_[2:]) / 60, int(e_[:2]) + int(e_[2:]) / 60
        if eh <= sh:
            eh += 24
        if sc >= 85 and eh - sh >= 20 and r.get("merTpbuzNm") not in ALLDAY_OK:
            out.append("하루 20시간 이상 결제가 있음")
    raw = r.get("merNm") or ""
    if NONFOOD_NAME.search(clean_name(raw)):
        out.append("이름이 식당 같지 않음")
    if sc >= 90 and re.search(CORP, raw) and "/" not in raw and not re.search(r"\(([^)]{2,})\)\s*$", raw):
        out.append("법인 이름만 있음")
    k = KCHECK.get(mer_id(r.get("merUrl")))
    if k and "none" not in k and not (k.get("g") in ("FD6", "CE7") or (k.get("c") or "").startswith("음식점")):
        out.append(f"카카오맵에 음식점이 아닌 곳으로 나옴({k.get('c')})")
    return out


def anomaly_exclude(reasons: list) -> bool:
    """확실한 경우만 뺀다: 이름부터 식당이 아니고 신호가 하나 더 있거나, 신호가 세 개 이상."""
    return ("이름이 식당 같지 않음" in reasons and len(reasons) >= 2) or len(reasons) >= 3


LOW_CUT = 50  # 전국 같은 업종 매출 하위 절반(상위 50% 밖)은 싣지 않는다. 주인장 픽·고급 추정은 예외
ROTATE = 4  # --rotate: 매주 동네의 1/4만 새로 받는다 (한 번에 많이 부르지 않기)

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


# 카카오맵 교차검증 결과 (tools/kakao_check.py → data/kakao_check.json): eat.pl 업종(카드 가맹점 등록 업종)보다 실제에 가깝다
KCHECK = json.loads((ROOT / "data" / "kakao_check.json").read_text()) if (ROOT / "data" / "kakao_check.json").exists() else {}


def kakao_category(c: str, current: str) -> str:
    """카카오 업종('음식점 > 양식 > 이탈리안')을 사이트 업종 이름(eat.pl 업종 체계)으로. 모르면 current."""
    segs = [x.strip() for x in (c or "").split(">")]
    top, sub = (segs[1] if len(segs) > 1 else ""), " ".join(segs[2:])
    if segs[0] == "간식" or top == "간식":
        sub = sub or top
        return "기타베이커리" if re.search("제과|베이커리|빵|도넛", sub) else "아이스크림/요거트/빙수" if re.search("아이스크림|빙수", sub) else "떡/한과" if "떡" in sub else "카페"
    rules = [("카페", "", "카페"), ("술집", "일본식주점", "이자카야"), ("술집", "칵테일|와인|바", "바(bar)"), ("술집", "호프|맥주|요리주점|포장마차", "호프"),
             ("술집", "", "기타주점"), ("한식", "국밥|해장국|설렁탕|곰탕|감자탕|찌개|전골|순대|추어", "찜/탕/찌개"),
             ("한식", "육류|고기|곱창|막창|족발|보쌈|닭", "고기구이"), ("한식", "해물|생선|회|조개|게|복어|장어", "횟집"), ("한식", "국수|냉면|칼국수|만두|수제비", "국수/면/만두"),
             ("한식", "한정식|백반|쌈밥|비빔밥", "백반/한정식"), ("한식", "", "기타한식"), ("일식", "초밥|롤|회", "스시/초밥"),
             ("일식", "돈까스|우동", "일식돈가스"), ("일식", "라멘|라면", "일식라멘"), ("일식", "", "기타일식"), ("중식", "", "기타중식"),
             ("양식", "이탈리안|파스타", "이탈리아음식"), ("양식", "프랑스", "프랑스음식"), ("양식", "피자", "피자"), ("양식", "햄버거", "버거"),
             ("양식", "멕시칸|브라질", "멕시칸"), ("양식", "", "기타외국식"), ("아시아음식", "", "아시안"), ("분식", "", "분식"),
             ("패스트푸드", "", "버거"), ("치킨", "", "치킨"), ("도시락", "", "도시락/죽"), ("샐러드", "", "브런치카페/샐러드"),
             ("뷔페", "", "뷔페"), ("퓨전요리", "", "기타외국식"), ("간식", "", "카페")]
    for t, pat, cat in rules:
        if top == t and (not pat or re.search(pat, sub)):
            return cat
    return current


def kakao_label(c: str) -> str:
    """카드에 보일 업종 이름: '음식점 > 한식 > 설렁탕' → '설렁탕', '음식점 > 카페 > 커피전문점 > 블루보틀' → '카페'."""
    segs = [x.strip() for x in (c or "").split(">")]
    if len(segs) < 2:
        return ""
    if segs[1] in ("카페", "간식") and len(segs) > 2:
        return segs[2].replace(",", "·") if segs[1] == "간식" else "카페"
    return (segs[2] if len(segs) > 2 else segs[1]).replace(",", "·")


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
    if path.exists() and not refresh:  # 캐시가 있으면 쓴다. 새로 받는 건 --refresh / --rotate 일 때만
        cached = json.loads(path.read_text())
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


# 프랜차이즈 본사·운영사 이름 (직영점은 결제 데이터에 '운영사 + 브랜드 + 지점'으로 올라온다). 표시할 땐 떼고, 프랜차이즈로 본다.
# Keep in sync with OPERATORS in src/app.js.
OPERATORS = [("다이닝브랜즈 그룹", ""), ("다이닝브랜즈그룹", ""), ("비케이알", ""), ("한국맥도날드", "맥도날드"), ("롯데지알에스", ""),
             ("신세계푸드", ""), ("씨제이푸드빌", ""), ("이랜드이츠", ""), ("제너시스", ""), ("썬앳푸드", ""), ("갓텐코리아", "")]


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
    name = max(parts[1:], key=len) if len(parts[0]) <= 2 and len(parts) > 1 else parts[0]
    for op, repl in OPERATORS:
        if name.startswith(op) and len(name) > len(op) + 1:
            name = (repl + name[len(op):]).strip() if repl else name[len(op):].strip()
            break
    return name


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


# 프랜차이즈: 이 사이트가 모은 가게 중 '상호 ○○점' 형태가 4곳 이상이거나 잘 알려진 체인 (main 에서 FRANCHISE 를 채운다)
CHAIN_MIN = 4
CHAIN_KNOWN = ("스타벅스", "메가엠지씨", "메가커피", "컴포즈", "빽다방", "이디야", "투썸", "할리스", "커피빈", "폴바셋", "파스쿠찌",
               "공차", "더벤티", "매머드", "바나프레소", "텐퍼센트", "맥도날드", "롯데리아", "버거킹", "케이에프씨", "kfc", "맘스터치",
               "써브웨이", "서브웨이", "파리바게뜨", "뚜레쥬르", "배스킨", "던킨", "크리스피크림", "이삭토스트", "본죽", "교촌",
               "비에이치씨", "bhc", "비비큐", "bbq", "굽네", "도미노", "피자헛", "김밥천국", "한솥", "본도시락", "새마을식당",
               "홍콩반점", "역전할머니", "신전떡볶이", "엽기떡볶이", "죠스떡볶이", "설빙", "아웃백", "빕스", "애슐리",
               "타코벨", "강강술래", "놀부")
FRANCHISE: set = set()


BRANCH_RE = re.compile(r"(점|지점|호점|직영점|본점)$")


def brand_parts(r: dict) -> tuple[str, bool]:
    """(상호, 지점명이 붙어 있었나). '메가엠지씨커피 서울역점' → ('메가엠지씨커피', True)."""
    t = re.sub(r"\(.*?\)", "", clean_name(r.get("merNm"))).split()
    if len(t) >= 2 and BRANCH_RE.search(t[-1]):
        return "".join(t[:-1]).lower(), True
    return "".join(t).lower(), False


def brand_of(r: dict) -> str:
    return brand_parts(r)[0]


def is_chain(r: dict) -> bool:
    raw = (r.get("merNm") or "").replace(" ", "")
    if any(op.replace(" ", "") in raw for op, _ in OPERATORS):  # '다이닝브랜즈그룹 비에이치씨 선릉역점' 같은 본사 직영점
        return True
    b, _ = brand_parts(r)
    if b in FRANCHISE or any(b.startswith(k) or (len(k) >= 3 and k in b) for k in CHAIN_KNOWN):
        return True
    # '투썸플레이스을지트윈타워점' 처럼 붙여 쓴 지점명, '하이디라오코리아 코엑스' 처럼 지점 표시가 없는 경우
    if BRANCH_RE.search(b) and any(len(f) >= 3 and b.startswith(f) for f in FRANCHISE):
        return True
    first = re.sub(r"\(.*?\)", "", clean_name(r.get("merNm"))).split()[:1]
    return bool(first and len(first[0]) >= 3 and first[0].lower() in FRANCHISE)


# 주인장 취향: 주인장 픽 vs 같은 건물의 다른 가게로 학습한 로지스틱 모델 (tools/taste_train.py → data/taste_model.json)
TASTE = json.loads((ROOT / "data" / "taste_model.json").read_text()) if (ROOT / "data" / "taste_model.json").exists() else None
TASTE_TOP = 0.10  # 취향 일치도 상위 10%에 '주인장 취향' 표시


def taste_features(r: dict) -> dict:
    """취향 모델 입력. tools/taste_train.py 도 이 함수를 그대로 쓴다."""
    f = {}
    a = float(r.get("allSaleRnk") or 99)
    c = float(r.get("ccgSaleRnk") or 99)
    f["log_all"] = math.log10(max(a, 0.01))
    f["log_ccg"] = math.log10(max(c, 0.01))
    f["all_minus_ccg"] = f["log_all"] - f["log_ccg"]
    g = GROUP_OF.get(r.get("merTpbuzNm"), "etc")
    for k in [k for k, _, _ in GROUPS] + ["etc"]:
        f["g_" + k] = 1.0 if g == k else 0.0
    f["local"] = 1.0 if r.get("loclPrsnPpltYn") else 0.0
    f["foreign"] = 1.0 if r.get("frnrVsitYn") else 0.0
    f["group_dinner"] = 1.0 if r.get("empyVsitYn") else 0.0
    s, e = r.get("srtTime") or "", r.get("endTime") or ""
    sh = int(s[:2]) + int(s[2:]) / 60 if len(s) == 4 and s.isdigit() else 11.0
    eh = int(e[:2]) + int(e[2:]) / 60 if len(e) == 4 and e.isdigit() else 21.0
    if eh < sh:
        eh += 24
    f["open_h"], f["close_h"], f["dur_h"] = sh, eh, eh - sh
    hol = r.get("hldyDwk") or []
    f["n_holiday"] = float(len(hol))
    f["closed_weekend"] = 1.0 if ("토" in hol or "일" in hol) else 0.0
    f["closed_mon"] = 1.0 if "월" in hol else 0.0
    for k in ("점심", "저녁", "심야", "오전", "오후", "새벽"):
        f["peak_" + k] = 1.0 if r.get("congestionTizn") == k else 0.0
    f["peak_weekend"] = 1.0 if r.get("congestionDwk") in ("토", "일") else 0.0
    cust = r.get("vistCstmrRnkList") or {}
    ages, fem = [], 0
    for k in ("rk01", "rk02", "rk03"):
        v = cust.get(k) or ""
        m = re.match(r"(\d+)대", v)
        if m:
            ages.append(int(m.group(1)))
        fem += 1 if "여성" in v else 0
    f["n_2030"] = float(sum(1 for x in ages if x in (20, 30)))
    f["n_50p"] = float(sum(1 for x in ages if x >= 50))
    f["top1_age"] = float(ages[0] if ages else 40)
    f["n_female"] = float(fem)
    f["chain"] = 1.0 if is_chain(r) else 0.0
    f["premium"] = 1.0 if is_premium(r) else 0.0
    n = clean_name(r.get("merNm"))
    f["name_len"] = float(len(n))
    f["name_latin"] = 1.0 if re.search(r"[A-Za-z]", n) else 0.0
    f["name_oldschool"] = 1.0 if re.search(r"(식당|집|회관|가든|옥|정|관)$", n.replace(" ", "")) else 0.0
    f["name_bonjeom"] = 1.0 if "본점" in (r.get("merNm") or "") else 0.0
    return f


DEST_GATE = 0.35  # 찾아가는 맛집 규칙도 취향 모델 상위 35% 안에 들어야 한다 (호텔 식당·구내식당 등 걸러내기)
DEST_BAD = re.compile(r"호스피탈리티|푸드앤|호텔|리조트|컨벤션|케이터링|푸드서비스|외식산업|에프앤비|F&B|PC|피씨|노래|만화|스터디|"
                      r"_NICE|\(구\)|휴게소|푸드코트|병원|대학교|인문대|학생식당|구내식당|식당\d|사조", re.I)


def is_destination(r: dict) -> bool:
    """찾아가서 먹는 유명한 집: 매출점수 90점 이상 + 동네 주민 위주가 아님 + 프랜차이즈 아님 + 식사 업종.
    모델은 주인장 목록에서 '주말·저녁에 찾아가는 집'을 주로 배워서 '평일 점심 줄 서는 노포'를 놓치기 쉬워 규칙으로 더한다.
    Keep in sync with isDestination() in src/app.js."""
    g = GROUP_OF.get(r.get("merTpbuzNm"), "etc")
    return (r.get("_score", score_of(r)) >= 90 and not r.get("loclPrsnPpltYn") and not is_chain(r)
            and g not in ("drink", "cafe", "quick", "etc") and not DEST_BAD.search(r.get("merNm") or ""))


def taste_prob(r: dict) -> float:
    if not TASTE:
        return 0.0
    f = taste_features(r)
    z = TASTE["intercept"]
    for k, mu, sd, w in zip(TASTE["features"], TASTE["mean"], TASTE["scale"], TASTE["coef"]):
        z += w * ((f.get(k, 0.0) - mu) / (sd or 1.0))
    return 1 / (1 + math.exp(-max(min(z, 30), -30)))


def where_short(addr) -> str:
    """'서울 중구 세종대로11길 26 (서소문동)' → '중구 세종대로11길'"""
    t = short_addr(addr).split()
    t = t[1:] if t and not t[0].startswith("세종") else t
    return " ".join(x for x in t[:2])


def card(r: dict, rank: int) -> str:
    """간단한 카드: 이름·점수 / 업종·위치·영업시간 / 태그 3개 / eat.pl 버튼. 나머지는 '자세히' 안에."""
    cat = r.get("merTpbuzNm") or "기타"
    g = GROUP_OF.get(cat, "etc")
    lunch = open_for_lunch(r.get("srtTime") or "", r.get("endTime") or "") and g not in ("drink", "cafe")
    s = r["_score"]
    premium = is_premium(r) and not r.get("_knonfood")
    chain = is_chain(r)
    busy = is_busy(r)
    talk = (is_talk(r) or (premium and not busy) or bool(r.get("_tag_room"))) and not r.get("_knonfood")
    tags = [(k, t) for k, t, on in (
        ("pick", "주인장 픽", r.get("_pick")),
        ("taste", "주인장 취향", r.get("_taste")),
        ("room", "룸 있음", r.get("_tag_room")),
        ("talk", "대화하기 좋은 곳", talk),
        ("premium", "고급", premium),
        ("", "회식", r.get("empyVsitYn")),
        ("busy", "아주 붐빔", busy),
        ("", "동네 맛집", r.get("loclPrsnPpltYn")),
        ("", "외국인 방문", r.get("frnrVsitYn")),
        ("chain", "프랜차이즈", chain)) if on][:3]
    cust = r.get("vistCstmrRnkList") or {}
    cust_txt = ", ".join(v for v in (cust.get("rk01"), cust.get("rk02"), cust.get("rk03")) if v)
    peak = " ".join(x for x in (f"{r.get('congestionDwk')}요일" if r.get("congestionDwk") else "", r.get("congestionTizn") or "") if x)
    flags = " ".join(f for f, on in (("lunch", lunch), ("group", r.get("empyVsitYn")), ("local", r.get("loclPrsnPpltYn")),
                                    ("foreign", r.get("frnrVsitYn")), ("pick", r.get("_pick")), ("busy", busy), ("talk", talk), ("premium", premium),
                                    ("taste", r.get("_taste")), ("mine", r.get("_pick") or r.get("_taste")),
                                    ("hidden", r.get("_taste") and s < 70)) if on)
    gs = g + ("" if g in ("drink", "cafe") else " meal")
    hrs = hours(r.get("srtTime") or "", r.get("endTime") or "")
    more = [f'<p class="nums">전국 같은 업종 매출 상위 {pct(r.get("allSaleRnk"))}% · 구 안에서 상위 {pct(r.get("ccgSaleRnk"))}%</p>',
            f'<p>{esc(holidays(r.get("hldyDwk")))}{(" · 붐비는 때 " + esc(peak)) if peak else ""}</p>']
    if cust_txt:
        more.append(f"<p>주 고객 {esc(cust_txt)}</p>")
    if busy:
        room = "" if r.get("_tag_room") else (BUSY_ROOM_GROUP if r.get("empyVsitYn") else BUSY_ROOM)
        more.append(f'<p class="note busy">{BUSY_NOTE}{(" · " + room) if room else ""}</p>')
    if premium and s < 75:
        rk = r.get("_prem_rank")
        more.append('<p class="note premium">가격대가 높은 소규모 업장으로 보여요. 손님 수가 적어 매출점수가 낮게 나오는 유형이에요'
                    + (f' (고급 추정 {rk[1]}곳 중 매출 {rk[0]}위)' if rk else '') + '</p>')
    more.append(f"<p>{esc(short_addr(r.get('merAddr')))}</p>")
    kq = urllib.parse.quote(f"{r.get('_name') or clean_name(r.get('merNm'))} {where_short(r.get('merAddr'))}")
    more.append(f'<p><a class="kakao" href="https://map.kakao.com/link/search/{kq}" target="_blank" rel="noopener">카카오맵에서 보기 (사진·리뷰)</a></p>')
    sub = " · ".join(x for x in (f'<span class="cat">{esc(r.get("_catlabel") or cat)}</span>', esc(where_short(r.get("merAddr"))), esc(hrs)) if x)
    tag_html = "".join(f'<span class="t {k}">{esc(t)}</span>' for k, t in tags)
    return f"""<li class="card" id="p-{esc(mer_id(r.get('merUrl')))}" data-f="{esc(flags)}" data-g="{gs}" data-a="{age_tags(r)}" data-s="{sex_tag(r)}" data-k="{'chain' if chain else 'indie'}" data-c="{esc(city_of(r.get('merAddr')))}">
<div class="card-head"><span class="rank">{rank}</span><span class="ico" aria-hidden="true">{icon_of(cat)}</span><h3>{esc(r.get('_name') or clean_name(r.get('merNm')))}</h3><span class="score" title="매출점수"><b>{s}</b><small>{grade(s)}</small></span></div>
<p class="sub">{sub}</p>
{f'<p class="tags">{tag_html}</p>' if tag_html else ''}
<div class="actions"><a class="go" href="{esc(r.get('merUrl'))}" target="_blank" rel="noopener">eat.pl에서 메뉴 보기</a>{'<button type="button" class="map-btn" aria-label="지도에서 보기">지도</button>' if latlng(r) else ''}<button type="button" class="share" aria-label="공유">공유</button></div>
<details class="more"><summary>자세히</summary>{''.join(more)}</details>
</li>"""


def group_chips(present: set, default: str = "meal") -> str:
    chips = [("meal", "식사"), ("", "전체")] + [(k, n) for k, n, _ in GROUPS if k in present]
    on = ' class="on"'
    return "".join(f'<button type="button" data-v="{k}"{on if k == default else ""}>{n}</button>' for k, n in chips)


ASSET_V = ""  # main() 에서 style.css·app.js·map.js 내용으로 정한다 (휴대폰 캐시에 옛 화면이 남지 않게)


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
<link rel="stylesheet" href="{up}assets/style.css?v={ASSET_V}">
</head><body data-root="{up}"{f' data-api="{esc(LOOKUP_API)}"' if LOOKUP_API else ''}>
<div id="inapp" class="inapp" hidden></div>
<header class="top"><a class="brand" href="{up}index.html">{SITE_NAME}</a><span class="tagline">{TAGLINE}</span>
<nav class="topnav"><a href="{up}map.html">지도</a><a href="{up}areas.html">동네별</a></nav></header>
<main>{body}</main>
<footer>
<p>데이터: <strong>eat.pl 잇플 · BC카드 결제 데이터 기반</strong>. 매출점수는 최근 1개월 같은 업종 매출 순위를 0~100점으로 바꾼 값으로, 맛 평가가 아니에요. 영업시간은 결제 시각으로 추정한 값이에요.</p>
<p>이 사이트는 BC카드나 eat.pl의 공식 서비스가 아닙니다.</p>
</footer>
<script src="{up}assets/app.js?v={ASSET_V}"></script>
</body></html>"""


AGE_OPTS = [("", "모든 연령"), ("young", "20~30대 위주"), ("20", "20대"), ("30", "30대"), ("40", "40대"), ("50", "50대 이상")]
SEX_OPTS = [("", "모든 성별"), ("f", "여성 위주"), ("m", "남성 위주"), ("mix", "남녀 고루")]


def select_box(key: str, label: str, opts: list, default: str = "", el_id: str = "") -> str:
    sel = ' selected'
    o = "".join(f'<option value="{esc(k)}"{sel if k == default else ""}>{esc(v)}</option>' for k, v in opts)
    return f'<select{f" id=\"{el_id}\"" if el_id else ""} data-key="{key}" aria-label="{esc(label)}">{o}</select>'


def list_section(cards: str, present: set, cities: list | None = None, purpose: bool = True, default: str = "meal",
                 chain_default: str = "indie", extra: list | None = None) -> str:
    """위 줄: 함께 켤 수 있는 버튼(주인장 취향·대화하기 좋은 곳…). 아래 줄: 하나만 고르는 드롭다운(종류·연령·성별·지역·프랜차이즈)."""
    toggles = ([("mine", "🍽️ 주인장 취향"), ("talk", "🤫 대화하기 좋은 곳"), ("premium", "🍷 고급"), ("group", "👥 회식"),
                ("lunch", "점심 영업"), ("local", "동네 맛집"), ("foreign", "외국인 많이 가는 곳")] if purpose else [])
    toggles += list(extra or [])
    tog = ""
    if toggles:
        tog = ('<nav class="filters toggles" data-key="f" aria-label="함께 고를 수 있는 조건"><button type="button" data-v="" class="on">전체</button>'
               + "".join(f'<button type="button" data-v="{k}">{esc(v)}</button>' for k, v in toggles) + "</nav>")
    kinds = [("meal", "식사"), ("", "모든 종류")] + [(k, n) for k, n, _ in GROUPS if k in present]
    sels = [select_box("g", "종류", kinds, default), select_box("a", "손님 연령", AGE_OPTS), select_box("s", "손님 성별", SEX_OPTS)]
    if cities and len(cities) > 1:
        sels.append(select_box("c", "지역", [("", "모든 지역")] + [(c, c) for c in cities]))
    sels.append(select_box("k", "프랜차이즈", [("indie", "프랜차이즈 빼고"), ("", "프랜차이즈 포함")], chain_default))
    return f"""<div class="sticky">{tog}<div class="selects">{''.join(sels)}</div></div>
<p class="count" id="count"></p>
<ol class="list" id="list">
{cards}
</ol>"""


# ------------------------------------------------------------------ build
def main():
    refresh = "--refresh" in sys.argv
    week = datetime.now(KST).isocalendar()[1]
    rotate = "--rotate" in sys.argv
    areas = json.loads((ROOT / "areas.json").read_text())
    picks = json.loads(PICKS.read_text()) if PICKS.exists() else {"picks": [], "items": []}
    pick_name = {p["merUrl"]: p["name"] for p in picks.get("picks", [])}
    tag_file = ROOT / "data" / "tags.json"
    tdata = json.loads(tag_file.read_text()) if tag_file.exists() else {}
    tags = {k: set(tdata.get(k) or []) for k in ("room", "premium", "exclude")}  # merUrl 또는 가게 이름

    OUT.mkdir(exist_ok=True)
    for d in ("area", "cat", "assets"):
        (OUT / d).mkdir(exist_ok=True)
    global ASSET_V
    for f in ("style.css", "app.js", "map.js"):
        (OUT / "assets" / f).write_text((SRC / f).read_text())
    ASSET_V = hashlib.md5("".join((SRC / f).read_text() for f in ("style.css", "app.js", "map.js")).encode()).hexdigest()[:8]
    if (SRC / "og.png").exists():
        (OUT / "assets" / "og.png").write_bytes((SRC / "og.png").read_bytes())

    places = {}  # merUrl → item (every place we know: area pages + picks lookups)
    review = []  # (가게, 이상치 이유, 뺐는지) — 공개하지 않는 검토 목록 (REVIEW_OUT 이 있을 때만 파일로)

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
        if NOT_RESTAURANT.search(r.get("merNm") or "") or url in tags["exclude"] or (nm & tags["exclude"]):
            return None
        reasons = anomaly_reasons(r)
        if reasons:
            review.append((r, reasons, anomaly_exclude(reasons) and url not in pick_name))
            if review[-1][2]:
                return None
        k = KCHECK.get(mer_id(url))
        if k and "none" not in k:
            food = k.get("g") in ("FD6", "CE7") or (k.get("c") or "").startswith("음식점")
            if food:
                kc = kakao_category(k.get("c"), r.get("merTpbuzNm"))
                if GROUP_OF.get(kc, "etc") != GROUP_OF.get(r.get("merTpbuzNm"), "etc"):
                    r["_eatpl_cat"] = r.get("merTpbuzNm")
                    r["merTpbuzNm"] = kc  # 업종 그룹이 다르면 카카오를 따른다 (예: 기타외국식 → 카페)
                r["_catlabel"] = kakao_label(k.get("c"))
            else:
                r["_knonfood"] = True  # 카카오엔 회사·판매점 등으로만 나온다 → 추천 태그는 붙이지 않는다
        try:
            low = float(r.get("allSaleRnk")) > LOW_CUT
        except (TypeError, ValueError):
            low = True
        if low and url not in pick_name and not is_premium(r):
            return None
        r["_score"] = score_of(r)
        r["_pick"] = url in pick_name
        r["_name"] = pick_name.get(url) or clean_name(r.get("merNm"))
        r["_area"] = ""
        places[url] = r
        return r

    index_cards, latest, built_areas, area_rows = {}, "", [], []
    for a in areas:
        raw, fetched = area_items(a, refresh or (rotate and areas.index(a) % ROTATE == week % ROTATE))
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
    for r in places.values():  # '○○ ○○점' 처럼 지점명이 붙은 상호만 센다 (흔한 이름 '고향집' 등은 제외)
        b, branch = brand_parts(r)
        if branch:
            brand_count[b] = brand_count.get(b, 0) + 1
    FRANCHISE.update(b for b, n in brand_count.items() if n >= CHAIN_MIN and len(b) >= 2)
    prem = sorted((r for r in places.values() if is_premium(r) and not r.get("_knonfood")), key=lambda r: float(r.get("allSaleRnk") or 999))
    for i, r in enumerate(prem):
        r["_prem_rank"] = (i + 1, len(prem))
    # 주인장 취향: 픽이 아닌 가게 중 취향 일치도 상위 TASTE_TOP
    # 법인명으로만 등록된 가게('(주)○○푸드', '○○몰 주식회사')는 실제 간판을 알 수 없어 취향 추천에서 뺀다
    def corp_only(r):
        raw = r.get("merNm") or ""
        return bool(re.search(CORP, raw)) and "/" not in raw and not re.search(r"\(([^)]{2,})\)\s*$", raw.split("/")[0])
    others = sorted((r for r in places.values() if not r["_pick"] and not corp_only(r) and not r.get("_knonfood")),
                    key=lambda r: -taste_prob(r)) if TASTE else []
    taste_thr = 1.0
    gate_p = taste_prob(others[min(len(others) - 1, int(len(others) * DEST_GATE))]) if others else 1.0
    for i, r in enumerate(others):
        top = (i + 1) / len(others)
        r["_taste_p"] = taste_prob(r)
        r["_taste"] = top <= TASTE_TOP or (is_destination(r) and r["_taste_p"] >= gate_p)
        r["_taste_top"] = max(1, math.ceil(top * 100))
        if top <= TASTE_TOP:
            taste_thr = r["_taste_p"]

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
  <p class="crumb"><a href="../areas.html">동네별</a> › {esc(a['city'])}</p>
  <h1>{esc(a['name'])} 맛집</h1>
  <p class="sub">카드 매출 상위 {len(items)}곳 · {esc(updated)} 기준</p>
  <button type="button" class="share-page">공유하기</button>
</section>
<section class="picker" id="picker">
  <h2>오늘 점심 뭐 먹지?</h2>
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
  <p class="crumb"><a href="../areas.html">동네·종류별</a> › {esc(name)}</p>
  <h1>{esc(name)} 맛집 TOP {len(rows)}</h1>
  <p class="sub">전국 {len(places):,}곳 중 매출점수 순</p>
  <button type="button" class="share-page">공유하기</button>
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
  <h1>주인장 픽 {len(prows)}곳</h1>
  <p class="sub">만든 사람이 오래 모아 온 맛집 · 평균 {avg}점</p>
  <button type="button" class="share-page">공유하기</button>
</section>
{list_section(cards, {GROUP_OF.get(r.get('merTpbuzNm'), 'etc') for r in prows}, cities, default="", chain_default="")}"""
        (OUT / "picks.html").write_text(page(f"주인장 픽 — 직접 모은 맛집 {len(prows)}곳의 매출점수 | {SITE_NAME}",
                                             "사이트 주인이 지도에 모아 온 전국 맛집을 BC카드 결제 매출점수로 다시 봤습니다.",
                                             body, "picks.html", 0))

    # ---- 대화하기 좋은 곳
    trows = [r for r in ranked if (is_talk(r) or (is_premium(r) and not is_busy(r)) or r["_tag_room"]) and not r.get("_knonfood")]
    if trows:
        cities = sorted({city_of(r.get("merAddr")) for r in trows}, key=lambda c: -sum(1 for r in trows if city_of(r.get("merAddr")) == c))
        cards = "\n".join(card(r, i + 1) for i, r in enumerate(trows))
        body = f"""
<section class="hero small">
  <p class="crumb"><a href="index.html">홈</a> › 대화하기 좋은 곳</p>
  <h1>대화하기 좋은 곳</h1>
  <p class="sub">소개팅·데이트·오랜만의 모임에</p>
  <details class="about"><summary>어떻게 골랐나요?</summary><p>양식·일식·바 같은 업종 중 단체 회식 결제가 없고, 손님이 몰리는 최상위 집과 체인·쇼핑몰 매장은 빼고, 저녁까지 영업하며 20~30대 손님이 많은 곳이에요. 고급 업장과 룸이 있는 집도 넣었어요. 실제 소음을 잰 건 아니에요.</p></details>
  <button type="button" class="share-page">공유하기</button>
</section>
{list_section(cards, {GROUP_OF.get(r.get('merTpbuzNm'), 'etc') for r in trows}, cities, purpose=False, default="")}"""
        (OUT / "talk.html").write_text(page(f"조용히 대화하기 좋은 맛집 {len(trows)}곳 — 소개팅·데이트 장소 | {SITE_NAME}",
                                            "단체 회식이 없고 너무 붐비지 않는, 저녁에 조용히 대화하기 좋은 양식·일식·바. BC카드 결제 데이터로 골랐습니다.",
                                            body, "talk.html", 0))

    # ---- 주인장 취향
    tsrows = sorted((r for r in places.values() if r.get("_taste")), key=lambda r: -r["_taste_p"])
    if tsrows:
        cities = sorted({city_of(r.get("merAddr")) for r in tsrows}, key=lambda c: -sum(1 for r in tsrows if city_of(r.get("merAddr")) == c))
        cards = "\n".join(card(r, i + 1) for i, r in enumerate(tsrows))
        n_hidden = sum(1 for r in tsrows if r["_score"] < 70)
        body = f"""
<section class="hero small">
  <p class="crumb"><a href="index.html">홈</a> › 주인장 취향</p>
  <h1>주인장 취향 추천</h1>
  <p class="sub">주인장 맛집과 닮은 단독 식당 · 숨은 집 {n_hidden}곳 포함</p>
  <details class="about"><summary>어떻게 골랐나요?</summary><p>주인장 맛집 {len(prows)}곳과 같은 건물의 다른 가게를 비교해 고르는 기준을 배웠어요. 프랜차이즈가 아니고, 동네 주민보다 찾아오는 손님이 많고, 주말에도 열고, 저녁에 붐비는 집일수록 높아요. 매출점수와는 따로 계산해서 70점 미만 숨은 집도 나와요. 한 사람의 취향이라 참고로만 봐 주세요.</p></details>
  <button type="button" class="share-page">공유하기</button>
</section>
{list_section(cards, {GROUP_OF.get(r.get('merTpbuzNm'), 'etc') for r in tsrows}, cities, default="", extra=[("hidden", "70점 미만 숨은 집")])}"""
        (OUT / "taste.html").write_text(page(f"주인장 취향 추천 맛집 {len(tsrows):,}곳 — 점수 낮은 숨은 집까지 | {SITE_NAME}",
                                             "주인장이 모아 온 맛집의 공통점을 배워 고른 단독 식당 추천. 매출점수는 낮아도 취향에 맞는 숨은 집까지.",
                                             body, "taste.html", 0))

    # ---- 고급 추정
    if prem:
        cities = sorted({city_of(r.get("merAddr")) for r in prem}, key=lambda c: -sum(1 for r in prem if city_of(r.get("merAddr")) == c))
        cards = "\n".join(card(r, i + 1) for i, r in enumerate(prem))
        body = f"""
<section class="hero small">
  <p class="crumb"><a href="index.html">홈</a> › 고급·가격대 있는 집</p>
  <h1>고급 맛집</h1>
  <p class="sub">오마카세·파인다이닝·비스트로·한우</p>
  <details class="about"><summary>어떻게 골랐나요?</summary><p>결제 데이터엔 가격이 없어서 가게 이름·업종·영업 시간으로 추정했어요. 손님 수가 적어 매출점수가 낮게 나오기 쉬워서, 여기서는 고급 업장끼리 매출 순으로 줄 세웠어요. 체인과 쇼핑몰 매장은 뺐어요.</p></details>
  <button type="button" class="share-page">공유하기</button>
</section>
{list_section(cards, {GROUP_OF.get(r.get('merTpbuzNm'), 'etc') for r in prem}, cities, purpose=False, default="")}"""
        (OUT / "premium.html").write_text(page(f"오마카세·파인다이닝 등 고급 맛집 {len(prem)}곳 매출 순위 | {SITE_NAME}",
                                               "오마카세, 파인다이닝, 비스트로, 한우·스테이크하우스 등 가격대 있는 집을 BC카드 결제 매출로 줄 세웠습니다.",
                                               body, "premium.html", 0))

    # ---- 지도
    body = ("""
<section class="hero small map-hero">
  <h1>내 주변 맛집</h1>
</section>
<div class="map-tools">
  <button type="button" id="locate" class="locate">◎ 내 위치</button>
  <button type="button" id="here" class="here" hidden>이 지역 실시간으로 찾기</button>
  <span id="map-status" class="map-status" aria-live="polite"></span>
</div>
<nav class="filters toggles" id="map-toggles" aria-label="함께 고를 수 있는 조건">
  <button type="button" data-v="" class="on">전체</button><button type="button" data-v="mine">🍽️ 주인장 취향</button>
  <button type="button" data-v="talk">🤫 대화하기 좋은 곳</button><button type="button" data-v="premium">🍷 고급</button>
  <button type="button" data-v="group">👥 회식</button>
</nav>
<div class="selects">""" + select_box("g", "종류", [("meal", "식사"), ("", "모든 종류")] + [(k, n) for k, n, _ in GROUPS], "meal", "map-kind")
    + select_box("a", "손님 연령", AGE_OPTS, "", "map-age") + select_box("s", "손님 성별", SEX_OPTS, "", "map-sex")
    + select_box("k", "프랜차이즈", [("indie", "프랜차이즈 빼고"), ("", "프랜차이즈 포함")], "indie", "map-chain") + """</div>
<div id="map" class="map" role="region" aria-label="맛집 지도"></div>
<div class="map-listhead"><p class="count" id="map-count"></p>
<nav class="filters sortnav" id="map-sort" aria-label="정렬" hidden><button type="button" data-v="score" class="on">점수순</button><button type="button" data-v="dist">가까운 순</button></nav></div>
<ol class="list" id="map-list"></ol>
<p class="small-note sub">지도 © OpenStreetMap 기여자. 가게 위치는 결제 데이터의 좌표라 실제 입구와 수십 m 다를 수 있어요.</p>
""")
    html_map = page(f"내 주변 맛집 지도 — 매출점수로 보는 동네 맛집 | {SITE_NAME}",
                    "지도에서 화면 안 매출점수 높은 집을 보고, 내 위치 주변 맛집을 가까운 순으로. BC카드 결제 데이터 기반.",
                    body, "map.html", 0)
    css_tag, js_tag = f'<link rel="stylesheet" href="assets/style.css?v={ASSET_V}">', f'<script src="assets/app.js?v={ASSET_V}"></script>'
    html_map = html_map.replace(css_tag, '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">\n' + css_tag)
    html_map = html_map.replace(js_tag, js_tag + '\n<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>\n'
                                f'<script src="assets/map.js?v={ASSET_V}"></script>')
    (OUT / "map.html").write_text(html_map)

    # ---- search index (loaded lazily by the home page search box)
    # 압축 형식 (v2): [이름, 업종번호, 주소, 점수, 전국%, 구%, eat.pl id, 동네번호(-1), 플래그, 위도, 경도]
    # 플래그 비트: 1 주인장 픽, 2 아주 붐빔, 4 대화하기 좋은 곳, 8 고급 추정, 16 회식 적합, 32 프랜차이즈, 64 주인장 취향,
    # 128 20~30대 위주, 256/512/1024/2048 주 고객 20·30·40·50대+, 4096 여성 위주, 8192 남성 위주, 16384 남녀 고루. app.js load()가 풀어 쓴다.
    label = lambda r: r.get("_catlabel") or r.get("merTpbuzNm") or ""
    cat_group = {}
    for r in ranked:
        cat_group.setdefault(label(r), GROUP_OF.get(r.get("merTpbuzNm"), "etc"))
    cats = sorted(cat_group)
    cat_no = {c: i for i, c in enumerate(cats)}
    slugs = [a["slug"] for a in built_areas]
    area_no = {sl: i for i, sl in enumerate(slugs)}

    def flags(r):
        nf = r.get("_knonfood")
        talk = (is_talk(r) or (is_premium(r) and not is_busy(r)) or r["_tag_room"]) and not nf
        return ((1 if r["_pick"] else 0) | (2 if is_busy(r) else 0) | (4 if talk else 0) | (8 if is_premium(r) and not nf else 0)
                | (16 if r.get("empyVsitYn") else 0) | (32 if is_chain(r) else 0) | (64 if r.get("_taste") else 0)
                | sum(bit for tag, bit in (("young", 128), ("20", 256), ("30", 512), ("40", 1024), ("50", 2048))
                      if tag in age_tags(r).split())
                | {"f": 4096, "m": 8192, "mix": 16384}.get(sex_tag(r), 0))

    def pos(r):
        ll = latlng(r)
        return [round(ll[0], 4), round(ll[1], 4)] if ll else [0, 0]
    index = [[r["_name"], cat_no[label(r)], short_addr(r.get("merAddr")), r["_score"], r.get("allSaleRnk"),
              r.get("ccgSaleRnk"), r.get("merUrl").replace(MER_PREFIX, "", 1), area_no.get(r["_area"], -1), flags(r), *pos(r)]
             for r in ranked]
    (OUT / "assets" / "places.json").write_text(json.dumps(
        {"v": 2, "cats": cats, "catGroup": [cat_group[c] for c in cats],
         "areas": [[sl, area_name[sl]] for sl in slugs], "groups": GROUP_NAME, "places": index,
         "chains": sorted(FRANCHISE), "chainKnown": list(CHAIN_KNOWN),
         "taste": ({k: TASTE[k] for k in ("features", "mean", "scale", "coef", "intercept")} | {"thr": taste_thr, "gate": gate_p}) if TASTE else None},
        ensure_ascii=False, separators=(",", ":")))

    # ---- home
    groups = []
    for city, rows in index_cards.items():
        tiles = "".join(
            f'<a class="tile" href="area/{esc(a["slug"])}.html" data-name="{esc(a["name"])} {esc(city)}">'
            f'<strong>{esc(a["name"])}</strong><span>{n}곳 · {esc(top3)}</span></a>'
            for a, n, top3 in rows)
        groups.append(f'<details class="city"><summary><h2>{esc(city)} <span>{len(rows)}곳</span></h2></summary>'
                      f'<div class="tiles">{tiles}</div></details>')
    cats = "".join(f'<a class="chip" href="cat/{k}.html">{esc(n)}</a>' for k, n, _ in cat_links)
    body = f"""
<section class="hero home" id="search">
  <h1>그 집, <span>몇 점</span>일까?</h1>
  <p class="sub">카드 결제 매출로 매긴 맛집 점수</p>
  <input id="q" type="search" placeholder="가게 이름 검색" aria-label="가게 이름 검색" autocomplete="off">
  <div id="results" class="results" aria-live="polite"></div>
  <div class="home-extra">
    <a class="near-big" href="map.html#near"><span aria-hidden="true">📍</span><b>내 주변 맛집</b><small>지금 위치 근처 매출 상위 집을 지도로</small></a>
    <a class="home-more" href="areas.html">동네별 · 종류별 순위 보기 →</a>
  </div>
</section>"""
    areas_body = f"""
<section class="hero small">
  <h1>동네별 · 종류별</h1>
  <p class="sub">{len(built_areas)}개 동네 · {len(places):,}곳</p>
</section>
<nav class="quick" aria-label="테마">
  <a href="taste.html"><span aria-hidden="true">🍽️</span>주인장 취향</a>
  <a href="talk.html"><span aria-hidden="true">🤫</span>조용한 곳</a>
  <a href="premium.html"><span aria-hidden="true">🍷</span>고급</a>
  <a href="picks.html"><span aria-hidden="true">⭐</span>주인장 픽</a>
</nav>
<nav class="filters cats" aria-label="종류별 순위">{cats}</nav>
<div id="areas"><h2 class="section-title">동네별 <small>{len(built_areas)}곳</small></h2>
{''.join(groups)}</div>
<details class="how">
  <summary>점수는 이렇게 읽어요</summary>
  <ul>
    <li><strong>매출점수</strong> — 전국 같은 업종 가게 중 매출 순위(75%)와 같은 구 안 순위(25%)를 0~100점으로 바꾼 값입니다. 상위 0.1%면 96점, 1%면 89점, 10%면 77점, 40%면 61점 정도예요.</li>
    <li><strong>전국구 90+ · 찐맛집 80+ · 동네 강자 70+ · 꾸준한 집 60+</strong> — 맛 평가가 아니라 판매량 기준이라, 좌석이 적은 집이나 예약제 식당은 낮게 나올 수 있어요.</li>
    <li><strong>손님 연령 필터</strong> — 결제 고객 중 많은 연령·성별 1·2위를 기준으로 걸러요. "20~30대가 많이 가는 곳"은 1·2위가 모두 20~30대인 곳(전체의 약 38%)이에요. 성별도 같은 방식으로, 1·2위가 모두 여성이면 "여성 손님 위주"(약 20%), 모두 남성이면 "남성 손님 위주"(약 44%), 섞여 있으면 "남녀 고루"예요.</li>
    <li><strong>회식 적합</strong> — 저녁 시간대 단체 결제가 있었던 곳. <strong>동네 맛집</strong> — 동네 손님 비율이 구 평균보다 높은 곳.</li>
    <li><strong>아주 붐비는 집</strong> — 전국 같은 업종 매출 상위 0.5% 안(회식 적합이면 1% 안)인 곳이에요. 인기는 확실하지만 조용히 대화할 자리라면 붐비는 시간을 피하는 게 좋아요.</li>
    <li><strong>대화하기 좋은 곳</strong> — 양식·일식·바 같은 업종 중 단체 회식 결제가 없고, 아주 붐비는 집이 아니며, 저녁까지 영업하고 20~30대 손님이 많은 곳이에요. 고급 업장과 룸이 있는 집도 포함해요. <a href="talk.html">모아 보기</a></li>
    <li><strong>주인장 취향</strong> — 주인장 맛집과 같은 건물의 다른 가게를 비교해 배운 기준(프랜차이즈 아님, 찾아오는 손님, 주말 영업, 저녁 손님 등)에 잘 맞는 상위 10%예요. 매출점수가 낮아도 표시돼요. <a href="taste.html">모아 보기</a></li>
    <li><strong>고급 추정</strong> — 오마카세·파인다이닝·비스트로·한우 등 가격대가 있어 보이는 집이에요. 손님 수가 적어 매출점수가 낮게 나오기 쉬워서 고급 업장끼리 순위를 따로 보여 드려요. <a href="premium.html">모아 보기</a></li>
  </ul>
</details>"""
    (OUT / "areas.html").write_text(page(f"동네별·종류별 맛집 매출 순위 — {len(built_areas)}개 동네 | {SITE_NAME}",
                                         f"을지로·강남·여의도·판교·성수 등 {len(built_areas)}개 동네와 한식·고기·일식 등 종류별 매출점수 순위.",
                                         areas_body, "areas.html", 0))
    title = f"{SITE_NAME} — 우리 동네 맛집 매출점수 검색, 점심·회식 맛집 순위"
    desc = f"별점 말고 실제 결제 매출로 매긴 맛집 점수. {len(places):,}곳 검색, 한식·고기·일식 등 종류별 순위, 을지로·강남·여의도·판교 등 {len(built_areas)}개 동네 점심·회식 맛집."
    (OUT / "index.html").write_text(page(title, desc, body, "index.html", 0))

    if BASE_URL:
        urls = ([f"{BASE_URL}/index.html", f"{BASE_URL}/picks.html", f"{BASE_URL}/talk.html", f"{BASE_URL}/premium.html", f"{BASE_URL}/map.html", f"{BASE_URL}/taste.html", f"{BASE_URL}/areas.html"]
                + [f"{BASE_URL}/area/{a['slug']}.html" for a in built_areas]
                + [f"{BASE_URL}/cat/{k}.html" for k, _, _ in cat_links])
        sm = "".join(f"<url><loc>{esc(u)}</loc><lastmod>{latest[:10]}</lastmod></url>" for u in urls)
        (OUT / "sitemap.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sm}</urlset>')
        (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n")
    else:
        (OUT / "robots.txt").write_text("User-agent: *\nAllow: /\n")
    moved = [r for r in places.values() if r.get("_eatpl_cat")]
    print(f"kakao check: {len(KCHECK)} checked, {len(moved)} recategorized, "
          f"{sum(1 for r in places.values() if r.get('_knonfood'))} not restaurants on Kakao (no recommendation tags)")
    review_out = os.environ.get("REVIEW_OUT")
    if review_out:  # 사이트(site/)·저장소에는 넣지 않는다. 판정이 아니라 사람이 보고 판단할 목록
        seen, lines = set(), []
        rows = sorted(review, key=lambda x: (not x[2], -len(x[1]), float(x[0].get("allSaleRnk") or 99)))
        for r, reasons, dropped in rows:
            if r["merUrl"] in seen or (len(reasons) < 2 and not any(x.startswith(("카카오", "이름")) for x in reasons)):
                continue
            seen.add(r["merUrl"])
            lines.append(f"| {'뺌' if dropped else '검토'} | {clean_name(r.get('merNm'))} | {r.get('merTpbuzNm')} | {score_of(r)} | "
                         f"{short_addr(r.get('merAddr'))} | {' · '.join(reasons)} | [eat.pl]({r.get('merUrl')}) |")
        pathlib.Path(review_out).write_text(
            "# 데이터 이상치 검토 목록 (비공개)\n\n결제 데이터가 보통 식당과 다르게 보이는 곳입니다. 부정 거래 판정이 아니라, "
            "식당 목록에 넣을지 사람이 보고 정하기 위한 목록입니다.\n'뺌'은 사이트에서 자동으로 뺀 곳, '검토'는 그대로 둔 곳입니다. "
            "빼고 싶은 곳은 data/tags.json 의 exclude 에 이름을 넣으면 됩니다.\n\n"
            "| 처리 | 가게 | 업종(eat.pl) | 점수 | 주소 | 이유 | 링크 |\n|---|---|---|---|---|---|---|\n" + "\n".join(lines) + "\n")
        print(f"review list: {len(lines)} rows ({sum(1 for x in review if x[2])} dropped) → {review_out}")
    print(f"built {len(built_areas)} area pages, {len(cat_links)} category pages, {len(prows)} picks, {len(places)} places into {OUT}")


if __name__ == "__main__":
    random.seed(0)
    main()
