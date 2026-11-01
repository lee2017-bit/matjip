#!/usr/bin/env python3
"""Build data/picks.json ("주인장 픽") from public Kakao Map favorite folders.

  python3 tools/picks.py --raw /tmp/picks-raw 226557 609868 ...   # Kakao folder ids

1. reads the public folders (map.kakao.com/favorite/list?folderid=...)
2. looks each place up on eat.pl by its road address (k-skill proxy, one call per address,
   3 s apart, responses cached in --raw so a rerun only fetches what is missing)
3. matches the place name against the stores at that address and writes data/picks.json:
   picks = [{name, merUrl}], items = every store returned (they enlarge the search index).
Only the place name and the matched eat.pl store are kept: no folder names, memos or Kakao ids.
"""
import argparse
import difflib
import hashlib
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROXY = "https://k-skill-proxy.nomadamas.org/v1/bccard-eatpl/search"
KST = timezone(timedelta(hours=9))
CORP = r"\(주\)|㈜|주식회사|\(유\)|유한회사"


def get_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "maechul-matjip/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def location_of(addr: str):
    """'서울 중구 수표로 46-1 1층 (저동2가)' → '중구 수표로 46-1' (what the eat.pl search understands)."""
    a = re.sub(r"\(.*?\)", "", addr or "").replace(",", " ")
    t = a.split()
    if not t or not re.search("[가-힣]", a):
        return None
    start = 0 if t[0].startswith("세종") else 1
    for i in range(start, len(t) - 1):
        if re.search(r"(로|길)$", t[i]) and re.match(r"^\d+(-\d+)?$", t[i + 1]):
            return " ".join(t[start:i + 2])
    for i in range(start, len(t)):  # 지번 주소
        if re.match(r"^(산)?\d+(-\d+)?$", t[i]):
            return " ".join(t[start:i + 1])
    return None


def norm(s: str) -> str:
    s = re.sub(CORP, "", s or "").lower()
    return re.sub(r"[^0-9a-z가-힣]", "", s)


def base(s: str) -> str:
    """drop branch words: '광화문미진 본점' → '광화문미진', '신동궁감자탕 선릉직영점' → '신동궁감자탕'."""
    words = [w for w in re.sub(CORP, " ", s or "").split() if not re.search(r"(본점|직영점|점|지점|호점)$", w) or len(w) > 6]
    return norm(" ".join(words)) or norm(s)


def match(name: str, items: list):
    want = {norm(name), base(name)} - {""}
    best, best_r = None, 0.0
    for it in items:
        cands = {norm(x) for x in (it.get("merNm") or "").split("/")} | {norm(re.sub(r"^.*\(([^)]*)\)\s*$", r"\1", it.get("merNm") or ""))}
        cands -= {""}
        r = 0.0
        for w in want:
            for c in cands:
                if w == c:
                    r = max(r, 1.0)
                elif min(len(w), len(c)) >= 2 and (w in c or c in w):
                    r = max(r, 0.9 if min(len(w), len(c)) >= 3 else 0.8)
                else:
                    r = max(r, difflib.SequenceMatcher(None, w, c).ratio())
        if r > best_r:
            best, best_r = it, r
    return (best, best_r) if best_r >= 0.75 else (None, best_r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="+")
    ap.add_argument("--raw", default=str(ROOT / "data" / "picks-raw"), help="cache dir for eat.pl responses")
    ap.add_argument("--out", default=str(ROOT / "data" / "picks.json"))
    ap.add_argument("--offline", action="store_true", help="do not call eat.pl; use only cached responses")
    args = ap.parse_args()
    raw = pathlib.Path(args.raw)
    raw.mkdir(parents=True, exist_ok=True)

    places, seen = [], set()
    for fid in args.folders:
        cached = raw / f"kakao_{fid}.json"
        if args.offline and cached.exists():
            d = json.loads(cached.read_text())
        else:
            d = get_json(f"https://map.kakao.com/favorite/list?folderid={fid}",
                         {"Referer": "https://map.kakao.com/", "Accept-Language": "ko-KR,ko;q=0.9"})
            cached.write_text(json.dumps(d, ensure_ascii=False))
        for f in d.get("favorites", []):
            if f.get("type") == "PLACE" and f["key"] not in seen:
                seen.add(f["key"])
                places.append({"name": f["display1"], "loc": location_of(f["display2"])})
        print(f"folder {fid}: {len(d.get('favorites', []))} places", file=sys.stderr)

    locs = sorted({p["loc"] for p in places if p["loc"]})
    print(f"{len(places)} places, {len(locs)} addresses", file=sys.stderr)
    for n, loc in enumerate(locs):
        f = raw / (hashlib.md5(loc.encode()).hexdigest() + ".json")
        if f.exists() or args.offline:
            continue
        for wait in (0, 15, 45, 120):
            if wait:
                time.sleep(wait)
            try:
                d = get_json(PROXY + "?" + urllib.parse.urlencode({"location": loc}))
                f.write_text(json.dumps({"loc": loc, "items": d.get("items") or []}, ensure_ascii=False))
                break
            except Exception as exc:
                print(f"  ! {loc}: {exc}", file=sys.stderr)
        if n % 50 == 0:
            print(f"  {n}/{len(locs)}", file=sys.stderr)
        time.sleep(3)

    picks, items, missed = [], {}, 0
    for p in places:
        f = raw / (hashlib.md5((p["loc"] or "").encode()).hexdigest() + ".json")
        rows = json.loads(f.read_text())["items"] if p["loc"] and f.exists() else []
        for it in rows:
            if (it.get("merUrl") or "").startswith("https://web.paybooc.ai/"):
                items[it["merUrl"]] = it
        it, _ = match(p["name"], rows)
        if it and it.get("merUrl"):
            picks.append({"name": p["name"], "merUrl": it["merUrl"]})
        else:
            missed += 1
    uniq = {p["merUrl"]: p for p in picks}
    out = {"fetched_at": datetime.now(KST).isoformat(timespec="seconds"),
           "source": "eat.pl 잇플 · BC카드 결제 데이터 기반", "picks": list(uniq.values()), "items": list(items.values())}
    pathlib.Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=0))
    print(f"matched {len(uniq)} picks, {missed} not found, {len(items)} stores saved → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
