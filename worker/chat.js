// 매출맛집 '물어보기' — Cloudflare Workers AI(무료 할당량)로 질문을 읽고 후보 가게 중에서 골라 준다.
//
//   POST /chat {"step":"parse","q":"딥스테이션에서 5시까지 다이빙하고 뭐 먹지?"}
//   → {intent:{place, area, time, kind, foods, ...}, place:{name, addr, lat, lng} | null}
//     AI가 질문에서 장소·시간·원하는 것을 뽑고, 장소는 카카오 장소 검색(k-skill 프록시)으로 좌표를 찾는다.
//   POST /chat {"step":"answer","q":..., "intent":..., "place":{"name":...}, "cands":[{i,n,c,s,d,t,h}, ...]}
//   → {answer, picks:[{i, why}]}
//     후보 목록은 사이트(브라우저)가 매출점수·주인장 픽 데이터로 만든다. AI는 그 번호만 고르고,
//     가게 링크(merUrl)는 사이트가 데이터 그대로 붙인다 (AI가 링크·가게를 지어낼 수 없게).
//
// 비용: Workers Free 플랜은 Workers AI 하루 10,000 뉴런 무료, 넘으면 과금 없이 오류만 난다 (질문 1개 ≈ 30~60 뉴런).
// 남용 막기: 등록한 사이트(Origin)에서 온 요청만, IP당 하루 질문 수 제한, 질문 200자·후보 20곳 제한, 같은 질문은 캐시.

const MODELS = ['@cf/google/gemma-4-26b-a4b-it', '@cf/qwen/qwen3-30b-a3b-fp8'];
const KAKAO_KEYWORD = 'https://k-skill-proxy.nomadamas.org/v1/kakao-map/search/keyword';
const DAILY_LIMIT = { parse: 30, answer: 40 };

const PARSE_SYS = `너는 맛집 질문을 분석해 JSON 하나만 출력한다. 설명이나 코드블록 없이 JSON만.
형식: {"place":"","area":"","time":"","kind":"meal","foods":[],"avoid":[],"talk":false,"premium":false,"group":false,"young":false,"female":false,"chain_ok":false,"situation":""}
- place: 질문에 나온 구체적인 장소 이름(건물·역·시설·가게·랜드마크). 예: "딥스테이션", "강남역", "코엑스". 없으면 "".
- area: 동네·지역 이름(시·구·동). 예: "성수동", "용인 처인구". 없으면 "".
- time: 식사를 시작할 시각, 24시간 "HH:MM". 낮 활동이 "5시까지"면 오후 5시로 보고 "17:30". "점심"→"12:00", "저녁"→"18:30", "야식·늦게"→"22:30", "아침"→"08:30". 단서가 없으면 "".
- kind: 식사 "meal", 카페·디저트 "cafe", 술자리 "drink".
- foods: 원하는 음식 종류를 짧은 한국어 낱말로 최대 5개. 직접 말하지 않았어도 상황에 어울리는 것을 추론한다. 예: 운동·물놀이 뒤 → ["국밥","찌개","고기"].
- avoid: 피하고 싶다고 한 음식.
- talk: 조용히 대화·데이트·소개팅·접대면 true. premium: 기념일·접대·비싸도 좋은 곳이면 true. group: 여럿이·회식·단체면 true.
- young: 20~30대가 많이 가는 곳을 원하면 true. female: 여성 손님이 많은 곳을 원하면 true. chain_ok: 프랜차이즈도 괜찮다고 하면 true.
- situation: 상황 요약 한 문장(30자 안팎).`;

const ANSWER_SYS = `너는 맛집 사이트 '매출맛집'의 추천 도우미다. 질문과 후보 가게 목록을 보고 후보 중에서만 3곳(많아야 4곳)을 골라 JSON 하나만 출력한다. 설명이나 코드블록 없이 JSON만.
형식: {"answer":"","picks":[{"i":0,"why":""}]}
규칙:
- i는 후보 번호. 목록에 없는 가게는 절대 말하지 않는다.
- 메뉴 이름·가격·별점·맛 평가처럼 목록에 없는 사실을 지어내지 않는다. 업종·거리·매출점수·태그·영업시간만 근거로 쓴다. 업종에서 짐작되는 정도(찜/탕/찌개 → 따뜻한 국물)는 괜찮다.
- 매출점수는 BC카드 결제 매출 순위로 매긴 0~100점이다(맛 평가가 아님). 높을수록 손님이 많이 찾는 집이다.
- '주인장 픽'은 사이트 주인이 직접 모아 둔 맛집, '주인장 취향'은 주인장 픽과 특징이 비슷한 집이다. 상황에 맞으면 먼저 고려한다.
- 거리를 고려한다. 2km가 넘으면 차로 가기 좋은 거리라고 말해도 된다. 시간 계산은 하지 않는다.
- 업종이 서로 겹치지 않게 고르면 좋다.
- why: 상황에 맞는 이유 한 문장(45자 이내, 해요체).
- answer: 상황을 짚고 어떤 기준으로 골랐는지 해요체 2문장 이내. 링크·URL은 쓰지 않는다.
- 후보가 질문과 잘 맞지 않으면 answer에서 솔직하게 말하고 가장 가까운 대안을 고른다.`;

export async function chat(request, env, ctx, cors) {
  const origin = env.ALLOW_ORIGIN || 'https://lee2017-bit.github.io';
  if (request.headers.get('Origin') !== origin) return json({ message: '이 사이트에서만 쓸 수 있어요.' }, 403, cors);
  if (!env.AI) return json({ message: 'AI 설정이 아직 안 되어 있어요.' }, 503, cors);
  let body;
  try {
    const raw = await request.text();
    if (raw.length > 12000) return json({ message: '요청이 너무 커요.' }, 413, cors);
    body = JSON.parse(raw);
  } catch (e) { return json({ message: '요청 형식이 잘못됐어요.' }, 400, cors); }
  const step = body.step === 'answer' ? 'answer' : 'parse';
  const q = String(body.q || '').replace(/\s+/g, ' ').trim().slice(0, 200);
  if (q.length < 4) return json({ message: '조금 더 자세히 물어봐 주세요.' }, 400, cors);

  const cache = caches.default;
  const key = new Request('https://cache.matjip/chat/' + step + '/' + await sha(step === 'parse' ? q : JSON.stringify([q, body.place && body.place.name, (body.cands || []).map(c => c && c.n)])));
  const hit = await cache.match(key);
  if (hit) return withHeaders(hit, cors);

  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
  if (await overLimit(cache, ctx, ip, step)) return json({ message: '오늘 물어볼 수 있는 횟수를 다 썼어요. 내일 다시 물어봐 주세요.' }, 429, cors);

  let out;
  try {
    out = step === 'parse' ? await parse(env, q) : await answer(env, q, body);
  } catch (e) {
    const msg = String(e && e.message || e);
    console.log('chat error', step, msg);
    const quota = /4006|neuron|quota|limit|capacity/i.test(msg);
    return json({ message: quota ? '오늘 AI 무료 사용량을 다 썼어요. 내일 다시 물어봐 주세요.' : 'AI가 지금 답하지 못했어요. 잠시 뒤 다시 물어봐 주세요.' }, 503, cors);
  }
  const res = json(out, 200, cors);
  ctx.waitUntil(cache.put(key, new Response(JSON.stringify(out), {
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'max-age=' + (step === 'parse' ? 86400 : 6 * 3600) },
  })));
  return res;
}

async function parse(env, q) {
  const raw = await ask(env, PARSE_SYS, '질문: ' + q, 350, 0.1);
  const j = raw || {};
  const s = (v, n) => String(v || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const list = v => (Array.isArray(v) ? v : []).map(x => s(x, 12)).filter(Boolean).slice(0, 5);
  const time = /^([01]?\d|2[0-3]):[0-5]\d$/.test(s(j.time, 5)) ? s(j.time, 5).padStart(5, '0') : '';
  const intent = {
    place: s(j.place, 30), area: s(j.area, 30), time,
    kind: ['meal', 'cafe', 'drink'].includes(j.kind) ? j.kind : 'meal',
    foods: list(j.foods), avoid: list(j.avoid),
    talk: !!j.talk, premium: !!j.premium, group: !!j.group, young: !!j.young, female: !!j.female, chain_ok: !!j.chain_ok,
    situation: s(j.situation, 60),
  };
  let place = null;
  if (intent.place) place = await findPlace(intent.place + (intent.area ? ' ' + intent.area : ''), intent.place) || (intent.area ? await findPlace(intent.place, intent.place) : null);
  if (!place && intent.area) place = await findPlace(intent.area, intent.area);
  return { intent, place };
}

async function answer(env, q, body) {
  const intent = body.intent || {};
  const cands = (Array.isArray(body.cands) ? body.cands : []).slice(0, 20).map((c, i) => ({
    i, n: clip(c.n, 30), c: clip(c.c, 16), s: Math.max(0, Math.min(100, parseInt(c.s, 10) || 0)), d: Math.max(0, parseInt(c.d, 10) || 0),
    t: (Array.isArray(c.t) ? c.t : []).map(x => clip(x, 12)).filter(Boolean).slice(0, 6), h: clip(c.h, 16),
  })).filter(c => c.n);
  if (!cands.length) return { answer: '', picks: [] };
  const lines = cands.map(c => [c.i, c.n, c.c, c.s + '점', dist(c.d), c.t.join(', ') || '-', c.h || '-'].join(' | ')).join('\n');
  const user = '질문: ' + q
    + '\n상황: ' + (clip(intent.situation, 60) || '-') + (intent.time ? ' · 식사 시각 ' + clip(intent.time, 5) : '')
    + (Array.isArray(intent.foods) && intent.foods.length ? ' · 어울리는 음식 ' + intent.foods.slice(0, 5).map(x => clip(x, 12)).join(', ') : '')
    + '\n기준 장소: ' + (clip(body.place && body.place.name, 30) || '-')
    + '\n후보 (번호 | 이름 | 업종 | 매출점수 | 거리 | 태그 | 영업):\n' + lines;
  const j = await ask(env, ANSWER_SYS, user, 700, 0.4) || {};
  const seen = new Set();
  const picks = (Array.isArray(j.picks) ? j.picks : []).map(p => ({ i: parseInt(p && p.i, 10), why: noUrl(clip(p && p.why, 90)) }))
    .filter(p => p.i >= 0 && p.i < cands.length && !seen.has(p.i) && seen.add(p.i)).slice(0, 4);
  return { answer: noUrl(clip(j.answer, 300)), picks };
}

// 모델을 차례로 시도하고, 답에서 JSON 객체를 꺼낸다
async function ask(env, system, user, maxTokens, temperature) {
  let lastErr = null;
  for (const model of MODELS) {
    try {
      const r = await env.AI.run(model, {
        messages: [{ role: 'system', content: system }, { role: 'user', content: user }],
        max_tokens: maxTokens, temperature, chat_template_kwargs: { enable_thinking: false },
      });
      const j = toJson(r);
      if (j) return j;
      lastErr = new Error('no json from ' + model);
    } catch (e) { lastErr = e; if (/4006|neuron|quota/i.test(String(e && e.message))) break; }
  }
  throw lastErr || new Error('ai failed');
}

function toJson(r) {
  let t = r;
  if (r && typeof r === 'object') {
    if (r.response && typeof r.response === 'object') return r.response;
    t = r.response ?? (r.choices && r.choices[0] && r.choices[0].message && r.choices[0].message.content) ?? (r.result && r.result.response) ?? '';
  }
  t = String(t || '').replace(/<think>[\s\S]*?<\/think>/g, '');
  const a = t.indexOf('{'), b = t.lastIndexOf('}');
  if (a === -1 || b <= a) return null;
  const s = t.slice(a, b + 1);
  try { return JSON.parse(s); } catch (e) { /* fall through */ }
  try { return JSON.parse(s.replace(/,\s*([}\]])/g, '$1')); } catch (e) { return null; }
}

async function findPlace(query, want) {
  try {
    const r = await fetch(KAKAO_KEYWORD + '?size=5&query=' + encodeURIComponent(query), { headers: { 'User-Agent': 'maechul-matjip-worker/1.0' } });
    if (!r.ok) return null;
    const docs = ((await r.json()).documents || []).filter(d => +d.y > 33 && +d.y < 39 && +d.x > 124 && +d.x < 132);
    if (!docs.length) return null;
    const w = norm(want);
    const d = docs.find(x => norm(x.place_name) === w) || docs.find(x => norm(x.place_name).indexOf(w) === 0) || docs[0];
    return { name: d.place_name, addr: d.road_address_name || d.address_name || '', lat: +(+d.y).toFixed(5), lng: +(+d.x).toFixed(5) };
  } catch (e) { return null; }
}

async function overLimit(cache, ctx, ip, step) {
  const key = new Request('https://cache.matjip/rl/' + new Date().toISOString().slice(0, 10) + '/' + step + '/' + encodeURIComponent(ip));
  const hit = await cache.match(key);
  const n = hit ? parseInt(await hit.text(), 10) || 0 : 0;
  if (n >= DAILY_LIMIT[step]) return true;
  ctx.waitUntil(cache.put(key, new Response(String(n + 1), { headers: { 'Cache-Control': 'max-age=86400' } })));
  return false;
}

async function sha(s) {
  const b = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return [...new Uint8Array(b)].slice(0, 16).map(x => x.toString(16).padStart(2, '0')).join('');
}
const clip = (v, n) => String(v == null ? '' : v).replace(/\s+/g, ' ').trim().slice(0, n);
const noUrl = s => s.replace(/https?:\/\/\S+|www\.\S+/g, '').trim();
const norm = s => String(s || '').toLowerCase().replace(/[^0-9a-z가-힣]/g, '');
const dist = m => m < 1000 ? Math.round(m / 10) * 10 + 'm' : (m / 1000).toFixed(1) + 'km';

function json(obj, status, headers) {
  return new Response(JSON.stringify(obj), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', ...headers } });
}
function withHeaders(res, headers) {
  const r = new Response(res.body, res);
  for (const [k, v] of Object.entries(headers)) r.headers.set(k, v);
  return r;
}
