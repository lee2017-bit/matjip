// 매출맛집 실시간 조회 Worker (Cloudflare Workers 무료 플랜으로 충분, 다른 키 필요 없음)
//
//   GET https://<이름>.<계정>.workers.dev/?q=가게이름        예) ?q=진주회관, ?q=을지로 진주회관
//   → eat.pl 잇플 가맹점 이름 검색(k-skill 프록시)을 불러 이름이 맞는 가게들을 돌려준다.
//   GET https://<이름>.<계정>.workers.dev/?lat=37.56&lng=126.97   (지도 '내 위치')
//   → 좌표와 주변(약 450m) 지점의 동 이름을 찾고(k-skill 프록시의 카카오 좌표→주소) 최대 4개 동의 eat.pl 매출 상위 가게를 돌려준다.
//   모든 가게에 WGS84 위경도(lat, lng)를 붙여 준다.
//   POST https://<이름>.<계정>.workers.dev/chat   ('물어보기', Workers AI 무료 할당량 사용 — chat.js 참고)
//
// 설정 (선택, Cloudflare 대시보드 → Worker → Settings → Variables and Secrets):
//   ALLOW_ORIGIN  (Text)  기본값 https://lee2017-bit.github.io
//
// 이 Worker는 eat.pl 검색 API만 부른다. 맛집 상세 페이지(merUrl)는 절대 호출하지 않고
// 받은 주소를 그대로 돌려줄 뿐이다 (공모전 규칙: 서버에서 merUrl 직접 호출 금지).
// 방문자 한 명이 버튼을 눌렀을 때 한 번(최대 3번) 조회하는 개인 검색 용도로만 쓴다.

import { chat } from './chat.js';

const PROXY = 'https://k-skill-proxy.nomadamas.org/v1/bccard-eatpl/search';
const COORD2ADDR = 'https://k-skill-proxy.nomadamas.org/v1/kakao-map/coord2address';
const CACHE_TTL = 24 * 3600;

export default {
  async fetch(request, env, ctx) {
    const origin = env.ALLOW_ORIGIN || 'https://lee2017-bit.github.io';
    const cors = { 'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type', 'Vary': 'Origin' };
    if (request.method === 'OPTIONS') return new Response(null, { headers: cors });
    if (new URL(request.url).pathname === '/chat') {
      if (request.method !== 'POST') return json({ message: 'POST only' }, 405, cors);
      return chat(request, env, ctx, cors);
    }
    if (request.method !== 'GET') return json({ message: 'GET only' }, 405, cors);

    const params = new URL(request.url).searchParams;
    const lat = parseFloat(params.get('lat')), lng = parseFloat(params.get('lng'));
    if (!isNaN(lat) && !isNaN(lng)) return near(lat, lng, cors, ctx);
    const q = (params.get('q') || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    if (q.length < 2) return json({ message: '두 글자 이상 입력해 주세요.' }, 400, cors);

    const cache = caches.default;
    const cacheKey = new Request('https://cache.matjip/q/' + encodeURIComponent(q));
    const hit = await cache.match(cacheKey);
    if (hit) return withHeaders(hit, cors);

    // 시도 순서: ① 전체를 이름으로 ② 업종 말을 뗀 이름('농민백암순대' → '농민백암', eat.pl 등록 이름이 다를 때가 많다)
    // ③ "동네 가게" → 마지막 단어를 이름 ④ "가게 동네" → 첫 단어를 이름. 결과가 나오면 멈춘다.
    const tries = [{ name: q, location: '' }];
    const t = q.split(' ');
    const ti = t.findIndex(w => FOOD_SUFFIX.test(w) && core(w));
    if (ti !== -1) tries.push({ name: core(t[ti]), location: t.filter((_, i) => i !== ti).join(' ') });
    if (t.length > 1) {
      tries.push({ name: t[t.length - 1], location: t.slice(0, -1).join(' ') });
      tries.push({ name: t[0], location: t.slice(1).join(' ') });
    }
    let items = [], failed = false;
    for (let i = 0; i < tries.length && !items.length; i++) {
      if (i) await sleep(1500);  // 프록시에 몰아서 부르지 않는다
      const r = await eatpl(tries[i]);
      if (r === null) { failed = true; break; }
      items = r;
    }
    if (failed) return json({ results: [], message: '지금은 조회가 몰려 있어요. 잠시 뒤 다시 시도해 주세요.' }, 503, cors);

    items.sort((a, b) => (a.allSaleRnk ?? 999) - (b.allSaleRnk ?? 999));
    const body = {
      query: q,
      results: items.slice(0, 20).map(item => ({ item: withLatLng(item) })),
      message: items.length ? '' : 'BC카드 결제 데이터에서 이 이름의 가게를 찾지 못했어요. 간판 이름 그대로(지점명 빼고) 검색해 보세요.',
    };
    ctx.waitUntil(cache.put(cacheKey, new Response(JSON.stringify(body), {
      headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'max-age=' + CACHE_TTL },
    })));
    return json(body, 200, cors);
  },
};

async function near(lat, lng, cors, ctx) {
  if (lat < 33 || lat > 39 || lng < 124 || lng > 132) return json({ results: [], message: '국내 위치만 찾을 수 있어요.' }, 400, cors);
  const cache = caches.default;
  const key = new Request('https://cache.matjip/near3/' + lat.toFixed(3) + ',' + lng.toFixed(3));  // 약 100m 격자
  const hit = await cache.match(key);
  if (hit) return withHeaders(hit, cors);
  // 가운데 + 동서남북 약 450m 지점의 동 이름을 모아 최대 4개 동을 조회한다 (화면 가장자리까지 채우기)
  const pts = [[0, 0], [0.004, 0], [-0.004, 0], [0, 0.005], [0, -0.005]];
  const names = await Promise.all(pts.map(([dy, dx]) => dongAt(lat + dy, lng + dx)));
  const areas = [...new Set(names.filter(Boolean))].slice(0, 4);
  if (!areas.length) return json({ results: [], message: '위치의 동 이름을 찾지 못했어요.' }, 404, cors);
  const seen = new Set(), results = [];
  for (let i = 0; i < areas.length; i++) {
    if (i) await sleep(800);  // 프록시에 몰아서 부르지 않는다
    const items = await eatpl({ name: '', location: areas[i] });
    if (items === null) { if (!results.length) return json({ results: [], message: '지금은 조회가 몰려 있어요. 잠시 뒤 다시 시도해 주세요.' }, 503, cors); break; }
    for (const it of items) if (!seen.has(it.merUrl)) { seen.add(it.merUrl); results.push({ item: withLatLng(it) }); }
  }
  const body = { area: areas.join(', '), areas, results, message: results.length ? '' : '이 근처에서 가게를 찾지 못했어요.' };
  ctx.waitUntil(cache.put(key, new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'max-age=' + CACHE_TTL },
  })));
  return json(body, 200, cors);
}

async function dongAt(lat, lng) {
  try {
    const r = await fetch(COORD2ADDR + '?x=' + lng.toFixed(6) + '&y=' + lat.toFixed(6), { headers: { 'User-Agent': 'maechul-matjip-worker/1.0' } });
    if (!r.ok) return '';
    const a = ((((await r.json()).documents || [])[0] || {}).address) || {};
    // 읍·면 지역은 '포곡읍 마성리'처럼 리까지 오는데, eat.pl 은 '리' 단위로는 못 찾는다 → '처인구 포곡읍'
    const d3 = String(a.region_3depth_name || '').split(' ').filter(w => !/리$/.test(w) || w.length < 2).join(' ');
    return [a.region_2depth_name, d3].filter(Boolean).join(' ');
  } catch (e) { return ''; }
}

async function eatpl({ name, location }) {
  const u = PROXY + '?location=' + encodeURIComponent(location) + (name ? '&merNm=' + encodeURIComponent(name) : '');
  for (const wait of [0, 2500]) {
    if (wait) await sleep(wait);
    const r = await fetch(u, { headers: { 'User-Agent': 'maechul-matjip-worker/1.0' } });
    if (r.status === 429) continue;
    if (!r.ok) return null;
    return ((await r.json()).items || []).filter(i => (i.merUrl || '').startsWith('https://web.paybooc.ai/'));
  }
  return null;
}

const sleep = ms => new Promise(r => setTimeout(r, ms));
const FOOD_SUFFIX = /(순대국밥|순대국|순대|국밥|설렁탕|곰탕|해장국|칼국수|냉면|막국수|갈비|곱창|족발|보쌈|치킨|김밥|떡볶이|식당|회관|본점|집)$/;
const core = w => { const c = w.replace(FOOD_SUFFIX, ''); return c.length >= 2 ? c : ''; };

function json(obj, status, headers) {
  return new Response(JSON.stringify(obj), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', ...headers } });
}

function withHeaders(res, headers) {
  const r = new Response(res.body, res);
  for (const [k, v] of Object.entries(headers)) r.headers.set(k, v);
  return r;
}

// eat.pl 좌표(TM128/KATEC, Bessel) → WGS84. build.py 의 tm128_to_wgs84 와 같은 계산.
function withLatLng(item) {
  const x = parseFloat(item.xaxsVal), y = parseFloat(item.yaxsVal);
  if (isNaN(x) || isNaN(y)) return item;
  const [lat, lng] = tm128ToWgs84(x, y);
  return (lat > 33 && lat < 39 && lng > 124 && lng < 132) ? { ...item, lat: +lat.toFixed(5), lng: +lng.toFixed(5) } : item;
}

function tm128ToWgs84(x, y) {
  const a = 6377397.155, f = 1 / 299.1528128, e2 = 2 * f - f * f, ep2 = e2 / (1 - e2);
  const k0 = 0.9999, lat0 = 38 * Math.PI / 180, lon0 = 128 * Math.PI / 180, fe = 400000, fn = 600000;
  const mer = phi => a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
    - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * Math.sin(2 * phi)
    + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * Math.sin(4 * phi) - (35 * e2 ** 3 / 3072) * Math.sin(6 * phi));
  const mu = (mer(lat0) + (y - fn) / k0) / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256));
  const e1 = (1 - Math.sqrt(1 - e2)) / (1 + Math.sqrt(1 - e2));
  const phi1 = mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * Math.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * Math.sin(4 * mu)
    + (151 * e1 ** 3 / 96) * Math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * Math.sin(8 * mu);
  const n1 = a / Math.sqrt(1 - e2 * Math.sin(phi1) ** 2), t1 = Math.tan(phi1) ** 2, c1 = ep2 * Math.cos(phi1) ** 2;
  const r1 = a * (1 - e2) / (1 - e2 * Math.sin(phi1) ** 2) ** 1.5, d = (x - fe) / (n1 * k0);
  const lat = phi1 - (n1 * Math.tan(phi1) / r1) * (d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
    + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720);
  const lon = lon0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6 + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120) / Math.cos(phi1);
  const n = a / Math.sqrt(1 - e2 * Math.sin(lat) ** 2);
  const X = n * Math.cos(lat) * Math.cos(lon) - 146.43, Y = n * Math.cos(lat) * Math.sin(lon) + 507.89, Z = n * (1 - e2) * Math.sin(lat) + 681.46;
  const a2 = 6378137.0, f2 = 1 / 298.257223563, e22 = 2 * f2 - f2 * f2, p = Math.hypot(X, Y);
  let lat2 = Math.atan2(Z, p * (1 - e22));
  for (let i = 0; i < 6; i++) { const n2 = a2 / Math.sqrt(1 - e22 * Math.sin(lat2) ** 2); lat2 = Math.atan2(Z + e22 * n2 * Math.sin(lat2), p); }
  return [lat2 * 180 / Math.PI, Math.atan2(Y, X) * 180 / Math.PI];
}
