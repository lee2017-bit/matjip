#!/usr/bin/env python3
"""카카오맵 교차검증: eat.pl 가게가 실제로 어떤 업종의 가게인지(카페인지, 식당이 맞는지) 카카오 장소 검색으로 확인한다.

  python3 build.py && python3 tools/kakao_check.py [--limit 200]   # → data/kakao_check.json (이어서 build.py 를 다시 돌린다)

- 대상: 사이트에서 눈에 띄는 가게 (주인장 픽·취향, 대화하기 좋은 곳, 고급, 동네별 매출점수 상위 15곳) 중 아직 확인 안 한 곳
- 방법: '가게 이름 + 구 + 도로명'으로 카카오 장소 검색(k-skill 프록시가 중계하는 카카오 공식 Local API) 후
  이름이 비슷하고 300m 안에 있는 장소를 같은 가게로 본다. 1초 간격, 결과는 저장해 두고 다시 묻지 않는다.
- 저장: {eat.pl id: {"n": 카카오 이름, "c": 카카오 업종, "g": 업종 그룹 코드, "d": 거리 m}} / 못 찾으면 {"none": 1}
"""
import argparse
import collections
import difflib
import json
import math
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "kakao_check.json"
KAKAO = "https://k-skill-proxy.nomadamas.org/v1/kakao-map/search/keyword"
MER = "https://web.paybooc.ai/mer/web/profile/"


def norm(s):
    s = re.sub(r"\(주\)|㈜|주식회사|본점|직영점|[0-9]+호점", "", s or "").lower()
    return re.sub(r"[^0-9a-z가-힣]", "", s)


def similar(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 0.9 if min(len(a), len(b)) >= 2 else 0.6
    return difflib.SequenceMatcher(None, a, b).ratio()


def meters(lat1, lng1, lat2, lng2):
    dy = (lat1 - lat2) * 111000
    dx = (lng1 - lng2) * 111000 * math.cos(math.radians(lat1))
    return math.hypot(dx, dy)


def search(query):
    url = KAKAO + "?" + urllib.parse.urlencode({"query": query, "size": 5})
    for wait in (0, 10, 30):
        if wait:
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "maechul-matjip/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r).get("documents") or []
        except Exception as exc:  # 429 등은 잠시 쉬었다가 다시
            print(f"  ! {query}: {exc}", file=sys.stderr)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="이번에 확인할 최대 개수 (0 = 전부)")
    args = ap.parse_args()
    data = json.loads((ROOT / "site" / "assets" / "places.json").read_text())
    done = json.loads(OUT.read_text()) if OUT.exists() else {}

    places = data["places"]  # v2 압축 형식: [이름, 업종번호, 주소, 점수, 전국%, 구%, id, 동네번호, 플래그, 위도, 경도]
    want = {q[6] for q in places if q[8] & (1 | 4 | 8 | 64)}
    by_area = collections.defaultdict(list)
    for q in places:
        if q[7] >= 0 and not q[8] & 32:
            by_area[q[7]].append(q)
    for rows in by_area.values():
        want |= {q[6] for q in sorted(rows, key=lambda q: -q[3])[:15]}
    todo = [q for q in places if q[6] in want and q[6] not in done and q[9]]
    todo.sort(key=lambda q: -q[3])
    if args.limit:
        todo = todo[:args.limit]
    print(f"to check: {len(todo)} (already {len(done)})", file=sys.stderr)

    for n, q in enumerate(todo):
        name, addr, mid, lat, lng = q[0], q[2], q[6], q[9], q[10]
        parts = addr.split()
        area = " ".join(parts[1:3]) if len(parts) >= 3 else addr
        docs = search(f"{name} {area}")
        if docs is None:
            break
        best, best_s, best_food = None, 0.0, False
        for doc in docs:
            try:
                d = meters(lat, lng, float(doc["y"]), float(doc["x"]))
            except (KeyError, ValueError):
                continue
            s = similar(name, doc.get("place_name"))
            ok = (s >= 0.5 and d <= 300) or (s >= 0.3 and d <= 60)
            food = doc.get("category_group_code") in ("FD6", "CE7") or (doc.get("category_name") or "").startswith("음식점")
            # 같은 이름의 본사·법인(서비스,산업 > 기업)보다 음식점·카페를 먼저 고른다
            if ok and ((food and not best_food) or (food == best_food and s - d / 3000 > best_s)):
                best, best_s, best_food = (doc, d), s - d / 3000, food
        if best:
            doc, d = best
            done[mid] = {"n": doc.get("place_name"), "c": doc.get("category_name"), "g": doc.get("category_group_code"), "d": round(d)}
        else:
            done[mid] = {"none": 1}
        if n % 100 == 0:
            OUT.write_text(json.dumps(done, ensure_ascii=False, separators=(",", ":")))
            print(f"  {n}/{len(todo)}", file=sys.stderr)
        time.sleep(1.0)
    OUT.write_text(json.dumps(done, ensure_ascii=False, separators=(",", ":")))
    found = sum(1 for v in done.values() if "none" not in v)
    print(f"checked {len(done)}: found {found}, not found {len(done) - found} → {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
