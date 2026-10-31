#!/usr/bin/env python3
"""Build the static site: fetch restaurant rankings per area, then render HTML pages.

Usage:
  python3 build.py                 # use cached data when it is younger than 6 days
  python3 build.py --refresh       # fetch everything again
  BASE_URL=https://example.github.io/eatpl python3 build.py   # absolute URLs for sitemap / share previews

Data: eat.pl 잇플 search (BC카드 결제 데이터 기반) via the public k-skill proxy.
Python standard library only.
"""
import html
import json
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
OUT = ROOT / "site"
SRC = ROOT / "src"
PROXY = os.environ.get("EATPL_PROXY", "https://k-skill-proxy.nomadamas.org/v1/bccard-eatpl/search")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
SITE_NAME = "매출맛집"
TAGLINE = "별점 말고, 실제로 많이 팔리는 집"
KST = timezone(timedelta(hours=9))
MAX_AGE = timedelta(days=6)

DRINK = ("호프", "주점", "이자카야", "바", "포차", "와인", "칵테일", "맥주")
CAFE = ("카페", "베이커리", "디저트", "제과", "커피", "아이스크림", "도넛")


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


def clean_name(n) -> str:
    n = re.sub(r"^\s*(\(주\)|㈜|주식회사|\(유\)|유한회사)\s*", "", str(n or ""))
    n = re.sub(r"\s*(\(주\)|㈜|주식회사)\s*$", "", n)
    return n.strip() or str(n)


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


def kind_of(cat: str) -> dict:
    cat = cat or ""
    return {
        "drink": any(k in cat for k in DRINK),
        "cafe": any(k in cat for k in CAFE) and "브런치" not in cat,
    }


def card(r: dict, rank: int) -> str:
    cat = r.get("merTpbuzNm") or "기타"
    k = kind_of(cat)
    lunch = open_for_lunch(r.get("srtTime") or "", r.get("endTime") or "") and not k["drink"] and not k["cafe"]
    tags = []
    if r.get("empyVsitYn"):
        tags.append("회식 적합")
    if r.get("loclPrsnPpltYn"):
        tags.append("동네 맛집")
    if r.get("frnrVsitYn"):
        tags.append("외국인 방문")
    hol = r.get("hldyDwk") or []
    hol_txt = "휴무 " + "·".join(hol) if hol else "휴무일 없음"
    cust = r.get("vistCstmrRnkList") or {}
    cust_txt = ", ".join(v for v in (cust.get("rk01"), cust.get("rk02"), cust.get("rk03")) if v)
    busy = " ".join(x for x in (f"{r.get('congestionDwk')}요일" if r.get("congestionDwk") else "", r.get("congestionTizn") or "") if x)
    flags = " ".join(f for f, on in (("lunch", lunch), ("group", r.get("empyVsitYn")), ("cafe", k["cafe"]),
                                    ("local", r.get("loclPrsnPpltYn")), ("foreign", r.get("frnrVsitYn"))) if on)
    hrs = hours(r.get("srtTime") or "", r.get("endTime") or "")
    return f"""<li class="card" data-flags="{esc(flags)}" data-rank="{rank}">
  <div class="card-head"><span class="rank">{rank}</span><h3>{esc(clean_name(r.get('merNm')))}</h3><span class="cat">{esc(cat)}</span></div>
  <p class="nums"><strong>전국 매출 상위 {pct(r.get('allSaleRnk'))}%</strong> · 구 매출 상위 {pct(r.get('ccgSaleRnk'))}%</p>
  <p class="meta">{esc(r.get('merAddr'))}</p>
  <p class="meta">{'영업 예상 ' + esc(hrs) + ' · ' if hrs else ''}{esc(hol_txt)}{(' · 붐비는 때 ' + esc(busy)) if busy else ''}</p>
  {f'<p class="meta">주 고객 {esc(cust_txt)}</p>' if cust_txt else ''}
  {''.join(f'<span class="tag">{esc(t)}</span>' for t in tags)}
  <a class="go" href="{esc(r.get('merUrl'))}" target="_blank" rel="noopener">eat.pl에서 자세히 보기 →</a>
</li>"""


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
</head><body>
<div id="inapp" class="inapp" hidden></div>
<header class="top"><a class="brand" href="{up}index.html">{SITE_NAME}</a><span class="tagline">{TAGLINE}</span></header>
<main>{body}</main>
<footer>
<p>데이터: <strong>eat.pl 잇플 · BC카드 결제 데이터 기반</strong> (최근 1개월 매출 순위). 순위는 같은 업종 안에서의 매출 백분위이며 낮을수록 상위입니다.</p>
<p>영업 예상 시간은 최근 1개월 결제 승인 시각으로 추정한 값이라 공식 영업시간과 다를 수 있습니다. 이 사이트는 BC카드나 eat.pl의 공식 서비스가 아닙니다.</p>
</footer>
<script src="{up}assets/app.js"></script>
</body></html>"""


# ------------------------------------------------------------------ build
def main():
    refresh = "--refresh" in sys.argv
    areas = json.loads((ROOT / "areas.json").read_text())
    OUT.mkdir(exist_ok=True)
    (OUT / "area").mkdir(exist_ok=True)
    (OUT / "assets").mkdir(exist_ok=True)
    for f in ("style.css", "app.js"):
        (OUT / "assets" / f).write_text((SRC / f).read_text())
    if (SRC / "og.png").exists():
        (OUT / "assets" / "og.png").write_bytes((SRC / "og.png").read_bytes())

    index_cards, latest = {}, ""
    for a in areas:
        items, fetched = area_items(a, refresh)
        if not items:
            print(f"{a['slug']:16s}   - skipped (no data yet)")
            continue
        latest = max(latest, fetched)
        items.sort(key=lambda r: float(r.get("allSaleRnk") if r.get("allSaleRnk") is not None else 999))
        print(f"{a['slug']:16s} {len(items):3d} places")
        updated = fetched[:10]
        cards = "\n".join(card(r, i + 1) for i, r in enumerate(items))
        top3 = ", ".join(clean_name(r.get("merNm")) for r in items[:3])
        body = f"""
<section class="hero small">
  <p class="crumb"><a href="../index.html">전체 지역</a> › {esc(a['city'])}</p>
  <h1>{esc(a['name'])} 점심·회식 맛집 <span>카드 매출 순위</span></h1>
  <p class="sub">BC카드 최근 1개월 결제 기준 매출 상위 {len(items)}곳 · {esc(updated)} 업데이트</p>
</section>
<section class="picker" id="picker">
  <h2>오늘 점심 뭐 먹지?</h2>
  <p>이 동네 점심 영업하는 매출 상위 집 중에서 3곳을 골라 드려요.</p>
  <button type="button" id="pick">3곳 뽑기</button>
  <ol id="picked" class="picked"></ol>
</section>
<nav class="filters" aria-label="필터">
  <button type="button" data-f="" class="on">전체</button>
  <button type="button" data-f="lunch">점심</button>
  <button type="button" data-f="group">회식</button>
  <button type="button" data-f="cafe">카페</button>
  <button type="button" data-f="local">동네 맛집</button>
  <button type="button" data-f="foreign">외국인 많이 가는 곳</button>
</nav>
<ol class="list" id="list">
{cards}
</ol>"""
        title = f"{a['name']} 점심·회식 맛집 매출 순위 TOP {len(items)} | {SITE_NAME}"
        desc = f"{a['name']} 맛집을 별점이 아닌 BC카드 결제 매출 순위로. {top3} 등. 영업 예상 시간, 붐비는 시간, 회식 적합 여부까지."
        (OUT / "area" / f"{a['slug']}.html").write_text(page(title, desc, body, f"area/{a['slug']}.html", 1))
        index_cards.setdefault(a["city"], []).append((a, len(items), top3))

    groups = []
    for city, rows in index_cards.items():
        tiles = "".join(
            f'<a class="tile" href="area/{esc(a["slug"])}.html" data-name="{esc(a["name"])} {esc(city)}">'
            f'<strong>{esc(a["name"])}</strong><span>{n}곳 · {esc(top3)}</span></a>'
            for a, n, top3 in rows)
        groups.append(f'<section class="city"><h2>{esc(city)}</h2><div class="tiles">{tiles}</div></section>')
    body = f"""
<section class="hero">
  <h1>회사 근처 점심·회식, <span>카드 매출</span>로 고르세요</h1>
  <p class="sub">별점이나 광고가 아니라 BC카드 최근 1개월 결제 데이터에서 실제로 많이 팔린 집을 동네별로 모았습니다. 영업 예상 시간, 붐비는 요일·시간, 회식 적합 여부도 함께 보여 드려요.</p>
  <input id="q" type="search" placeholder="지역 검색 (예: 을지로, 판교, 여의도)" aria-label="지역 검색">
</section>
{''.join(groups)}
<section class="how">
  <h2>순위는 이렇게 읽어요</h2>
  <ul>
    <li><strong>전국 매출 상위 0.05%</strong> — 전국의 같은 업종 가게 중 매출이 상위 0.05% 안에 든다는 뜻입니다. 숫자가 작을수록 잘 팔리는 집입니다.</li>
    <li><strong>회식 적합</strong> — 저녁 시간대 단체 결제가 있었던 곳입니다.</li>
    <li><strong>동네 맛집</strong> — 동네 손님 비율이 구 평균보다 높은 곳입니다.</li>
  </ul>
</section>"""
    title = f"{SITE_NAME} — 회사 근처 점심·회식 맛집을 카드 매출 순위로"
    desc = "별점 말고 실제 결제 매출로 고른 동네별 점심·회식 맛집. 을지로, 강남, 여의도, 판교 등 35개 지역."
    (OUT / "index.html").write_text(page(title, desc, body, "index.html", 0))

    if BASE_URL:
        urls = [f"{BASE_URL}/index.html"] + [f"{BASE_URL}/area/{a['slug']}.html" for a in areas]
        sm = "".join(f"<url><loc>{esc(u)}</loc><lastmod>{latest[:10]}</lastmod></url>" for u in urls)
        (OUT / "sitemap.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sm}</urlset>')
        (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n")
    else:
        (OUT / "robots.txt").write_text("User-agent: *\nAllow: /\n")
    print(f"built {len(areas)} area pages into {OUT}")


if __name__ == "__main__":
    random.seed(0)
    main()
