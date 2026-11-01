(function () {
  var body = document.body;
  var ROOT = body.getAttribute('data-root') || '';
  var API = body.getAttribute('data-api') || '';

  // ---------------------------------------------------------------- score (same as build.py)
  var SCORE_ANCHORS = [[0.01, 99], [0.1, 96], [0.5, 92], [1, 89], [3, 84], [5, 81], [10, 77], [20, 71], [40, 61], [70, 48], [100, 35]];
  function curve(p) {
    p = parseFloat(p);
    if (isNaN(p)) return null;
    p = Math.min(Math.max(p, 0.01), 100);
    var x = Math.log(p) / Math.LN10;
    for (var i = 0; i < SCORE_ANCHORS.length - 1; i++) {
      var a = SCORE_ANCHORS[i], b = SCORE_ANCHORS[i + 1];
      if (p <= b[0]) {
        var la = Math.log(a[0]) / Math.LN10, lb = Math.log(b[0]) / Math.LN10;
        return a[1] + (b[1] - a[1]) * (x - la) / (lb - la);
      }
    }
    return 35;
  }
  function scoreOf(all, ccg) {
    var a = curve(all), c = curve(ccg);
    if (a === null) return 0;
    return Math.round(0.75 * a + 0.25 * (c === null ? a : c));
  }
  function grade(s) { return s >= 90 ? '전국구' : s >= 80 ? '찐맛집' : s >= 70 ? '동네 강자' : s >= 60 ? '꾸준한 집' : '숨은 집'; }
  function pct(v) { var f = parseFloat(v); return isNaN(f) ? '-' : (f < 1 ? f.toFixed(2) : f.toFixed(1)); }
  // 아주 붐비는 집 (same rule as is_busy in build.py)
  function isBusy(all, group) { var p = parseFloat(all); return !isNaN(p) && (p <= 0.5 || (p <= 1 && !!group)); }
  // keep in sync with BUSY_NOTE / BUSY_ROOM* and is_premium() in build.py
  var BUSY_NOTE = '아주 붐비는 집 · 홀은 대화하기엔 시끄러울 수 있어요';
  var BUSY_ROOM_GROUP = '단체석·룸이 있을 가능성이 높으니 예약 때 물어보세요';
  var BUSY_ROOM = '조용한 자리라면 룸이 있는지 확인해 보세요';
  var PREMIUM_KW = /오마카세|omakase|우마카세|다이닝|dining|비스트로|bistro|프렌치|french|(^|[^타])코스|한우|와규|wagyu|스테이크하우스|steak ?house|가이세키|갓포|鮨|철판|teppan|트라토리아|오스테리아|리스토란테|ristorante|셰프|chef|chez|레스토랑|restaurant|스키야키|한정식/i;
  var PREMIUM_NOT = /정육|해장|곱창|국밥|순대|분식|뷔페|식당$/;
  var CHAIN = /(?:^|[^본])점(\)|$)|지점|신세계|현대백화점|롯데|몰|타임스퀘어|스타필드|아이파크|코엑스/;
  var PREMIUM_BAD = ['카페', '분식', '김밥전문점', '치킨', '버거', '피자', '호프', '기타주점', '빵/도넛', '기타베이커리', '샌드위치/토스트', '도시락/죽', '브런치카페/샐러드', '푸드코트', '휴게소', '뷔페'];
  function isPremium(it) {
    var name = cleanName(it.merNm), flat = name.replace(/\s/g, ''), cat = it.merTpbuzNm || '';
    var p = parseFloat(it.allSaleRnk), s = it.srtTime || '', e = it.endTime || '';
    if (PREMIUM_BAD.indexOf(cat) !== -1 || CHAIN.test(flat) || PREMIUM_NOT.test(flat) || isNaN(p) || p > 70) return false;
    if (!s || !e || s < '1100' || e < s || e > '2330') return false;
    var sushi = (cat === '스시/초밥' || cat === '기타일식') && /^스시\s?\S{1,4}$/.test(name);
    return PREMIUM_KW.test(name) || cat === '프랑스음식' || sushi;
  }
  // 프랜차이즈 판정 (build.py 의 brand_of / is_chain 과 같은 방식, 체인 목록은 places.json 에서)
  function brandOf(n) {
    var t = cleanName(n).replace(/\(.*?\)/g, '').trim().split(/\s+/);
    if (t.length >= 2 && /(점|지점|호점|직영점|본점)$/.test(t[t.length - 1])) t.pop();
    var b = t.join('');
    if (t.length === 1) b = b.replace(/([가-힣A-Za-z0-9]{2,}?)([가-힣0-9]{1,8}(역|대|동|로|타워|센터|빌딩|몰|광장|사거리|점)?점)$/, '$1');
    return b.toLowerCase();
  }
  function isChainName(n) {
    if (!data) return false;
    var b = brandOf(n);
    if (!data._chains) { data._chains = {}; (data.chains || []).forEach(function (c) { data._chains[c] = 1; }); }
    return !!data._chains[b] || (data.chainKnown || []).some(function (k) { return b.indexOf(k) === 0; });
  }
  function cleanName(n) {
    var parts = String(n || '').split('/').map(function (x) {
      var hadCorp = /\(주\)|㈜|주식회사/.test(x);
      x = x.replace(/\(주\)|㈜|주식회사|\(유\)|유한회사/g, ' ');
      var m = /\(([^)]{2,})\)\s*$/.exec(x);
      if (m && hadCorp) x = m[1];
      return x.replace(/\s+/g, ' ').trim();
    }).filter(Boolean);
    if (!parts.length) return String(n || '');
    if (parts[0].length <= 2 && parts.length > 1) return parts.slice(1).sort(function (a, b) { return b.length - a.length; })[0];
    return parts[0];
  }
  var MER = 'https://web.paybooc.ai/mer/web/profile/';

  // ---------------------------------------------------------------- sharing
  // Shared links always point at this site (registered hostname), never at eat.pl directly:
  // the friend opens our page and clicks through to eat.pl from here.
  var BASE = new URL(ROOT || './', location.href).href;
  function toast(msg) {
    var t = document.getElementById('toast');
    if (!t) { t = document.createElement('div'); t.id = 'toast'; t.className = 'toast'; document.body.appendChild(t); }
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast.timer); toast.timer = setTimeout(function () { t.hidden = true; }, 2600);
  }
  function share(title, text, url) {
    if (navigator.share) { navigator.share({ title: title, text: text, url: url }).catch(function () {}); return; }
    var full = text + '\n' + url;
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(full).then(function () { toast('링크를 복사했어요. 카톡이나 메신저에 붙여 넣어 보내 보세요.'); },
        function () { window.prompt('이 링크를 복사해 보내세요', url); });
    } else {
      window.prompt('이 링크를 복사해 보내세요', url);
    }
  }
  function cardInfo(c) {
    return { name: c.querySelector('h3').textContent, score: c.querySelector('.score b').textContent, grade: c.querySelector('.score small').textContent };
  }
  document.addEventListener('click', function (e) {
    var m = e.target.closest ? e.target.closest('button.map-btn') : null;
    if (!m) return;
    var c = m.closest('.card');
    var at = c.getAttribute('data-at');
    var target = ROOT + 'map.html#' + (at ? 'at=' + encodeURIComponent(at) : 'p=' + (c.id || '').replace(/^p-/, ''));
    if (location.pathname.slice(-8) === 'map.html' && window.MATJIP && window.MATJIP.focus) { history.replaceState(null, '', target); window.MATJIP.focus(); window.scrollTo(0, 0); }
    else location.href = target;
  });
  document.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('button.share') : null;
    if (!b) return;
    var c = b.closest('.card');
    var i = cardInfo(c);
    var id = (c.id || '').replace(/^p-/, '');
    var url = BASE + (c.getAttribute('data-q') ? '#q=' + encodeURIComponent(c.getAttribute('data-q')) : '#p=' + id);
    share(i.name + ' 매출점수 ' + i.score + '점', i.name + ' 매출점수 ' + i.score + '점(' + i.grade + ') — 별점 말고 BC카드 결제 매출로 매긴 맛집 점수예요. 내가 아는 집은 몇 점일까?', url);
  });
  document.querySelectorAll('button.share-page:not(#pick-share)').forEach(function (b) {
    b.addEventListener('click', function () {
      var h1 = document.querySelector('h1');
      var title = h1 ? h1.textContent.replace(/\s+/g, ' ').trim() : document.title;
      share(title, title + ' — 별점 말고 BC카드 결제 매출로 고른 맛집 순위예요.', location.href.split('#')[0]);
    });
  });

  // ---------------------------------------------------------------- list filters
  var list = document.getElementById('list');
  var navs = document.querySelectorAll('nav.filters[data-key]');
  function applyFilters() {
    if (!list) return;
    var want = {};
    navs.forEach(function (n) {
      var on = n.querySelector('button.on');
      want[n.getAttribute('data-key')] = on ? on.getAttribute('data-v') : '';
    });
    var shown = 0;
    list.querySelectorAll('.card').forEach(function (c) {
      var ok = true;
      for (var k in want) {
        if (!want[k]) continue;
        var vals = k === 'c' ? [c.getAttribute('data-c') || ''] : (c.getAttribute('data-' + k) || '').split(' ');
        if (vals.indexOf(want[k]) === -1) { ok = false; break; }
      }
      c.hidden = !ok;
      if (ok) { shown++; var r = c.querySelector('.rank'); if (r) r.textContent = shown; }
    });
    var count = document.getElementById('count');
    if (count) count.textContent = shown ? shown + '곳' : '조건에 맞는 곳이 없어요. 다른 버튼을 눌러 보세요.';
  }
  navs.forEach(function (n) {
    n.querySelectorAll('button').forEach(function (b) {
      b.addEventListener('click', function () {
        n.querySelectorAll('button').forEach(function (x) { x.classList.remove('on'); });
        b.classList.add('on');
        applyFilters();
      });
    });
  });
  applyFilters();

  // ---------------------------------------------------------------- "오늘 점심 뭐 먹지?" picker
  var pick = document.getElementById('pick');
  var pickShare = document.getElementById('pick-share');
  var picked = [];
  function showPicked(cards) {
    var out = document.getElementById('picked');
    out.innerHTML = '';
    picked = cards;
    cards.forEach(function (c) {
      var a = c.querySelector('a.go');
      var li = document.createElement('li');
      var link = document.createElement('a');
      link.href = a.href; link.target = '_blank'; link.rel = 'noopener';
      link.textContent = c.querySelector('h3').textContent + ' (' + c.querySelector('.cat').textContent + ')';
      var why = document.createElement('span');
      why.className = 'why';
      why.textContent = '매출점수 ' + c.querySelector('.score b').textContent + '점 · ' + c.querySelector('.nums').textContent;
      li.appendChild(link); li.appendChild(why); out.appendChild(li);
    });
    if (pickShare) pickShare.hidden = !cards.length;
    pick.textContent = '다시 뽑기';
  }
  if (pickShare) {
    pickShare.addEventListener('click', function () {
      var names = picked.map(function (c) { return cardInfo(c).name + '(' + cardInfo(c).score + '점)'; });
      var ids = picked.map(function (c) { return c.id.replace(/^p-/, ''); });
      var area = (document.querySelector('h1').firstChild.textContent || '').replace('점심·회식 맛집', '').trim();
      share('오늘 점심 후보 3곳', '오늘 ' + area + ' 점심 후보: ' + names.join(', ') + ' — 카드 매출 상위 집 중에서 뽑았어요. 어디 갈까요?',
        location.href.split('#')[0] + '#pick=' + ids.join(','));
    });
  }
  if (pick && list) {
    var hp = /#pick=([^&]+)/.exec(location.hash);
    if (hp) {
      var fromHash = hp[1].split(',').map(function (id) { return document.getElementById('p-' + id); }).filter(Boolean);
      if (fromHash.length) { showPicked(fromHash); document.getElementById('picker').scrollIntoView(); }
    }
    pick.addEventListener('click', function () {
      var cards = Array.prototype.slice.call(list.querySelectorAll('.card'));
      var pool = cards.filter(function (c) {
        return (c.getAttribute('data-f') || '').split(' ').indexOf('lunch') !== -1 && (c.getAttribute('data-g') || '').indexOf('meal') !== -1
          && c.getAttribute('data-k') !== 'chain';
      }).slice(0, 40);
      if (pool.length < 3) pool = cards.slice(0, 40);
      for (var i = pool.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)); var t = pool[i]; pool[i] = pool[j]; pool[j] = t; }
      showPicked(pool.slice(0, 3));
    });
  }

  // ---------------------------------------------------------------- home: search places + areas
  var q = document.getElementById('q');
  var results = document.getElementById('results');
  var data = null, loading = null;
  function norm(s) { return String(s || '').toLowerCase().replace(/\(주\)|㈜|주식회사/g, '').replace(/[^0-9a-z가-힣]/g, ''); }
  function load() {
    if (data || loading) return loading;
    loading = fetch(ROOT + 'assets/places.json').then(function (r) { return r.json(); }).then(function (d) {
      d.places.forEach(function (p) {
        p.n = norm(p[0]);
        p.hay = p.n + norm(p[3]) + norm(p[2]) + norm(d.groups[p[1]] || '') + norm(d.areas[p[8]] || '');
      });
      data = d;
      return d;
    });
    return loading;
  }
  // 업종 아이콘 (build.py 의 CAT_ICON / GROUP_ICON 과 같은 값)
  var ICONS = { '찜/탕/찌개': '🍲', '국수/면/만두': '🍜', '일식라멘': '🍜', '마라탕': '🌶️', '치킨': '🍗', '피자': '🍕', '버거': '🍔',
    '분식': '🍢', '떡볶이전문점': '🍢', '김밥전문점': '🍙', '도시락/죽': '🍱', '샌드위치/토스트': '🥪', '브런치카페/샐러드': '🥗',
    '족발/보쌈': '🍖', '곱창전골/구이': '🍖', '양꼬치': '🍢', '스시/초밥': '🍣', '일식돈가스': '🍛', '이탈리아음식': '🍝',
    '멕시칸': '🌮', '아시안': '🍜', '프랑스음식': '🍷', '바(bar)': '🍸', '이자카야': '🏮', '호프': '🍺', '기타주점': '🍶',
    '빵/도넛': '🥐', '기타베이커리': '🥐', '아이스크림/요거트/빙수': '🍨', '떡/한과': '🍡', '뷔페': '🍽️', '해산물구이/찜': '🦀',
    '백반/한정식': '🍚', '기타한식': '🍚', '전/부침개': '🍚', '반찬가게': '🍚', '고기구이': '🥩', '횟집': '🐟', '기타일식': '🍣',
    '기타중식': '🥟', '기타외국식': '🍝', '푸드코트': '🍔', '휴게소': '🍔', '카페': '☕', '체험카페': '☕', '비건/채식/사찰': '🥗' };
  function iconOf(cat) { return ICONS[cat] || '🍽️'; }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
  function placeCard(p) {
    // p: {name, cat, addr, score, all, ccg, url, area, pick, busy, note, q}
    var li = el('li', 'card');
    li.id = 'p-' + String(p.url || '').split('/').pop();
    if (p.q) li.setAttribute('data-q', p.q);
    var head = el('div', 'card-head');
    var ic = el('span', 'ico', iconOf(p.cat)); ic.setAttribute('aria-hidden', 'true'); head.appendChild(ic);
    head.appendChild(el('h3', '', p.name));
    head.appendChild(el('span', 'cat', p.cat || '기타'));
    var s = el('span', 'score'); s.appendChild(el('b', '', String(p.score))); s.appendChild(document.createTextNode('점')); s.appendChild(el('small', '', grade(p.score)));
    head.appendChild(s);
    li.appendChild(head);
    if (p.note) li.appendChild(el('p', 'meta where', p.note));
    var nums = el('p', 'nums'); nums.appendChild(el('strong', '', '전국 같은 업종 매출 상위 ' + pct(p.all) + '%')); nums.appendChild(document.createTextNode(' · 구 안에서 상위 ' + pct(p.ccg) + '%'));
    li.appendChild(nums);
    if (p.busy) li.appendChild(el('p', 'note busy', BUSY_NOTE + ' · ' + (p.group ? BUSY_ROOM_GROUP : BUSY_ROOM)));
    if (p.premium && p.score < 75) li.appendChild(el('p', 'note premium', '가격대가 높은 소규모 업장으로 보여요 · 손님 수가 적어 매출점수가 낮게 나오는 유형이에요'));
    var m = el('p', 'meta', p.addr || '');
    if (p.area && data && data.areas[p.area]) {
      m.appendChild(document.createTextNode(' · '));
      var al = el('a', '', data.areas[p.area] + ' 순위 보기'); al.href = ROOT + 'area/' + p.area + '.html'; m.appendChild(al);
    }
    li.appendChild(m);
    if (p.pick) li.appendChild(el('span', 'tag pick', '주인장 픽'));
    if (p.premium) li.appendChild(el('span', 'tag premium', '고급 추정'));
    if (p.chain) li.appendChild(el('span', 'tag chain', '프랜차이즈'));
    if (p.talk) li.appendChild(el('span', 'tag talk', '대화하기 좋은 곳'));
    var act = el('div', 'actions');
    var go = el('a', 'go', 'eat.pl에서 메뉴·리뷰 요약 보기 →'); go.href = p.url; go.target = '_blank'; go.rel = 'noopener';
    act.appendChild(go);
    if (p.lat) {
      var mb = el('button', 'map-btn', '지도'); mb.type = 'button';
      if (p.live) { li.setAttribute('data-at', p.lat + ',' + p.lng + ',' + p.name); }
      act.appendChild(mb);
    }
    var sb = el('button', 'share', '공유'); sb.type = 'button'; act.appendChild(sb);
    li.appendChild(act);
    return li;
  }
  function fromIndex(p) { return { name: p[0], cat: p[2], addr: p[3], score: p[4], all: p[5], ccg: p[6], url: (p[7].indexOf('https://') === 0 ? '' : MER) + p[7], area: p[8], pick: p[9], busy: p[10], talk: p[11], premium: p[12], group: p[13], lat: p[14], lng: p[15], chain: p[16] }; }

  function liveButton(term, box) {
    if (!API) {
      box.appendChild(el('p', 'hint', '아직 모아 둔 데이터에 없는 가게예요. 가게 이름과 동네를 알려 주시면 다음 업데이트 때 추가할게요.'));
      return;
    }
    var b = el('button', 'live', '"' + term + '" 실시간으로 찾아보기');
    b.type = 'button';
    b.onclick = function () {
      b.disabled = true; b.textContent = '찾는 중… (최대 10초)';
      if (box.querySelector('.hint')) box.querySelector('.hint').remove();
      fetch(API + '/?q=' + encodeURIComponent(term)).then(function (r) { return r.json(); }).then(function (d) {
        var ul = el('ol', 'list');
        var found = 0;
        (d.results || []).forEach(function (x) {
          if (!x.item || !x.item.merUrl) return;
          var it = x.item; found++;
          ul.appendChild(placeCard({ name: cleanName(it.merNm), cat: it.merTpbuzNm, addr: String(it.merAddr || '').replace(/\(.*?\)/g, '').split(',')[0].trim(),
            score: scoreOf(it.allSaleRnk, it.ccgSaleRnk), all: it.allSaleRnk, ccg: it.ccgSaleRnk, url: it.merUrl, note: '실시간 조회 결과',
            busy: isBusy(it.allSaleRnk, it.empyVsitYn), group: !!it.empyVsitYn, premium: isPremium(it), q: term,
            lat: it.lat, lng: it.lng, live: true, chain: isChainName(it.merNm) }));
        });
        b.remove();
        if (found) { box.appendChild(el('p', 'count', 'BC카드 결제 데이터에서 ' + found + '곳을 찾았어요')); box.appendChild(ul); }
        else box.appendChild(el('p', 'hint', d.message || 'BC카드 결제 데이터에서 이 가게를 찾지 못했어요. 이름을 조금 다르게(지점명 포함) 검색해 보세요.'));
      }).catch(function () { b.disabled = false; b.textContent = '다시 시도'; });
    };
    box.appendChild(b);
  }

  var timer = null;
  function search() {
    var v = q.value.trim();
    // area tiles
    document.querySelectorAll('.tile').forEach(function (t) {
      t.hidden = !!v && (t.getAttribute('data-name') || '').indexOf(v) === -1;
    });
    document.querySelectorAll('.city').forEach(function (s) { s.hidden = !s.querySelector('.tile:not([hidden])'); });
    var areasBox = document.getElementById('areas');
    if (areasBox) areasBox.hidden = !areasBox.querySelector('.city:not([hidden])');
    if (!results) return;
    results.innerHTML = '';
    if (v.length < 2) return;
    load().then(function (d) {
      if (q.value.trim() !== v) return;
      var toks = v.split(/\s+/).map(norm).filter(Boolean);
      var whole = norm(v);
      var hits = d.places.filter(function (p) {
        return toks.every(function (t) { return p.hay.indexOf(t) !== -1; });
      });
      hits.sort(function (a, b) {
        var ra = a.n === whole ? 0 : a.n.indexOf(whole) === 0 ? 1 : a.n.indexOf(whole) !== -1 ? 2 : 3;
        var rb = b.n === whole ? 0 : b.n.indexOf(whole) === 0 ? 1 : b.n.indexOf(whole) !== -1 ? 2 : 3;
        return ra - rb || b[4] - a[4];
      });
      results.innerHTML = '';
      var nameHit = hits.length && hits[0].n.indexOf(whole) !== -1;
      if (hits.length) {
        results.appendChild(el('p', 'count', hits.length > 30 ? '검색 결과 ' + hits.length + '곳 중 매출점수 상위 30곳' : '검색 결과 ' + hits.length + '곳'));
        var ul = el('ol', 'list');
        hits.slice(0, 30).forEach(function (p) { ul.appendChild(placeCard(fromIndex(p))); });
        results.appendChild(ul);
      } else {
        results.appendChild(el('p', 'count', '모아 둔 ' + d.places.length.toLocaleString() + '곳 중에는 없어요.'));
      }
      if (!nameHit) liveButton(v, results);
    });
  }
  if (q) {
    q.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(search, 180); });
    q.addEventListener('focus', load, { once: true });
    var fromUrl = function () {
      var hp = /#p=([^&]+)/.exec(location.hash);
      var hq = /[#?&]q=([^&]+)/.exec(location.hash) || /[?&]q=([^&]+)/.exec(location.search);
      if (hp) { showShared(hp[1]); return; }
      if (hq) { try { q.value = decodeURIComponent(hq[1].replace(/\+/g, ' ')); search(); } catch (e) { /* ignore */ } }
    };
    window.addEventListener('hashchange', fromUrl);
    fromUrl();
  }
  // a friend shared one place: show it on top, then invite them to search their own
  function showShared(id) {
    load().then(function (d) {
      var p = null;
      for (var i = 0; i < d.places.length; i++) if (d.places[i][7] === id) { p = d.places[i]; break; }
      results.innerHTML = '';
      if (!p) { results.appendChild(el('p', 'hint', '공유받은 가게를 찾지 못했어요. 이름으로 검색해 보세요.')); return; }
      var box = el('div', 'shared');
      box.appendChild(el('p', 'shared-title', '공유받은 맛집'));
      var ul = el('ol', 'list'); ul.appendChild(placeCard(fromIndex(p))); box.appendChild(ul);
      box.appendChild(el('p', 'hint', '내가 아는 집은 몇 점일까요? 위 검색창에 가게 이름을 입력해 보세요.'));
      results.appendChild(box);
      document.getElementById('search').scrollIntoView();
    });
  }

  // shared helpers for map.js
  window.MATJIP = { ROOT: ROOT, API: API, load: load, placeCard: placeCard, fromIndex: fromIndex, scoreOf: scoreOf,
    isBusy: isBusy, isPremium: isPremium, cleanName: cleanName, el: el, iconOf: iconOf, isChainName: isChainName, data: function () { return data; } };

  // ---------------------------------------------------------------- in-app browser notice
  // links opened inside chat apps may not keep the visit path
  var ua = navigator.userAgent || '';
  var box = document.getElementById('inapp');
  if (!box) return;
  var msg = null, action = null;
  if (/KAKAOTALK/i.test(ua)) {
    msg = '카카오톡 안에서 보고 있어요. 기본 브라우저로 열면 더 잘 보여요.';
    action = function () { location.href = 'kakaotalk://web/openExternal?url=' + encodeURIComponent(location.href); };
  } else if (/LinkedInApp|Instagram|FBAN|FBAV|Line\/|NAVER\(inapp|everytimeApp/i.test(ua)) {
    msg = '앱 안에서 보고 있어요. 오른쪽 위 메뉴(⋯)에서 "브라우저로 열기"를 누르면 더 잘 보여요.';
  }
  if (!msg) return;
  box.innerHTML = '';
  var span = document.createElement('span'); span.textContent = msg; box.appendChild(span);
  if (action) { var b = document.createElement('button'); b.textContent = '브라우저로 열기'; b.onclick = action; box.appendChild(b); }
  var x = document.createElement('button'); x.className = 'x'; x.textContent = '닫기'; x.onclick = function () { box.hidden = true; }; box.appendChild(x);
  box.hidden = false;
})();
