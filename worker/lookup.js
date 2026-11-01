// 매출맛집 실시간 조회 Worker (Cloudflare Workers 무료 플랜으로 충분, 다른 키 필요 없음)
//
//   GET https://<이름>.<계정>.workers.dev/?q=가게이름        예) ?q=진주회관, ?q=을지로 진주회관
//   → eat.pl 잇플 가맹점 이름 검색(k-skill 프록시)을 불러 이름이 맞는 가게들을 돌려준다.
//
// 설정 (선택, Cloudflare 대시보드 → Worker → Settings → Variables and Secrets):
//   ALLOW_ORIGIN  (Text)  기본값 https://lee2017-bit.github.io
//
// 이 Worker는 eat.pl 검색 API만 부른다. 맛집 상세 페이지(merUrl)는 절대 호출하지 않고
// 받은 주소를 그대로 돌려줄 뿐이다 (공모전 규칙: 서버에서 merUrl 직접 호출 금지).
// 방문자 한 명이 버튼을 눌렀을 때 한 번(최대 3번) 조회하는 개인 검색 용도로만 쓴다.

const PROXY = 'https://k-skill-proxy.nomadamas.org/v1/bccard-eatpl/search';
const CACHE_TTL = 24 * 3600;

export default {
  async fetch(request, env, ctx) {
    const origin = env.ALLOW_ORIGIN || 'https://lee2017-bit.github.io';
    const cors = { 'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Methods': 'GET, OPTIONS', 'Vary': 'Origin' };
    if (request.method === 'OPTIONS') return new Response(null, { headers: cors });
    if (request.method !== 'GET') return json({ message: 'GET only' }, 405, cors);

    const q = (new URL(request.url).searchParams.get('q') || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    if (q.length < 2) return json({ message: '두 글자 이상 입력해 주세요.' }, 400, cors);

    const cache = caches.default;
    const cacheKey = new Request('https://cache.matjip/q/' + encodeURIComponent(q));
    const hit = await cache.match(cacheKey);
    if (hit) return withHeaders(hit, cors);

    // 시도 순서: ① 전체를 가게 이름으로 ② "동네 가게" → 마지막 단어를 이름, 앞을 지역으로 ③ "가게 동네" → 첫 단어를 이름
    const tries = [{ name: q, location: '' }];
    const t = q.split(' ');
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
      results: items.slice(0, 20).map(item => ({ item })),
      message: items.length ? '' : 'BC카드 결제 데이터에서 이 이름의 가게를 찾지 못했어요. 간판 이름 그대로(지점명 빼고) 검색해 보세요.',
    };
    ctx.waitUntil(cache.put(cacheKey, new Response(JSON.stringify(body), {
      headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'max-age=' + CACHE_TTL },
    })));
    return json(body, 200, cors);
  },
};

async function eatpl({ name, location }) {
  const u = PROXY + '?location=' + encodeURIComponent(location) + '&merNm=' + encodeURIComponent(name);
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

function json(obj, status, headers) {
  return new Response(JSON.stringify(obj), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', ...headers } });
}

function withHeaders(res, headers) {
  const r = new Response(res.body, res);
  for (const [k, v] of Object.entries(headers)) r.headers.set(k, v);
  return r;
}
