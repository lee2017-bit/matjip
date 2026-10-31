(function () {
  // filters on area pages
  var list = document.getElementById('list');
  var buttons = document.querySelectorAll('.filters button');
  buttons.forEach(function (b) {
    b.addEventListener('click', function () {
      buttons.forEach(function (x) { x.classList.remove('on'); });
      b.classList.add('on');
      var f = b.getAttribute('data-f');
      list.querySelectorAll('.card').forEach(function (c) {
        var flags = (c.getAttribute('data-flags') || '').split(' ');
        c.hidden = !!f && flags.indexOf(f) === -1;
      });
    });
  });

  // "오늘 점심 뭐 먹지?" picker: 3 random places among the top lunch spots
  var pick = document.getElementById('pick');
  if (pick && list) {
    pick.addEventListener('click', function () {
      var cards = Array.prototype.slice.call(list.querySelectorAll('.card'));
      var pool = cards.filter(function (c) { return (c.getAttribute('data-flags') || '').indexOf('lunch') !== -1; }).slice(0, 40);
      if (pool.length < 3) pool = cards.slice(0, 40);
      for (var i = pool.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)); var t = pool[i]; pool[i] = pool[j]; pool[j] = t; }
      var out = document.getElementById('picked');
      out.innerHTML = '';
      pool.slice(0, 3).forEach(function (c) {
        var a = c.querySelector('a.go');
        var li = document.createElement('li');
        var link = document.createElement('a');
        link.href = a.href; link.target = '_blank'; link.rel = 'noopener';
        link.textContent = c.querySelector('h3').textContent + ' (' + c.querySelector('.cat').textContent + ')';
        var why = document.createElement('span');
        why.className = 'why';
        why.textContent = c.querySelector('.nums').textContent;
        li.appendChild(link); li.appendChild(why); out.appendChild(li);
      });
      pick.textContent = '다시 뽑기';
    });
  }

  // area search on the home page
  var q = document.getElementById('q');
  if (q) {
    q.addEventListener('input', function () {
      var v = q.value.trim();
      document.querySelectorAll('.tile').forEach(function (t) {
        t.hidden = !!v && (t.getAttribute('data-name') || '').indexOf(v) === -1;
      });
      document.querySelectorAll('.city').forEach(function (s) {
        s.hidden = !s.querySelector('.tile:not([hidden])');
      });
    });
  }

  // in-app browser notice (links opened inside chat apps may not keep the visit path)
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
