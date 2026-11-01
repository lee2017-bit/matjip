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
    ("korean", "한식", ("찜/탕/찌개", "백반/한정식", "기타한식", "국수/면/만두", "전/부침개", "도시락/죽", "뷔페", "반찬가게")),
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
GROUP_NAME = {key: name for key, name, _ in GROUPS}
MER_PREFIX = "https://web.paybooc.ai/mer/web/profile/"  # shortened in places.json, restored by app.js
SKIP_CATS = {"영화관", "기타식품소매업", "주류판매점", "유흥주점", "매점"}  # not restaurants

# 매출점수: percentile (lower = sells more) → 0..100, linear in log10(p) between anchors.
# Keep in sync with SCORE_ANCHORS in src/app.js and worker/lookup.js.
SCORE_ANCHORS = [(0.01, 99), (0.1, 95), (0.5, 90), (1, 86), (3, 79), (5, 75), (10, 68),
                 (20, 58), (40, 45), (70, 30), (100, 15)]


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


def card(r: dict, rank: int, show_area: str = "") -> str:
    cat = r.get("merTpbuzNm") or "기타"
    g = GROUP_OF.get(cat, "etc")
    lunch = open_for_lunch(r.get("srtTime") or "", r.get("endTime") or "") and g not in ("drink", "cafe")
    s = r["_score"]
    tags = []
    if r.get("_pick"):
        tags.append('<span class="tag pick">주인장 픽</span>')
    for on, t in ((r.get("empyVsitYn"), "회식 적합"), (r.get("loclPrsnPpltYn"), "동네 맛집"), (r.get("frnrVsitYn"), "외국인 방문")):
        if on:
            tags.append(f'<span class="tag">{t}</span>')
    cust = r.get("vistCstmrRnkList") or {}
    cust_txt = ", ".join(v for v in (cust.get("rk01"), cust.get("rk02"), cust.get("rk03")) if v)
    busy = " ".join(x for x in (f"{r.get('congestionDwk')}요일" if r.get("congestionDwk") else "", r.get("congestionTizn") or "") if x)
    flags = " ".join(f for f, on in (("lunch", lunch), ("group", r.get("empyVsitYn")), ("local", r.get("loclPrsnPpltYn")),
                                    ("foreign", r.get("frnrVsitYn")), ("pick", r.get("_pick"))) if on)
    gs = g + ("" if g in ("drink", "cafe") else " meal")
    hrs = hours(r.get("srtTime") or "", r.get("endTime") or "")
    where = f'<p class="meta where">{esc(show_area)}</p>' if show_area else ""
    return f"""<li class="card" data-f="{esc(flags)}" data-g="{gs}" data-c="{esc(city_of(r.get('merAddr')))}">
  <div class="card-head"><span class="rank">{rank}</span><h3>{esc(r.get('_name') or clean_name(r.get('merNm')))}</h3><span class="cat">{esc(cat)}</span>
  <span class="score" title="매출점수"><b>{s}</b>점<small>{grade(s)}</small></span></div>
  {where}<p class="nums"><strong>전국 같은 업종 매출 상위 {pct(r.get('allSaleRnk'))}%</strong> · 구 안에서 상위 {pct(r.get('ccgSaleRnk'))}%</p>
  <p class="meta">{esc(short_addr(r.get('merAddr')))}</p>
  <p class="meta">{'영업 예상 ' + esc(hrs) + ' · ' if hrs else ''}{esc(holidays(r.get('hldyDwk')))}{(' · 붐비는 때 ' + esc(busy)) if busy else ''}</p>
  {f'<p class="meta">주 고객 {esc(cust_txt)}</p>' if cust_txt else ''}
  {''.join(tags)}
  <a class="go" href="{esc(r.get('merUrl'))}" target="_blank" rel="noopener">eat.pl에서 자세히 보기 →</a>
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
<nav class="topnav"><a href="{up}index.html#search">맛집 점수 검색</a><a href="{up}picks.html">주인장 픽</a></nav></header>
<main>{body}</main>
<footer>
<p>데이터: <strong>eat.pl 잇플 · BC카드 결제 데이터 기반</strong> (최근 1개월 매출 순위). 백분위는 같은 업종 안에서의 매출 순위이며 낮을수록 상위입니다.</p>
<p><strong>매출점수</strong>는 이 백분위(전국 75% + 구 안 25%)를 0~100점으로 바꾼 값입니다. 맛 평가가 아니라 얼마나 많이 팔리는지를 보여 주므로, 좌석이 적거나 예약제인 집은 낮게 나올 수 있어요.</p>
<p>영업 예상 시간은 최근 1개월 결제 승인 시각으로 추정한 값이라 공식 영업시간과 다를 수 있습니다. 이 사이트는 BC카드나 eat.pl의 공식 서비스가 아닙니다.</p>
</footer>
<script src="{up}assets/app.js"></script>
</body></html>"""


def list_section(cards: str, present: set, cities: list | None = None, purpose: bool = True, default: str = "meal") -> str:
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
                       '<button type="button" data-v="local">동네 맛집</button>'
                       '<button type="button" data-v="foreign">외국인 많이 가는 곳</button>'
                       '<button type="button" data-v="pick">주인장 픽</button></nav>')
    return f"""{city_nav}<div class="sticky">
<nav class="filters" data-key="g" aria-label="종류">{group_chips(present, default)}</nav>
{purpose_nav}</div>
<p class="count" id="count"></p>
<ol class="list" id="list">
{cards}
</ol>"""


# ------------------------------------------------------------------ build
def main():
    refresh = "--refresh" in sys.argv
    areas = json.loads((ROOT / "areas.json").read_text())
    picks = json.loads(PICKS.read_text()) if PICKS.exists() else {"picks": [], "items": []}
    pick_name = {p["merUrl"]: p["name"] for p in picks.get("picks", [])}

    OUT.mkdir(exist_ok=True)
    for d in ("area", "cat", "assets"):
        (OUT / d).mkdir(exist_ok=True)
    for f in ("style.css", "app.js"):
        (OUT / "assets" / f).write_text((SRC / f).read_text())
    if (SRC / "og.png").exists():
        (OUT / "assets" / "og.png").write_bytes((SRC / "og.png").read_bytes())

    places = {}  # merUrl → item (every place we know: area pages + picks lookups)

    def prep(r):
        url = r.get("merUrl") or ""
        if not url.startswith("https://web.paybooc.ai/") or (r.get("merTpbuzNm") in SKIP_CATS):
            return None
        if url in places:
            return places[url]
        r = dict(r)
        r["_score"] = score_of(r)
        r["_pick"] = url in pick_name
        r["_name"] = pick_name.get(url) or clean_name(r.get("merNm"))
        r["_area"] = ""
        places[url] = r
        return r

    index_cards, latest, built_areas = {}, "", []
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
        items.sort(key=lambda r: (-r["_score"], float(r.get("allSaleRnk") if r.get("allSaleRnk") is not None else 999)))
        print(f"{a['slug']:16s} {len(items):3d} places")
        updated = fetched[:10]
        cards = "\n".join(card(r, i + 1) for i, r in enumerate(items))
        meals = [r for r in items if GROUP_OF.get(r.get("merTpbuzNm")) not in ("drink", "cafe")]
        top3 = ", ".join(r["_name"] for r in meals[:3])
        present = {GROUP_OF.get(r.get("merTpbuzNm"), "etc") for r in items}
        body = f"""
<section class="hero small">
  <p class="crumb"><a href="../index.html">전체 지역</a> › {esc(a['city'])}</p>
  <h1>{esc(a['name'])} 점심·회식 맛집 <span>매출점수 순위</span></h1>
  <p class="sub">BC카드 최근 1개월 결제 기준 매출 상위 {len(items)}곳 · {esc(updated)} 업데이트</p>
</section>
<section class="picker" id="picker">
  <h2>오늘 점심 뭐 먹지?</h2>
  <p>이 동네 점심 영업하는 매출 상위 집 중에서 3곳을 골라 드려요.</p>
  <button type="button" id="pick">3곳 뽑기</button>
  <ol id="picked" class="picked"></ol>
</section>
{list_section(cards, present)}"""
        title = f"{a['name']} 점심·회식 맛집 매출 순위 TOP {len(items)} | {SITE_NAME}"
        desc = f"{a['name']} 맛집을 별점이 아닌 BC카드 결제 매출점수로. {top3} 등. 한식·고기·일식·술집 등 종류별, 영업 예상 시간, 회식 적합 여부까지."
        (OUT / "area" / f"{a['slug']}.html").write_text(page(title, desc, body, f"area/{a['slug']}.html", 1))
        index_cards.setdefault(a["city"], []).append((a, len(items), top3))

    for r in picks.get("items", []):
        prep(r)
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
</section>
{list_section(cards, {GROUP_OF.get(r.get('merTpbuzNm'), 'etc') for r in prows}, cities, default="")}"""
        (OUT / "picks.html").write_text(page(f"주인장 픽 — 직접 모은 맛집 {len(prows)}곳의 매출점수 | {SITE_NAME}",
                                             "사이트 주인이 지도에 모아 온 전국 맛집을 BC카드 결제 매출점수로 다시 봤습니다.",
                                             body, "picks.html", 0))

    # ---- search index (loaded lazily by the home page search box)
    index = [[r["_name"], GROUP_OF.get(r.get("merTpbuzNm"), "etc"), r.get("merTpbuzNm") or "", short_addr(r.get("merAddr")),
              r["_score"], r.get("allSaleRnk"), r.get("ccgSaleRnk"), r.get("merUrl").replace(MER_PREFIX, "", 1), r["_area"], 1 if r["_pick"] else 0]
             for r in ranked]
    (OUT / "assets" / "places.json").write_text(json.dumps(
        {"areas": area_name, "groups": GROUP_NAME, "places": index}, ensure_ascii=False, separators=(",", ":")))

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
  <div id="results" class="results" aria-live="polite"></div>
</section>
<section class="browse">
  <h2>종류별 매출점수 순위</h2>
  <div class="chips">{cats}<a class="chip pick" href="picks.html">주인장 픽 {len(prows)}곳</a></div>
</section>
<div id="areas"><h2 class="section-title">동네별 점심·회식 맛집</h2>
{''.join(groups)}</div>
<section class="how">
  <h2>점수는 이렇게 읽어요</h2>
  <ul>
    <li><strong>매출점수</strong> — 전국 같은 업종 가게 중 매출 순위(75%)와 같은 구 안 순위(25%)를 0~100점으로 바꾼 값입니다. 상위 0.1%면 95점 안팎, 1%면 86점, 10%면 68점 정도예요.</li>
    <li><strong>전국구 90+ · 찐맛집 80+ · 동네 강자 70+ · 꾸준한 집 60+</strong> — 맛 평가가 아니라 판매량 기준이라, 좌석이 적은 집이나 예약제 식당은 낮게 나올 수 있어요.</li>
    <li><strong>회식 적합</strong> — 저녁 시간대 단체 결제가 있었던 곳. <strong>동네 맛집</strong> — 동네 손님 비율이 구 평균보다 높은 곳.</li>
  </ul>
</section>"""
    title = f"{SITE_NAME} — 우리 동네 맛집 매출점수 검색, 점심·회식 맛집 순위"
    desc = f"별점 말고 실제 결제 매출로 매긴 맛집 점수. {len(places):,}곳 검색, 한식·고기·일식 등 종류별 순위, 을지로·강남·여의도·판교 등 {len(built_areas)}개 동네 점심·회식 맛집."
    (OUT / "index.html").write_text(page(title, desc, body, "index.html", 0))

    if BASE_URL:
        urls = ([f"{BASE_URL}/index.html", f"{BASE_URL}/picks.html"]
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
