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
  var BUSY_NOTE = '붐비는 집이라 홀은 시끄러울 수 있어요';
  var BUSY_ROOM_GROUP = '룸은 예약 때 물어보세요';
  var BUSY_ROOM = '';
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
  function brandParts(n) {
    var t = cleanName(n).replace(/\(.*?\)/g, '').trim().split(/\s+/);
    var branch = t.length >= 2 && /(점|지점|호점|직영점|본점)$/.test(t[t.length - 1]);
    if (branch) t.pop();
    return [t.join('').toLowerCase(), branch];
  }
  function isChainName(n) {
    var raw = String(n || '').replace(/\s/g, '');
    if (OPERATORS.some(function (o) { return raw.indexOf(o[0].replace(/\s/g, '')) !== -1; })) return true;
    if (!data) return false;
    var b = brandParts(n)[0];
    if (!data._chains) { data._chains = {}; (data.chains || []).forEach(function (c) { data._chains[c] = 1; }); }
    if (data._chains[b] || (data.chainKnown || []).some(function (k) { return b.indexOf(k) === 0 || (k.length >= 3 && b.indexOf(k) !== -1); })) return true;
    if (/(점|지점|호점|직영점|본점)$/.test(b) && (data.chains || []).some(function (f) { return f.length >= 3 && b.indexOf(f) === 0; })) return true;
    var first = (cleanName(n).replace(/\(.*?\)/g, '').trim().split(/\s+/)[0] || '').toLowerCase();
    return first.length >= 3 && !!data._chains[first];
  }
  function cleanName(n) {
    var parts = String(n || '').split('/').map(function (x) {
      var hadCorp = /\(주\)|㈜|주식회사/.test(x);
      x = x.replace(/\(주\)|㈜|주식회사|\(유\)|유한회사/g, ' ');
      var m = /\(([^)]{2,})\)\s*$/.exec(x);
      if (m && hadCorp) {  // 괄호 안이 지점·지역 이름뿐이면 회사명이 곧 가게 이름 (build.py clean_name 과 같은 규칙)
        var inner = m[1].trim(), base = x.slice(0, m.index).trim();
        x = base && (/^\S{1,4}(점|지점|호점|직영점)$/.test(inner) || inner === '카페' || inner === '식당') ? base + (/점$/.test(inner) ? ' ' + inner : '') : inner;
      }
      return x.replace(/\s+/g, ' ').trim();
    }).filter(Boolean);
    if (!parts.length) return String(n || '');
    var name = parts[0].length <= 2 && parts.length > 1 ? parts.slice(1).sort(function (a, b) { return b.length - a.length; })[0] : parts[0];
    for (var i = 0; i < OPERATORS.length; i++) {
      var op = OPERATORS[i][0];
      if (name.indexOf(op) === 0 && name.length > op.length + 1) { name = (OPERATORS[i][1] + name.slice(op.length)).trim(); break; }
    }
    return name;
  }
  // 프랜차이즈 본사·운영사 이름 (build.py OPERATORS 와 같은 값)
  var OPERATORS = [['다이닝브랜즈 그룹', ''], ['다이닝브랜즈그룹', ''], ['비케이알', ''], ['한국맥도날드', '맥도날드'], ['롯데지알에스', ''],
    ['신세계푸드', ''], ['씨제이푸드빌', ''], ['이랜드이츠', ''], ['제너시스', ''], ['썬앳푸드', ''], ['갓텐코리아', '']];
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

  // ---------------------------------------------------------------- filters
  // 버튼 줄(.toggles): 여러 개를 함께 켤 수 있다 (모두 만족하는 집만). '전체'는 모두 끄기.
  // 드롭다운(select[data-key]): 하나만 고른다. 지도 페이지도 같은 방식 (map.js 가 onFilter 로 받는다).
  function bindToggles(nav, onChange) {
    var btns = nav.querySelectorAll('button');
    btns.forEach(function (b) {
      b.addEventListener('click', function () {
        var v = b.getAttribute('data-v');
        if (!v) btns.forEach(function (x) { x.classList.toggle('on', !x.getAttribute('data-v')); });
        else {
          b.classList.toggle('on');
          var any = nav.querySelector('button.on[data-v]:not([data-v=""])');
          nav.querySelector('button[data-v=""]').classList.toggle('on', !any);
        }
        onChange();
      });
    });
  }
  function toggleValues(nav) {
    return Array.prototype.map.call(nav.querySelectorAll('button.on'), function (b) { return b.getAttribute('data-v'); }).filter(Boolean);
  }
  // 기본값이 아닌 드롭다운은 진하게 표시
  document.querySelectorAll('select[data-key]').forEach(function (sel) {
    var def = Array.prototype.filter.call(sel.options, function (o) { return o.defaultSelected; })[0] || sel.options[0];
    var mark = function () { sel.classList.toggle('set', sel.value !== def.value); };
    sel.addEventListener('change', mark); mark();
  });
  var list = document.getElementById('list');
  function applyFilters() {
    if (!list) return;
    var tnav = document.querySelector('.sticky nav.toggles');
    var need = tnav ? toggleValues(tnav) : [];
    var want = {};
    document.querySelectorAll('.sticky select[data-key]').forEach(function (sel) { if (sel.value) want[sel.getAttribute('data-key')] = sel.value; });
    var shown = 0;
    list.querySelectorAll('.card').forEach(function (c) {
      var f = (c.getAttribute('data-f') || '').split(' ');
      var ok = need.every(function (t) { return f.indexOf(t) !== -1; });
      for (var k in want) {
        if (!ok) break;
        var vals = k === 'c' ? [c.getAttribute('data-c') || ''] : (c.getAttribute('data-' + k) || '').split(' ');
        if (vals.indexOf(want[k]) === -1) ok = false;
      }
      c.hidden = !ok;
      if (ok) { shown++; var r = c.querySelector('.rank'); if (r) r.textContent = shown; }
    });
    var count = document.getElementById('count');
    if (count) count.textContent = shown ? shown + '곳' : '조건에 맞는 곳이 없어요. 조건을 줄여 보세요.';
  }
  if (list) {
    var tn = document.querySelector('.sticky nav.toggles');
    if (tn) bindToggles(tn, applyFilters);
    document.querySelectorAll('.sticky select[data-key]').forEach(function (sel) { sel.addEventListener('change', applyFilters); });
    applyFilters();
  }

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
  var FOOD_SUFFIX = /(순대국밥|순대국|순대|국밥|설렁탕|곰탕|해장국|칼국수|냉면|막국수|갈비|곱창|족발|보쌈|치킨|김밥|떡볶이|식당|회관|본점|집)$/;
  function coreName(t) { var c = String(t || '').replace(FOOD_SUFFIX, ''); return c.length >= 2 ? c : ''; }
  function norm(s) { return String(s || '').toLowerCase().replace(/\(주\)|㈜|주식회사/g, '').replace(/[^0-9a-z가-힣]/g, ''); }
  function load() {
    if (data || loading) return loading;
    loading = fetch(ROOT + 'assets/places.json').then(function (r) { return r.json(); }).then(function (d) {
      if (d.v === 2) {  // 압축 형식을 예전 배열 모양으로 풀어 쓴다 (build.py 참고)
        var areaSlug = d.areas.map(function (a) { return a[0]; }), areaMap = {};
        d.areas.forEach(function (a) { areaMap[a[0]] = a[1]; });
        d.places = d.places.map(function (q) {
          var f = q[8];
          return [q[0], d.catGroup[q[1]], d.cats[q[1]], q[2], q[3], q[4], q[5], q[6], q[7] >= 0 ? areaSlug[q[7]] : '',
            f & 1 ? 1 : 0, f & 2 ? 1 : 0, f & 4 ? 1 : 0, f & 8 ? 1 : 0, f & 16 ? 1 : 0, q[9], q[10], f & 32 ? 1 : 0, f & 64 ? 1 : 0, f];
        });
        d.areas = areaMap;
      }
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
  var GROUP_ICONS = { korean: '🍚', meat: '🥩', seafood: '🐟', japanese: '🍣', chinese: '🥟', western: '🍝', quick: '🍔', drink: '🍺', cafe: '☕' };
  function iconOf(cat, group) {
    if (ICONS[cat]) return ICONS[cat];
    if (!group && data && data.cats) group = catGroupOf(cat);
    return GROUP_ICONS[group] || '🍽️';
  }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
  function placeCard(p) {
    // p: {name, cat, addr, score, all, ccg, url, area, pick, busy, talk, premium, chain, taste, note, q, lat, lng, live}
    // build.py card() 와 같은 모양: 이름·점수 / 업종·위치 / 태그 3개 / eat.pl 버튼 / 자세히
    var li = el('li', 'card');
    li.id = 'p-' + String(p.url || '').split('/').pop();
    if (p.q) li.setAttribute('data-q', p.q);
    var head = el('div', 'card-head');
    var ic = el('span', 'ico', iconOf(p.cat, p.g)); ic.setAttribute('aria-hidden', 'true'); head.appendChild(ic);
    head.appendChild(el('h3', '', p.name));
    var s = el('span', 'score'); s.appendChild(el('b', '', String(p.score))); s.appendChild(el('small', '', grade(p.score)));
    head.appendChild(s);
    li.appendChild(head);
    var sub = el('p', 'sub');
    sub.appendChild(el('span', 'cat', p.cat || '기타'));
    var where = String(p.addr || '').split(/\s+/);
    if (where.length && where[0].indexOf('세종') !== 0) where.shift();
    sub.appendChild(document.createTextNode(' · ' + where.slice(0, 2).join(' ')));
    if (p.area && data && data.areas[p.area]) {
      sub.appendChild(document.createTextNode(' · '));
      var al = el('a', '', data.areas[p.area]); al.href = ROOT + 'area/' + p.area + '.html'; sub.appendChild(al);
    }
    li.appendChild(sub);
    var tags = [['pick', '주인장 픽', p.pick], ['taste', '주인장 취향', p.taste], ['talk', '대화하기 좋은 곳', p.talk],
      ['premium', '고급', p.premium], ['busy', '아주 붐빔', p.busy], ['chain', '프랜차이즈', p.chain], ['', p.note, p.note]]
      .filter(function (t) { return t[2]; }).slice(0, 3);
    if (tags.length) {
      var tg = el('p', 'tags');
      tags.forEach(function (t) { tg.appendChild(el('span', 't ' + t[0], t[1])); });
      li.appendChild(tg);
    }
    var act = el('div', 'actions');
    var go = el('a', 'go', 'eat.pl에서 메뉴 보기'); go.href = p.url; go.target = '_blank'; go.rel = 'noopener';
    act.appendChild(go);
    if (p.lat) {
      var mb = el('button', 'map-btn', '지도'); mb.type = 'button'; mb.setAttribute('aria-label', '지도에서 보기');
      if (p.live) { li.setAttribute('data-at', p.lat + ',' + p.lng + ',' + p.name); }
      act.appendChild(mb);
    }
    var sb = el('button', 'share', '공유'); sb.type = 'button'; sb.setAttribute('aria-label', '공유'); act.appendChild(sb);
    li.appendChild(act);
    var more = el('details', 'more'); more.appendChild(el('summary', '', '자세히'));
    more.appendChild(el('p', 'nums', '전국 같은 업종 매출 상위 ' + pct(p.all) + '% · 구 안에서 상위 ' + pct(p.ccg) + '%'));
    if (p.busy) more.appendChild(el('p', 'note busy', BUSY_NOTE + (p.group ? ' · ' + BUSY_ROOM_GROUP : '')));
    if (p.premium && p.score < 75) more.appendChild(el('p', 'note premium', '소규모 고급 업장이라 점수가 낮게 나와요'));
    more.appendChild(el('p', '', p.addr || ''));
    var kp = el('p'), ka = el('a', 'kakao', '카카오맵에서 보기 (사진·리뷰)');
    var w2 = String(p.addr || '').split(/\s+/); if (w2.length && w2[0].indexOf('세종') !== 0) w2.shift();
    ka.href = 'https://map.kakao.com/link/search/' + encodeURIComponent(p.name + ' ' + w2.slice(0, 2).join(' '));
    ka.target = '_blank'; ka.rel = 'noopener'; kp.appendChild(ka); more.appendChild(kp);
    li.appendChild(more);
    return li;
  }
  function fromIndex(p) { return { name: p[0], g: p[1], cat: p[2], addr: p[3], score: p[4], all: p[5], ccg: p[6], url: (p[7].indexOf('https://') === 0 ? '' : MER) + p[7], area: p[8], pick: p[9], busy: p[10], talk: p[11], premium: p[12], group: p[13], lat: p[14], lng: p[15], chain: p[16], taste: p[17],
      mine: p[9] || p[17], ages: agesFromBits(p[18] || 0), sex: sexFromBits(p[18] || 0) }; }
  function agesFromBits(f) {
    return [['young', 128], ['20', 256], ['30', 512], ['40', 1024], ['50', 2048]].filter(function (x) { return f & x[1]; }).map(function (x) { return x[0]; });
  }
  function sexFromBits(f) { return f & 4096 ? 'f' : f & 8192 ? 'm' : f & 16384 ? 'mix' : ''; }
  // 실시간 가게: 주 고객 1·2위로 같은 값을 만든다 (build.py age_tags / sex_tag 와 같은 규칙)
  function custTags(it) {
    var c = it.vistCstmrRnkList || {}, ages = [], g = [];
    ['rk01', 'rk02'].forEach(function (k) {
      var v = c[k] || '', m = /(\d+)대/.exec(v);
      if (m) ages.push(Math.min(parseInt(m[1], 10), 50));
      g.push(v.indexOf('여성') !== -1 ? 'f' : v.indexOf('남성') !== -1 ? 'm' : '');
    });
    var tags = ages.filter(function (a) { return a >= 20; }).map(String);
    if (ages.length === 2 && ages.every(function (a) { return a === 20 || a === 30; })) tags.push('young');
    var sex = g.indexOf('') !== -1 ? '' : g[0] === g[1] ? g[0] : 'mix';
    return { ages: tags, sex: sex };
  }

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
            lat: it.lat, lng: it.lng, live: true, chain: isChainName(it.merNm), taste: isTaste(it) }));
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
    document.querySelectorAll('.city').forEach(function (s) {
      s.hidden = !s.querySelector('.tile:not([hidden])');
      if (v && !s.hidden && s.tagName === 'DETAILS') s.open = true;
    });
    var extra = document.querySelector('.home-extra');  // 검색 중에는 바로가기·종류 칩을 숨겨 결과만 보이게
    if (extra) extra.hidden = !!v;
    var areasBox = document.getElementById('areas');
    if (areasBox) areasBox.hidden = !areasBox.querySelector('.city:not([hidden])');
    if (!results) return;
    results.innerHTML = '';
    if (v.length < 2) return;
    load().then(function (d) {
      if (q.value.trim() !== v) return;
      results.innerHTML = '';
      // 가게 이름이 아니라 질문처럼 보이면 '물어보기'로 이어 준다
      var asked = API && v.length >= 8 && /\s/.test(v) && /\?|뭐|추천|어디|좋은|맛집|먹을|먹지|갈까/.test(v);
      if (asked) {
        var ask = el('a', 'ask-link', '"' + v + '" AI에게 물어보기 →');
        ask.href = document.getElementById('ask-form') ? '#ask=' + encodeURIComponent(v) : ROOT + 'ask.html#q=' + encodeURIComponent(v);
        results.appendChild(ask);
      }
      var toks = v.split(/\s+/).map(norm).filter(Boolean);
      var whole = norm(v);
      // '농민백암순대'처럼 업종 말이 붙은 이름은 '농민백암'으로도 찾는다 (eat.pl 등록 이름이 다를 때가 많다)
      var alts = toks.map(function (t) { var c = coreName(t); return c && c !== t ? [t, c] : [t]; });
      var hits = d.places.filter(function (p) {
        return alts.every(function (a) { return a.some(function (t) { return p.hay.indexOf(t) !== -1; }); });
      });
      hits.sort(function (a, b) {
        var ra = a.n === whole ? 0 : a.n.indexOf(whole) === 0 ? 1 : a.n.indexOf(whole) !== -1 ? 2 : 3;
        var rb = b.n === whole ? 0 : b.n.indexOf(whole) === 0 ? 1 : b.n.indexOf(whole) !== -1 ? 2 : 3;
        return ra - rb || b[4] - a[4];
      });
      var nameHit = hits.length && hits[0].n.indexOf(whole) !== -1;
      if (hits.length) {
        results.appendChild(el('p', 'count', hits.length > 30 ? '검색 결과 ' + hits.length + '곳 중 매출점수 상위 30곳' : '검색 결과 ' + hits.length + '곳'));
        var ul = el('ol', 'list');
        hits.slice(0, 30).forEach(function (p) { ul.appendChild(placeCard(fromIndex(p))); });
        results.appendChild(ul);
      } else if (!asked) {
        results.appendChild(el('p', 'count', '모아 둔 ' + d.places.length.toLocaleString() + '곳 중에는 없어요.'));
      }
      if (!nameHit && !asked) liveButton(v, results);
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
      box.appendChild(el('p', 'hint', '내가 아는 집도 검색해 보세요.'));
      results.appendChild(box);
      document.getElementById('search').scrollIntoView();
    });
  }

  // 주인장 취향 (build.py taste_features / taste_prob / is_destination 과 같은 계산, 모델은 places.json 에서)
  function tasteFeatures(it) {
    var f = {}, a = parseFloat(it.allSaleRnk), c = parseFloat(it.ccgSaleRnk);
    a = isNaN(a) ? 99 : a; c = isNaN(c) ? 99 : c;
    f.log_all = Math.log(Math.max(a, 0.01)) / Math.LN10; f.log_ccg = Math.log(Math.max(c, 0.01)) / Math.LN10; f.all_minus_ccg = f.log_all - f.log_ccg;
    var g = catGroupOf(it.merTpbuzNm);
    ['korean', 'meat', 'seafood', 'japanese', 'chinese', 'western', 'quick', 'drink', 'cafe', 'etc'].forEach(function (k) { f['g_' + k] = g === k ? 1 : 0; });
    f.local = it.loclPrsnPpltYn ? 1 : 0; f.foreign = it.frnrVsitYn ? 1 : 0; f.group_dinner = it.empyVsitYn ? 1 : 0;
    var s = it.srtTime || '', e = it.endTime || '';
    var sh = /^\d{4}$/.test(s) ? +s.slice(0, 2) + +s.slice(2) / 60 : 11, eh = /^\d{4}$/.test(e) ? +e.slice(0, 2) + +e.slice(2) / 60 : 21;
    if (eh < sh) eh += 24;
    f.open_h = sh; f.close_h = eh; f.dur_h = eh - sh;
    var hol = it.hldyDwk || [];
    f.n_holiday = hol.length; f.closed_weekend = (hol.indexOf('토') !== -1 || hol.indexOf('일') !== -1) ? 1 : 0; f.closed_mon = hol.indexOf('월') !== -1 ? 1 : 0;
    ['점심', '저녁', '심야', '오전', '오후', '새벽'].forEach(function (k) { f['peak_' + k] = it.congestionTizn === k ? 1 : 0; });
    f.peak_weekend = (it.congestionDwk === '토' || it.congestionDwk === '일') ? 1 : 0;
    var cust = it.vistCstmrRnkList || {}, ages = [], fem = 0;
    ['rk01', 'rk02', 'rk03'].forEach(function (k) { var v = cust[k] || '', m = /(\d+)대/.exec(v); if (m) ages.push(+m[1]); if (v.indexOf('여성') !== -1) fem++; });
    f.n_2030 = ages.filter(function (x) { return x === 20 || x === 30; }).length; f.n_50p = ages.filter(function (x) { return x >= 50; }).length;
    f.top1_age = ages.length ? ages[0] : 40; f.n_female = fem;
    f.chain = isChainName(it.merNm) ? 1 : 0; f.premium = isPremium(it) ? 1 : 0;
    var n = cleanName(it.merNm);
    f.name_len = n.length; f.name_latin = /[A-Za-z]/.test(n) ? 1 : 0;
    f.name_oldschool = /(식당|집|회관|가든|옥|정|관)$/.test(n.replace(/\s/g, '')) ? 1 : 0; f.name_bonjeom = (it.merNm || '').indexOf('본점') !== -1 ? 1 : 0;
    return f;
  }
  function catGroupOf(cat) {
    if (!data) return 'etc';
    if (!data._cg) { data._cg = {}; (data.cats || []).forEach(function (c, i) { data._cg[c] = data.catGroup[i]; }); }
    return data._cg[cat] || 'etc';
  }
  var DEST_BAD = /호스피탈리티|푸드앤|호텔|리조트|컨벤션|케이터링|푸드서비스|외식산업|에프앤비|F&B|PC|피씨|노래|만화|스터디|_NICE|\(구\)|휴게소|푸드코트|병원|대학교|인문대|학생식당|구내식당|식당\d|사조/i;
  function isDestination(it, score) {  // build.py is_destination 과 같은 규칙
    var g = catGroupOf(it.merTpbuzNm);
    return score >= 90 && !it.loclPrsnPpltYn && !isChainName(it.merNm) && ['drink', 'cafe', 'quick', 'etc'].indexOf(g) === -1
      && !DEST_BAD.test(it.merNm || '');
  }
  function isTaste(it) {
    var m = data && data.taste;
    if (!m || /\(주\)|㈜|주식회사/.test(it.merNm || '') && (it.merNm || '').indexOf('/') === -1) return false;
    var f = tasteFeatures(it), z = m.intercept;
    for (var i = 0; i < m.features.length; i++) z += m.coef[i] * (((f[m.features[i]] || 0) - m.mean[i]) / (m.scale[i] || 1));
    var prob = 1 / (1 + Math.exp(-z));
    return prob >= m.thr || (prob >= (m.gate || 1) && isDestination(it, scoreOf(it.allSaleRnk, it.ccgSaleRnk)));
  }

  // shared helpers for map.js
  window.MATJIP = { ROOT: ROOT, API: API, load: load, placeCard: placeCard, fromIndex: fromIndex, scoreOf: scoreOf,
    isBusy: isBusy, isPremium: isPremium, cleanName: cleanName, el: el, iconOf: iconOf, isChainName: isChainName,
    bindToggles: bindToggles, toggleValues: toggleValues, custTags: custTags, isTaste: isTaste, catGroupOf: catGroupOf, share: share,
    data: function () { return data; } };

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
