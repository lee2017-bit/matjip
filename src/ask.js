(function () {
  // '물어보기' 페이지: 질문 → Worker(/chat parse: AI가 장소·시간·원하는 것을 뽑고 장소 좌표를 찾음)
  // → 이 사이트 데이터(places.json) + 그 근처 실시간 eat.pl 가게로 후보를 만든다
  // → Worker(/chat answer: AI가 후보 번호만 골라 이유를 씀) → 카드는 데이터 그대로(merUrl 그대로) 그린다.
  var M = window.MATJIP;
  var form = document.getElementById('ask-form');
  if (!M || !form || !M.API) return;
  var input = document.getElementById('ask-q'), out = document.getElementById('ask-out'), go = document.getElementById('ask-go');
  var el = M.el;

  function post(body) {
    return fetch(M.API + '/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { j._status = r.status; return j; }); });
  }
  function withTimeout(p, ms) {
    return Promise.race([p, new Promise(function (_, rej) { setTimeout(function () { rej(new Error('timeout')); }, ms); })]);
  }
  function dist(a, b) {
    var R = 6371000, dLat = (b.lat - a.lat) * Math.PI / 180, dLng = (b.lng - a.lng) * Math.PI / 180;
    var x = Math.sin(dLat / 2) * Math.sin(dLat / 2) + Math.cos(a.lat * Math.PI / 180) * Math.cos(b.lat * Math.PI / 180) * Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return 2 * R * Math.asin(Math.sqrt(x));
  }
  function fmtDist(m) { return m < 1000 ? Math.round(m / 10) * 10 + 'm' : (m / 1000).toFixed(1) + 'km'; }
  function hhmm(s) { return /^\d{4}$/.test(s || '') ? s.slice(0, 2) + ':' + s.slice(2) : ''; }
  function addMin(t, m) { var h = +t.slice(0, 2), mm = +t.slice(2) + m; h = (h + Math.floor(mm / 60)) % 24; mm %= 60; return (h < 10 ? '0' : '') + h + (mm < 10 ? '0' : '') + mm; }
  function openAtRaw(s, e, t) {
    if (!/^\d{4}$/.test(s || '') || !/^\d{4}$/.test(e || '')) return true;
    return e < s ? (t >= s || t <= e) : (s <= t && t <= e);
  }
  // places.json 의 영업 비트 (build.py open_at: 09:00·12:30·18:30·22:30)
  function timeBit(t) { var m = +t.slice(0, 2) * 60 + +t.slice(2); return m < 630 ? 32768 : m < 900 ? 65536 : m < 1260 ? 131072 : 262144; }

  // 원하는 음식 낱말 → 업종·이름에서 찾을 말
  var FOOD = [
    [/국밥|국물|탕|찌개|해장|전골|곰탕|설렁탕|순대|따뜻/, /탕|찌개|국밥|국수|전골|순대|해장|곰탕|설렁탕|백숙|칼국수/, ''],
    [/고기|삼겹|갈비|한우|소고기|돼지|구이|불고기|바베큐|숯불/, /고기|구이|갈비|육류|정육|삼겹|불고기|숯불/, 'meat'],
    [/회|해산물|조개|생선|수산|해물/, /회|횟집|해산물|조개|수산|해물|생선|아구|대게/, 'seafood'],
    [/초밥|스시|일식|오마카세|돈까스|돈가스|카츠/, /스시|초밥|일식|돈까스|돈가스|카츠/, 'japanese'],
    [/면|국수|칼국수|냉면|라멘|우동|소바|막국수/, /면|국수|라멘|우동|소바|냉면/, ''],
    [/중식|중국|짜장|짬뽕|마라|훠궈|양꼬치/, /중식|중국|짬뽕|반점|마라|훠궈|양꼬치|각$/, 'chinese'],
    [/양식|파스타|피자|스테이크|이탈리|브런치|버거/, /양식|파스타|피자|스테이크|이탈리|브런치|버거|비스트로/, 'western'],
    [/치킨|닭|통닭/, /치킨|닭|통닭/, ''],
    [/백반|한식|밥|정식|한정식|집밥/, /백반|한식|한정식|밥|정식/, 'korean'],
    [/분식|떡볶이|김밥/, /분식|떡볶이|김밥/, ''],
    [/카페|커피|디저트|빵|케이크|베이커리/, /카페|커피|디저트|빵|케이크|베이커리|도넛/, 'cafe'],
    [/술|맥주|와인|이자카야|포차|하이볼|바/, /주점|호프|이자카야|포차|바\(bar\)|와인|맥주/, 'drink']
  ];
  function foodMatch(words, p) {
    var hay = (p.cat || '') + ' ' + (p.name || '');
    return (words || []).some(function (w) {
      return FOOD.some(function (f) { return f[0].test(w) && (f[1].test(hay) || (f[2] && f[2] === p.g)); }) || (w.length >= 2 && hay.indexOf(w) !== -1);
    });
  }

  // 실시간 가게는 저장된 프랜차이즈 목록에 없는 브랜드도 많다: '갑짬뽕 처인구점', '○○에버랜드점'처럼 지점 이름이면 프랜차이즈로 본다
  var BRANCH = /(점|지점|호점|직영점)$/, NOT_BRANCH = /(본점|반점|정육점|주점|상점|만점)$/;
  function liveChain(it) {
    if (M.isChainName(it.merNm)) return true;
    var n = M.cleanName(it.merNm).replace(/\(.*?\)/g, '').trim(), last = n.split(/\s+/).pop() || '';
    return BRANCH.test(last) && !NOT_BRANCH.test(last) && n.replace(/\s/g, '').length >= 5;
  }
  function fromLive(it) {
    var c = M.custTags(it), chain = liveChain(it);
    return { name: M.cleanName(it.merNm), cat: it.merTpbuzNm, addr: String(it.merAddr || '').replace(/\(.*?\)/g, '').split(',')[0].trim(),
      score: M.scoreOf(it.allSaleRnk, it.ccgSaleRnk), all: it.allSaleRnk, ccg: it.ccgSaleRnk, url: it.merUrl,
      busy: M.isBusy(it.allSaleRnk, it.empyVsitYn), group: !!it.empyVsitYn, premium: M.isPremium(it),
      lat: it.lat, lng: it.lng, live: true, g: M.catGroupOf(it.merTpbuzNm), chain: chain,
      ages: c.ages, sex: c.sex, taste: !chain && M.isTaste(it), srt: it.srtTime || '', end: it.endTime || '',
      hours: hhmm(it.srtTime) && hhmm(it.endTime) ? hhmm(it.srtTime) + '–' + hhmm(it.endTime) : '' };
  }
  function fromStored(p) { var o = M.fromIndex(p); o.bits = p[18] || 0; return o; }

  function candidates(d, live, at, intent) {
    var seen = {}, pool = [];
    function add(p) {
      var id = String(p.url || '').split('/').pop();
      if (!id || seen[id] || !p.lat) return;
      seen[id] = 1; p.d = dist(at, p); pool.push(p);
    }
    // 저장된 데이터(주인장 픽·취향 표시가 있는 쪽)를 먼저 넣고, 실시간 가게로 채운다
    d.places.forEach(function (row) { if (row[14] && Math.abs(row[14] - at.lat) < 0.12 && Math.abs(row[15] - at.lng) < 0.15) add(fromStored(row)); });
    live.forEach(function (it) { if (it.merUrl && it.lat) add(fromLive(it)); });
    var t = (intent.time || '').replace(':', '');
    var ok = pool.filter(function (p) {
      if (p.d > 12000) return false;
      if (p.chain && !intent.chain_ok) return false;
      if (intent.kind === 'cafe' ? p.g !== 'cafe' : intent.kind === 'drink' ? ['drink', 'meat', 'seafood', 'japanese', 'chinese'].indexOf(p.g) === -1
        : (p.g === 'cafe' || p.g === 'drink')) return false;
      if (t) {
        if (p.live) { if (!openAtRaw(p.srt, p.end, t) || !openAtRaw(p.srt, p.end, addMin(t, 45))) return false; }
        else if (!(p.bits & timeBit(t))) return false;
      }
      if (intent.avoid && intent.avoid.length && foodMatch(intent.avoid, p)) return false;
      // 소개팅·데이트: 주 고객 1·2위가 모두 20~30대이고 모두 여성인 곳만 (주인장 픽이어도 예외 없음)
      if (intent.date && !(youngF(p))) return false;
      return true;
    });
    // 반경을 넓혀 가며 후보가 14곳 이상 모이는 가장 가까운 범위를 쓴다
    var radius = [1000, 2000, 3500, 6000, 9000, 12000].filter(function (r) {
      return ok.filter(function (p) { return p.d <= r; }).length >= 14;
    })[0] || 12000;
    // 주인장 픽은 조금 멀어도(반경의 3배: 도심 3km, 교외 6km 안팎) 후보에 넣는다 — 주인장이 그 동네에서 직접 모아 둔 집이라서
    // 소개팅·데이트는 주인장 픽을 챙기지 않는다
    var pickR = intent.date ? radius : Math.min(Math.max(radius * 3, 2000), 12000);
    ok = ok.filter(function (p) { return p.d <= radius || (p.pick && p.d <= pickR); });
    ok.forEach(function (p) {
      var mine = !intent.date && (p.pick ? 14 : 0) + (p.taste ? 8 : 0);
      var r = p.score + mine - 10 * p.d / (p.pick ? pickR : radius);
      if (intent.date) r += (p.talk ? 10 : 0) + (p.premium ? 4 : 0) - (p.busy ? 12 : 0) - (p.group ? 5 : 0) - (p.g === 'quick' ? 8 : 0);
      if (intent.foods && intent.foods.length && foodMatch(intent.foods, p)) r += 10;
      if (intent.talk) r += (p.talk ? 8 : 0) - (p.busy ? 10 : 0);
      if (intent.premium && p.premium) r += 10;
      if (intent.group && p.group) r += 6;
      if (intent.young && (p.ages || []).indexOf('young') !== -1) r += 6;
      if (intent.female && p.sex === 'f') r += 6;
      p.rank = r;
    });
    ok.sort(function (a, b) { return b.rank - a.rank; });
    return { list: ok, radius: radius };
  }
  function youngF(p) { return (p.ages || []).indexOf('young') !== -1 && p.sex === 'f'; }
  function tagsOf(p, date) {
    var yf = youngF(p);
    return [['주인장 픽', p.pick && !date], ['주인장 취향', p.taste && !p.pick && !date], ['20~30대 여성 많음', yf], ['대화하기 좋은 곳', p.talk], ['고급', p.premium],
      ['회식', p.group], ['아주 붐빔', p.busy], ['20~30대 많음', !yf && (p.ages || []).indexOf('young') !== -1], ['여성 손님 많음', !yf && p.sex === 'f']]
      .filter(function (x) { return x[1]; }).map(function (x) { return x[0]; });
  }

  function status(msg) {
    var s = out.querySelector('.ask-status');
    if (!s) { s = el('p', 'ask-status'); out.appendChild(s); }
    s.textContent = msg;
  }
  function fail(msg) {
    out.innerHTML = '';
    out.appendChild(el('p', 'hint', msg));
    go.disabled = false;
  }
  function card(p, why) {
    var c = M.placeCard({ name: p.name, cat: p.cat, g: p.g, addr: p.addr, score: p.score, all: p.all, ccg: p.ccg, url: p.url, area: p.area,
      pick: p.pick, taste: p.taste, talk: p.talk, premium: p.premium, busy: p.busy, group: p.group, chain: p.chain,
      lat: p.lat, lng: p.lng, live: p.live });
    c.querySelector('.sub').appendChild(document.createTextNode(' · ' + fmtDist(p.d) + (p.hours ? ' · ' + p.hours : '')));
    if (why) {
      var w = el('p', 'why', why);
      c.insertBefore(w, c.querySelector('.sub').nextSibling);
    }
    return c;
  }

  function run(q, here) {
    q = q.replace(/\s+/g, ' ').trim();
    if (q.length < 4) { fail('장소와 상황을 조금 더 자세히 적어 주세요. 예) 성수동에서 조용히 대화하기 좋은 저녁'); return; }
    if (!here && !/[#&]fresh/.test(location.hash)) {
      if (!presets) { presetsLoad.then(function () { run(q, here); }); return; }
      var pre = presets[qkey(q)];
      if (pre) { var r = JSON.parse(JSON.stringify(pre)); r.q = q; render(r); return; }
    }
    go.disabled = true; out.innerHTML = '';
    var qline = el('p', 'ask-q-line', q); out.appendChild(qline);
    status('질문을 읽는 중…');
    var slow = setTimeout(function () { if (out.querySelector('.ask-status')) status('AI가 생각하는 중… (20초쯤 걸릴 수 있어요)'); }, 8000);
    var loadData = M.load();
    var parsed = here ? Promise.resolve({ intent: here.intent, place: { name: '내 위치', lat: here.lat, lng: here.lng, addr: '' } }) : withTimeout(post({ step: 'parse', q: q }), 45000);
    parsed.then(function (r) {
      if (r._status && r._status !== 200) { fail(r.message || 'AI가 지금 답하지 못했어요. 잠시 뒤 다시 물어봐 주세요.'); return; }
      clearTimeout(slow);
      var intent = r.intent || {}, place = r.place;
      if (/소개팅|데이트|기념일|연인|커플|여자 ?친구|여친|남자 ?친구|남친/.test(q)) intent.date = true;
      if (!place) { askWhere(q, intent); return; }
      status('"' + place.name + '" 근처 가게를 모으는 중… (최대 15초)');
      var live = withTimeout(fetch(M.API + '/?lat=' + place.lat + '&lng=' + place.lng).then(function (x) { return x.json(); }), 15000)
        .then(function (j) { return (j.results || []).map(function (x) { return x.item; }).filter(Boolean); }, function () { return []; });
      return Promise.all([loadData, live]).then(function (v) {
        var c = candidates(v[0], v[1], place, intent);
        if (!c.list.length) { fail('"' + place.name + '" 근처에서 ' + (intent.date ? '20~30대 여성 손님이 많은 ' : '') + '조건에 맞는 가게를 찾지 못했어요. 시간이나 조건을 줄여서 다시 물어봐 주세요.'); return; }
        var top = c.list.slice(0, 18);
        status('후보 ' + top.length + '곳 중에서 고르는 중…');
        var cands = top.map(function (p) { return { n: p.name, c: p.cat, s: p.score, d: Math.round(p.d), t: tagsOf(p, intent.date), h: p.hours || '' }; });
        return withTimeout(post({ step: 'answer', q: q, intent: intent, place: { name: place.name }, cands: cands }), 45000)
          .catch(function () { return { _status: 0 }; })
          .then(function (a) { show(q, intent, place, c, top, a); });
      });
    }).catch(function () { fail('AI가 지금 답하지 못했어요. 잠시 뒤 다시 물어봐 주세요.'); });
  }

  function askWhere(q, intent) {
    out.innerHTML = '';
    out.appendChild(el('p', 'ask-q-line', q));
    out.appendChild(el('p', 'hint', '어느 근처인지 알려 주세요. 장소를 넣어 다시 묻거나 내 위치로 찾을 수 있어요.'));
    if (navigator.geolocation) {
      var b = el('button', 'live', '📍 내 위치 근처로 찾기'); b.type = 'button';
      b.onclick = function () {
        b.disabled = true; b.textContent = '위치 확인 중…';
        navigator.geolocation.getCurrentPosition(function (pos) {
          run(q, { intent: intent, lat: +pos.coords.latitude.toFixed(5), lng: +pos.coords.longitude.toFixed(5) });
        }, function () { b.disabled = false; b.textContent = '위치를 확인하지 못했어요 · 다시 시도'; }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 });
      };
      out.appendChild(b);
    }
    go.disabled = false;
  }

  function show(q, intent, place, c, top, a) {
    var picks = (a && a.picks && a.picks.length) ? a.picks.filter(function (x) { return top[x.i]; }) : [];
    var msg = a && a.answer;
    var ai = !!picks.length;
    if (!ai) {
      msg = a && a._status === 429 ? (a.message || '오늘 물어볼 수 있는 횟수를 다 썼어요.') + ' 대신 점수·거리로 고른 후보를 보여 드려요.'
        : 'AI 답을 만들지 못해서 매출점수·주인장 픽·거리로 고른 후보를 보여 드려요.';
      picks = top.slice(0, 3).map(function (p, i) { return { i: i, why: '' }; });
    }
    var used = {}; picks.forEach(function (x) { used[x.i] = 1; });
    var res = { q: q, intent: intent, place: { name: place.name, lat: place.lat, lng: place.lng }, radius: c.radius, count: c.list.length, answer: msg || '',
      picks: picks.map(function (x) { return slim(top[x.i], x.why); }), rest: top.filter(function (_, i) { return !used[i]; }).slice(0, 6).map(function (p) { return slim(p, ''); }) };
    if (ai) window.__askResult = res;  // tools/ask_presets.js 가 예시 질문의 답을 미리 만들 때 읽는다
    render(res);
  }
  // 카드에 필요한 값만 (미리 만든 답 파일에도 이 모양으로 저장)
  var SLIM = ['name', 'cat', 'g', 'addr', 'score', 'all', 'ccg', 'url', 'area', 'pick', 'taste', 'talk', 'premium', 'busy', 'group', 'chain', 'lat', 'lng', 'live', 'hours'];
  function slim(p, why) {
    var o = { d: Math.round(p.d), why: why || '' };
    SLIM.forEach(function (k) { if (p[k] !== undefined && p[k] !== '' && p[k] !== 0 && p[k] !== false) o[k] = p[k]; });
    return o;
  }
  function render(res) {
    var intent = res.intent || {}, place = res.place;
    out.innerHTML = '';
    out.appendChild(el('p', 'ask-q-line', res.q));
    var box = el('div', 'ask-answer');
    if (res.answer) box.appendChild(el('p', 'a', res.answer));
    var meta = el('p', 'ask-meta');
    meta.appendChild(document.createTextNode('기준 ' + place.name + (intent.time ? ' · ' + intent.time + ' 영업' : '') + (intent.date ? ' · 20~30대 여성 손님이 많은 곳만' : '')
      + ' · ' + fmtDist(res.radius) + ' 안 ' + res.count + '곳 중 · '));
    var ml = el('a', '', '지도'); ml.href = M.ROOT + 'map.html#at=' + encodeURIComponent(place.lat + ',' + place.lng + ',' + place.name); meta.appendChild(ml);
    box.appendChild(meta);
    out.appendChild(box);
    var ol = el('ol', 'list');
    res.picks.forEach(function (p) { ol.appendChild(card(p, p.why)); });
    out.appendChild(ol);
    if (res.rest.length) {
      var more = el('details', 'ask-more'); more.appendChild(el('summary', '', '다른 후보 ' + res.rest.length + '곳 더 보기'));
      var ol2 = el('ol', 'list'); res.rest.forEach(function (p) { ol2.appendChild(card(p, '')); }); more.appendChild(ol2);
      out.appendChild(more);
    }
    var sb = el('button', 'share-page', '이 질문 공유'); sb.type = 'button';
    sb.onclick = function () {
      var url = new URL(M.ROOT + 'ask.html', location.href).href + '#q=' + encodeURIComponent(res.q);
      var names = res.picks.map(function (p) { return p.name; }).join(', ');
      M.share('맛집 물어보기', '"' + res.q + '" → ' + names + ' — 카드 결제 매출로 고른 맛집이에요.', url);
    };
    out.appendChild(sb);
    go.disabled = false;
  }

  // 예시 질문은 답을 미리 만들어 둔 파일(assets/ask-presets.json)에서 바로 보여 준다 (AI 사용량 0, 기다림 없음)
  var presets = null;
  var presetsLoad = fetch(M.ROOT + 'assets/ask-presets.json').then(function (r) { return r.ok ? r.json() : {}; }).catch(function () { return {}; })
    .then(function (j) { presets = (j && j.answers) || {}; return presets; });
  function qkey(q) { return String(q || '').toLowerCase().replace(/[^0-9a-z가-힣]/g, ''); }

  form.addEventListener('submit', function (e) { e.preventDefault(); run(input.value, null); });
  input.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); run(input.value, null); } });
  var exBox = document.querySelector('.ask-ex');
  if (exBox && exBox.getAttribute('data-show')) {  // 홈: 예시 몇 개만 골라 매번 다르게
    var bs = Array.prototype.slice.call(exBox.querySelectorAll('button')), n = +exBox.getAttribute('data-show');
    for (var i = bs.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)); var t = bs[i]; bs[i] = bs[j]; bs[j] = t; }
    bs.forEach(function (b, k) { b.hidden = k >= n; exBox.appendChild(b); });
  }
  document.querySelectorAll('.ask-ex button').forEach(function (b) {
    b.addEventListener('click', function () { input.value = b.textContent; run(input.value, null); });
  });
  // #ask=질문 (홈 검색창의 '물어보기' 링크), ask.html 은 #q=질문 도 (홈의 #q= 는 가게 이름 검색 공유 링크라 쓰지 않는다)
  function fromHash() {
    var m = /[#&]ask=([^&]+)/.exec(location.hash) || (!document.getElementById('q') && /[#&]q=([^&]+)/.exec(location.hash));
    if (!m) return;
    try { input.value = decodeURIComponent(m[1]); } catch (e) { return; }
    form.scrollIntoView({ behavior: 'smooth', block: 'start' });
    run(input.value, null);
  }
  window.addEventListener('hashchange', fromHash);
  fromHash();
})();
