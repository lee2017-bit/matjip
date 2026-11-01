// 매출맛집 실시간 조회 Worker (Cloudflare Workers, 무료 플랜으로 충분)
//
//   GET https://<이름>.<계정>.workers.dev/?q=가게이름
//   → 카카오 로컬 키워드 검색으로 가게 주소를 찾고 (음식점·카페만, 최대 2곳)
//   → 그 도로명 주소로 eat.pl 잇플 검색(k-skill 프록시)을 불러 같은 이름의 가게를 찾아 돌려준다.
//
// 설정 (Cloudflare 대시보드 → Worker → Settings → Variables and Secrets):
//   KAKAO_REST_KEY  (Secret, 필수)  카카오 developers 앱의 "REST API 키"
//   ALLOW_ORIGIN    (Text, 선택)    기본값 https://lee2017-bit.github.io
//
// 이 Worker는 eat.pl 검색 API만 부른다. 맛집 상세 페이지(merUrl)는 절대 호출하지 않고
// 받은 주소를 그대로 돌려줄 뿐이다 (공모전 규칙: 서버에서 merUrl 직접 호출 금지).
// 같은 질문·같은 주소는 7일 동안 캐시해서 프록시에 부담을 주지 않는다.

const PROXY = 'https://k-skill-proxy.nomadamas.org/v1/bccard-eatpl/search';
const CACHE_TTL = 7 * 24 * 3600;
const MAX_PLACES = 2;

export default {
  async fetch(request, env, ctx) {
    const origin = env.ALLOW_ORIGIN || 'https://lee2017-bit.github.io';
    const cors = {
      'Access-Control-Allow-Origin': origin,
      'Access-Control-Allow-Methods': 'GET, OPTIONS',
      'Vary': 'Origin',
    };
    if (request.method === 'OPTIONS') return new Response(null, { headers: cors });
    if (request.method !== 'GET') return json({ message: 'GET only' }, 405, cors);

    const url = new URL(request.url);
    const q = (url.searchParams.get('q') || '').trim().slice(0, 40);
    if (q.length < 2) return json({ message: '두 글자 이상 입력해 주세요.' }, 400, cors);
    if (!env.KAKAO_REST_KEY) return json({ message: 'KAKAO_REST_KEY가 설정되지 않았어요.' }, 500, cors);

    const cache = caches.default;
    const cacheKey = new Request('https://cache.matjip/q/' + encodeURIComponent(q.replace(/\s+/g, ' ')));
    const hit = await cache.match(cacheKey);
    if (hit) return withHeaders(hit, cors);

    // 1) 가게 이름 → 주소 (카카오 로컬)
    const kakao = await fetch('https://dapi.kakao.com/v2/local/search/keyword.json?size=10&query=' + encodeURIComponent(q), {
      headers: { Authorization: 'KakaoAK ' + env.KAKAO_REST_KEY },
    });
    if (!kakao.ok) return json({ message: '주소 검색에 실패했어요. 잠시 뒤 다시 시도해 주세요.' }, 502, cors);
    const docs = ((await kakao.json()).documents || [])
      .filter(d => (d.category_group_code === 'FD6' || d.category_group_code === 'CE7') && d.road_address_name)
      .slice(0, MAX_PLACES);
    if (!docs.length) return store(cache, cacheKey, ctx, { query: q, results: [], message: '그런 이름의 음식점·카페를 지도에서 찾지 못했어요.' }, cors);

    // 2) 주소 → eat.pl 검색 → 이름이 맞는 가게
    const results = [];
    for (const d of docs) {
      const loc = locationOf(d.road_address_name);
      const items = loc ? await eatpl(loc, cache, ctx) : null;
      const item = items ? best(d.place_name, items) : null;
      results.push({ place: { name: d.place_name, addr: d.road_address_name, category: d.category_name }, item });
    }
    const found = results.some(r => r.item);
    return store(cache, cacheKey, ctx, {
      query: q, results,
      message: found ? '' : 'BC카드 결제 데이터에서 이 가게를 찾지 못했어요. 결제 건수가 적거나 업종이 다르게 등록된 곳일 수 있어요.',
    }, cors);
  },
};

function locationOf(road) {
  // '서울 중구 세종대로11길 26' → '중구 세종대로11길 26' (세종시는 그대로)
  const t = road.trim().split(/\s+/);
  return t[0].startsWith('세종') ? t.join(' ') : t.slice(1).join(' ');
}

async function eatpl(loc, cache, ctx) {
  const key = new Request('https://cache.matjip/loc/' + encodeURIComponent(loc));
  const hit = await cache.match(key);
  if (hit) return (await hit.json()).items;
  for (const wait of [0, 2500]) {
    if (wait) await new Promise(r => setTimeout(r, wait));
    const r = await fetch(PROXY + '?location=' + encodeURIComponent(loc), { headers: { 'User-Agent': 'maechul-matjip-worker/1.0' } });
    if (r.status === 429) continue;
    if (!r.ok) return null;
    const items = ((await r.json()).items || []).filter(i => (i.merUrl || '').startsWith('https://web.paybooc.ai/'));
    ctx.waitUntil(cache.put(key, new Response(JSON.stringify({ items }), { headers: { 'Cache-Control': 'max-age=' + CACHE_TTL } })));
    return items;
  }
  return null;
}

function norm(s) {
  return String(s || '').toLowerCase().replace(/\(주\)|㈜|주식회사/g, '').replace(/[^0-9a-z가-힣]/g, '');
}

function baseName(s) {
  return norm(String(s || '').split(/\s+/).filter(w => !/(본점|직영점|점|지점|호점)$/.test(w) || w.length > 6).join(' ')) || norm(s);
}

function best(name, items) {
  const want = [norm(name), baseName(name)].filter(Boolean);
  let top = null, topScore = 0;
  for (const it of items) {
    const cands = String(it.merNm || '').split('/').map(norm).filter(Boolean);
    let s = 0;
    for (const w of want) for (const c of cands) {
      if (w === c) s = Math.max(s, 1);
      else if (Math.min(w.length, c.length) >= 2 && (w.includes(c) || c.includes(w))) s = Math.max(s, 0.9);
    }
    if (s > topScore) { top = it; topScore = s; }
  }
  return topScore >= 0.9 ? top : null;
}

function json(obj, status, headers) {
  return new Response(JSON.stringify(obj), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', ...headers } });
}

function withHeaders(res, headers) {
  const r = new Response(res.body, res);
  for (const [k, v] of Object.entries(headers)) r.headers.set(k, v);
  return r;
}

function store(cache, key, ctx, obj, cors) {
  const body = JSON.stringify(obj);
  ctx.waitUntil(cache.put(key, new Response(body, {
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'max-age=' + CACHE_TTL },
  })));
  return json(obj, 200, cors);
}
