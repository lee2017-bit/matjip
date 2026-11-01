(function () {
  // 지도 페이지: Leaflet + OpenStreetMap. 데이터는 홈 검색과 같은 places.json.
  var M = window.MATJIP;
  if (!M || !window.L || !document.getElementById('map')) return;
  var el = M.el;
  var status = document.getElementById('map-status');
  var listEl = document.getElementById('map-list');
  var countEl = document.getElementById('map-count');
  var hereBtn = document.getElementById('here');
  var filter = 'meal';
  var me = null;            // {lat, lng}
  var liveItems = [];       // 실시간 조회로 받은 가게 (places.json 에 없는 곳)
  var focusMarker = null;

  var map = L.map('map', { preferCanvas: true, zoomControl: true }).setView([37.5665, 126.978], 15);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
  }).addTo(map);
  var layer = L.layerGroup().addTo(map);
  var meLayer = L.layerGroup().addTo(map);

  function say(t) { status.textContent = t || ''; }
  function dist(a, b) {
    var R = 6371000, dLat = (b.lat - a.lat) * Math.PI / 180, dLng = (b.lng - a.lng) * Math.PI / 180;
    var x = Math.sin(dLat / 2) * Math.sin(dLat / 2) + Math.cos(a.lat * Math.PI / 180) * Math.cos(b.lat * Math.PI / 180) * Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return 2 * R * Math.asin(Math.sqrt(x));
  }
  function fmtDist(m) { return m < 1000 ? Math.round(m / 10) * 10 + 'm' : (m / 1000).toFixed(1) + 'km'; }
  function pass(p) {
    if (!filter) return true;
    if (filter === 'meal') return p.g !== 'drink' && p.g !== 'cafe';
    if (filter === 'talk') return !!p.talk;
    if (filter === 'premium') return !!p.premium;
    if (filter === 'pick') return !!p.pick;
    return p.g === filter;
  }
  function color(s) { return s >= 90 ? '#c2410c' : s >= 80 ? '#ea580c' : s >= 70 ? '#fb923c' : s >= 60 ? '#fdba74' : '#a8a29e'; }
  var sortBy = 'score';     // 내 위치를 켜면 '점수순 / 가까운 순' 전환
  var fetched = [];         // 실시간으로 불러온 지점들 (같은 곳을 다시 부르지 않는다)
  var autoLeft = 8;         // 한 번 방문에 자동 조회는 최대 8번

  var all = [];             // normalized places with coordinates
  function fromIndex(p) { var o = M.fromIndex(p); o.g = p[1]; return o; }
  function fromLive(it) {
    return { name: M.cleanName(it.merNm), cat: it.merTpbuzNm, addr: String(it.merAddr || '').replace(/\(.*?\)/g, '').split(',')[0].trim(),
      score: M.scoreOf(it.allSaleRnk, it.ccgSaleRnk), all: it.allSaleRnk, ccg: it.ccgSaleRnk, url: it.merUrl,
      busy: M.isBusy(it.allSaleRnk, it.empyVsitYn), group: !!it.empyVsitYn, premium: M.isPremium(it),
      lat: it.lat, lng: it.lng, live: true, note: '실시간 조회 결과', g: '' };
  }

  function popup(p) {
    var box = el('div', 'pop');
    var h = el('strong', '', M.iconOf(p.cat) + ' ' + p.name); box.appendChild(h);
    box.appendChild(el('div', 'pop-meta', p.score + '점 · ' + (p.cat || '') + (me ? ' · ' + fmtDist(dist(me, p)) : '')));
    var a = el('a', 'go', 'eat.pl에서 메뉴·리뷰 요약 보기 →'); a.href = p.url; a.target = '_blank'; a.rel = 'noopener';
    box.appendChild(a);
    return box;
  }

  function render() {
    var b = map.getBounds().pad(0.05);
    var inView = all.concat(liveItems).filter(function (p) { return p.lat && b.contains([p.lat, p.lng]) && pass(p); });
    inView.sort(function (x, y) { return y.score - x.score; });
    layer.clearLayers();
    inView.slice(0, 400).forEach(function (p) {
      var mk = L.circleMarker([p.lat, p.lng], {
        radius: p.score >= 90 ? 9 : p.score >= 80 ? 7 : 5, color: '#fff', weight: 1.5,
        fillColor: color(p.score), fillOpacity: 0.9
      });
      mk.bindPopup(function () { return popup(p); });
      mk.addTo(layer);
    });
    // list: near me → by distance, otherwise by score
    var rows = inView.slice();
    if (me && sortBy === 'dist') rows.sort(function (x, y) { return dist(me, x) - dist(me, y); });
    var sortNav = document.getElementById('map-sort');
    if (sortNav) sortNav.hidden = !me;
    listEl.innerHTML = '';
    rows.slice(0, 30).forEach(function (p) {
      var c = M.placeCard(p);
      if (me) {
        var d = el('span', 'dist', fmtDist(dist(me, p)));
        c.querySelector('.card-head').insertBefore(d, c.querySelector('.card-head').firstChild);
      }
      listEl.appendChild(c);
    });
    countEl.textContent = inView.length
      ? '이 화면 안 ' + inView.length + '곳' + (inView.length > 30 ? (me && sortBy === 'dist' ? ' · 가까운 30곳' : ' · 매출점수 상위 30곳') : '')
      : '이 화면에는 모아 둔 가게가 없어요.';
    if (hereBtn) hereBtn.hidden = !M.API || map.getZoom() < 14;
  }

  function live(lat, lng) {
    if (!M.API) { say('이 동네는 아직 데이터가 없어요.'); return; }
    fetched.push({ lat: lat, lng: lng });
    say('이 근처 동네 가게를 불러오는 중… (최대 10초)');
    if (hereBtn) hereBtn.disabled = true;
    fetch(M.API + '/?lat=' + lat.toFixed(5) + '&lng=' + lng.toFixed(5)).then(function (r) { return r.json(); }).then(function (d) {
      var known = {};
      all.forEach(function (p) { known[p.url] = 1; });
      var added = 0;
      (d.results || []).forEach(function (x) {
        var it = x.item;
        if (!it || !it.merUrl || !it.lat || known[it.merUrl]) return;
        known[it.merUrl] = 1; liveItems.push(fromLive(it)); added++;
      });
      say(d.area ? d.area + ' 매출 상위 가게를 불러왔어요' + (added ? ' (새로 ' + added + '곳)' : '') : (d.message || ''));
      setTimeout(function () { if (status.textContent.indexOf('불러왔어요') !== -1) say(''); }, 5000);
      render();
    }).catch(function () { say('지금은 조회가 몰려 있어요. 잠시 뒤 다시 시도해 주세요.'); })
      .then(function () { if (hereBtn) hereBtn.disabled = false; });
  }

  function locate() {
    if (!navigator.geolocation) { say('이 브라우저에서는 위치를 쓸 수 없어요.'); return; }
    say('위치를 확인하는 중…');
    navigator.geolocation.getCurrentPosition(function (pos) {
      me = { lat: pos.coords.latitude, lng: pos.coords.longitude };
      meLayer.clearLayers();
      L.circle([me.lat, me.lng], { radius: Math.min(pos.coords.accuracy, 300), color: '#2563eb', weight: 1, fillOpacity: 0.08 }).addTo(meLayer);
      L.circleMarker([me.lat, me.lng], { radius: 7, color: '#fff', weight: 2, fillColor: '#2563eb', fillOpacity: 1 }).addTo(meLayer).bindPopup('내 위치');
      map.setView([me.lat, me.lng], 16);
      say('');
      live(me.lat, me.lng);   // 모아 둔 데이터는 동네마다 들쭉날쭉해서 내 주변은 항상 실시간으로 채운다
    }, function (err) {
      say(err.code === 1 ? '위치 권한이 꺼져 있어요. 브라우저 설정에서 이 사이트의 위치 접근을 허용해 주세요. (카톡·인스타 등 앱 안 브라우저는 막혀 있을 수 있어요)'
        : '위치를 가져오지 못했어요. 잠시 뒤 다시 시도해 주세요.');
    }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
  }

  function focus() {
    var h = location.hash;
    var mp = /#p=([^&]+)/.exec(h), ma = /#at=([^&]+)/.exec(h);
    if (focusMarker) { map.removeLayer(focusMarker); focusMarker = null; }
    if (mp) {
      var p = all.filter(function (x) { return x.url.split('/').pop() === mp[1]; })[0];
      if (p) {
        map.setView([p.lat, p.lng], 17);
        focusMarker = L.marker([p.lat, p.lng]).addTo(map).bindPopup(popup(p)).openPopup();
      }
    } else if (ma) {
      var v = decodeURIComponent(ma[1]).split(',');
      var lat = parseFloat(v[0]), lng = parseFloat(v[1]);
      if (!isNaN(lat) && !isNaN(lng)) {
        map.setView([lat, lng], 17);
        focusMarker = L.marker([lat, lng]).addTo(map).bindPopup(el('strong', '', v.slice(2).join(',') || '선택한 가게')).openPopup();
      }
    } else if (/#near/.test(h)) {
      locate();
    }
  }
  M.focus = focus;

  document.getElementById('locate').addEventListener('click', locate);
  if (hereBtn) hereBtn.addEventListener('click', function () { var c = map.getCenter(); live(c.lat, c.lng); });
  document.querySelectorAll('#map-filter button').forEach(function (b) {
    b.addEventListener('click', function () {
      document.querySelectorAll('#map-filter button').forEach(function (x) { x.classList.remove('on'); });
      b.classList.add('on'); filter = b.getAttribute('data-v'); render();
    });
  });
  var autoTimer = null;
  map.on('moveend', function () {
    render();
    // 지도를 다른 동네로 옮기면 잠시 뒤 그 동네도 자동으로 불러온다
    clearTimeout(autoTimer);
    if (!M.API || autoLeft <= 0 || map.getZoom() < 15 || !all.length) return;
    autoTimer = setTimeout(function () {
      var c = map.getCenter(), cc = { lat: c.lat, lng: c.lng };
      if (fetched.some(function (f) { return dist(f, cc) < 800; })) return;
      autoLeft--; live(c.lat, c.lng);
    }, 1500);
  });
  document.querySelectorAll('#map-sort button').forEach(function (b) {
    b.addEventListener('click', function () {
      document.querySelectorAll('#map-sort button').forEach(function (x) { x.classList.remove('on'); });
      b.classList.add('on'); sortBy = b.getAttribute('data-v'); render();
    });
  });

  say('지도를 불러오는 중…');
  M.load().then(function (d) {
    all = d.places.filter(function (p) { return p[14]; }).map(fromIndex);
    say('');
    render();
    focus();
  });
})();
