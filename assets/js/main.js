(function () {
  var root = document.documentElement;
  var de = root.lang === 'de';
  var reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  var L = de
    ? { dark: 'Dunkles Design aktivieren', light: 'Helles Design aktivieren', open: 'Menü öffnen', close: 'Menü schließen', copy: 'E-Mail kopieren', copied: 'Kopiert' }
    : { dark: 'Switch to dark mode', light: 'Switch to light mode', open: 'Open menu', close: 'Close menu', copy: 'Copy email', copied: 'Copied' };
  var css = function (n) { return getComputedStyle(root).getPropertyValue(n).trim(); };

  // ----- language switch: remember the choice for the next visit -----
  var langLink = document.querySelector('.lang-switch');
  if (langLink) {
    langLink.addEventListener('click', function () {
      try { localStorage.setItem('lang', langLink.dataset.lang); } catch (e) {}
      langLink.href = langLink.getAttribute('href').split('#')[0] + location.hash;
    });
  }

  // ----- theme: icon morph + circular reveal from the button -----
  var themeBtn = document.querySelector('.theme-btn');
  function isDark() {
    var t = root.dataset.theme;
    return t ? t === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  }
  function syncTheme() {
    root.classList.toggle('is-dark', isDark());
    themeBtn.setAttribute('aria-label', isDark() ? L.light : L.dark);
    if (net) net.draw(performance.now());
  }
  themeBtn.addEventListener('click', function () {
    var next = isDark() ? 'light' : 'dark';
    var apply = function () {
      root.dataset.theme = next;
      try { localStorage.setItem('theme', next); } catch (e) {}
      syncTheme();
    };
    if (!document.startViewTransition || reduce) { apply(); return; }
    var r = themeBtn.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
    var end = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    document.startViewTransition(apply).ready.then(function () {
      root.animate({ clipPath: ['circle(0px at ' + x + 'px ' + y + 'px)', 'circle(' + end + 'px at ' + x + 'px ' + y + 'px)'] },
        { duration: 700, easing: 'cubic-bezier(.2,.7,.2,1)', pseudoElement: '::view-transition-new(root)' });
    });
  });
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', syncTheme);

  // ----- hero: medical data network -----
  // Labeled data types from the research send signals into the centre (the portrait = the model).
  var net = (function () {
    var canvas = document.getElementById('net');
    if (!canvas || !canvas.getContext) return null;
    var ctx = canvas.getContext('2d'), S = 0, nodes = [], edges = [], pulses = [], t0 = performance.now();
    var labels = (canvas.dataset.labels || '').split('|');
    function rng(seed) { return function () { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; }; }
    function ang(x) { while (x > Math.PI) x -= 2 * Math.PI; while (x < -Math.PI) x += 2 * Math.PI; return x; }
    function build() {
      var dpr = Math.min(window.devicePixelRatio || 1, 2); S = canvas.clientWidth;
      canvas.width = canvas.height = Math.round(S * dpr); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      var r = rng(31); nodes = []; edges = []; pulses = [];
      labels.forEach(function (l, i) { nodes.push({ ring: .4, a: -Math.PI / 2 + i * 2 * Math.PI / labels.length + .3, size: 5, label: l, dir: 1 }); });
      for (var i = 0; i < 10; i++) nodes.push({ ring: .32, a: i * Math.PI / 5 + r() * .3, size: 2.6, dir: -1 });
      for (var j = 0; j < 14; j++) nodes.push({ ring: .46 + r() * .03, a: j * Math.PI / 7 + r() * .4, size: 1.8, dir: 1 });
      var inner = nodes.filter(function (n) { return n.ring === .32; });
      function nearest(n, k) { return inner.slice().sort(function (p, q) { return Math.abs(ang(p.a - n.a)) - Math.abs(ang(q.a - n.a)); }).slice(0, k); }
      nodes.forEach(function (n) {
        if (n.label) nearest(n, 2).forEach(function (m) { edges.push([n, m]); });
        else if (n.ring === .32) edges.push([n, null]);
        else edges.push([n, nearest(n, 1)[0]]);
      });
      for (var p = 0; p < 18; p++) pulses.push({ e: edges[Math.floor(r() * edges.length)], t: r(), v: .004 + r() * .006 });
    }
    function pos(n, time) {
      if (!n) return [S / 2, S / 2];
      var a = n.a + (reduce ? 0 : time * .00004 * n.dir);
      return [S / 2 + Math.cos(a) * n.ring * S, S / 2 + Math.sin(a) * n.ring * S];
    }
    function draw(now) {
      if (!S) return;
      var time = now - t0, grow = reduce ? 1 : Math.min(1, time / 1600), e3 = 1 - Math.pow(1 - grow, 3);
      ctx.clearRect(0, 0, S, S);
      var g = ctx.createRadialGradient(S / 2, S / 2, S * .1, S / 2, S / 2, S * .5);
      g.addColorStop(0, css('--hero-glow')); g.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = g; ctx.fillRect(0, 0, S, S);
      ctx.strokeStyle = css('--line'); ctx.lineWidth = 1;
      [.32, .4].forEach(function (rr) { ctx.beginPath(); ctx.arc(S / 2, S / 2, rr * S * e3, 0, Math.PI * 2); ctx.stroke(); });
      ctx.strokeStyle = css('--edge');
      edges.forEach(function (e) {
        var A = pos(e[0], time), B = pos(e[1], time);
        ctx.beginPath(); ctx.moveTo(B[0], B[1]); ctx.lineTo(B[0] + (A[0] - B[0]) * e3, B[1] + (A[1] - B[1]) * e3); ctx.stroke();
      });
      if (grow === 1 && !reduce) {
        ctx.fillStyle = css('--accent');
        pulses.forEach(function (p) {
          p.t += p.v; if (p.t > 1) { p.t = 0; p.e = edges[Math.floor(Math.random() * edges.length)]; }
          var A = pos(p.e[0], time), B = pos(p.e[1], time);
          ctx.globalAlpha = Math.sin(p.t * Math.PI);
          ctx.beginPath(); ctx.arc(A[0] + (B[0] - A[0]) * p.t, A[1] + (B[1] - A[1]) * p.t, 2.2, 0, Math.PI * 2); ctx.fill();
        });
        ctx.globalAlpha = 1;
      }
      var fs = S < 380 ? 10 : 11;
      ctx.font = '500 ' + fs + 'px "IBM Plex Mono", monospace'; ctx.textBaseline = 'middle';
      nodes.forEach(function (n) {
        var P = pos(n, time);
        ctx.globalAlpha = n.label ? Math.max(0, e3 * 1.4 - .4) : e3;
        ctx.fillStyle = n.label ? css('--accent') : css('--muted');
        ctx.beginPath(); ctx.arc(P[0], P[1], n.size, 0, Math.PI * 2); ctx.fill();
        if (n.label) {
          var w = ctx.measureText(n.label).width + 16, h = fs + 12;
          var x = Math.min(Math.max(P[0] - w / 2, 2), S - w - 2), y = P[1] + (P[1] < S / 2 ? -h - 8 : 8);
          ctx.fillStyle = css('--surface'); ctx.strokeStyle = css('--line');
          ctx.beginPath(); if (ctx.roundRect) ctx.roundRect(x, y, w, h, h / 2); else ctx.rect(x, y, w, h); ctx.fill(); ctx.stroke();
          ctx.fillStyle = css('--ink'); ctx.fillText(n.label, x + 8, y + h / 2 + .5);
        }
      });
      ctx.globalAlpha = 1;
    }
    var visible = true;
    if ('IntersectionObserver' in window) new IntersectionObserver(function (e) { visible = e[0].isIntersecting; }).observe(canvas);
    function loop(now) { if (visible) draw(now); if (!reduce) requestAnimationFrame(loop); }
    function start() { build(); requestAnimationFrame(loop); }
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(start); else start();
    var rt; addEventListener('resize', function () { clearTimeout(rt); rt = setTimeout(function () { build(); draw(performance.now()); }, 150); });
    return { draw: draw };
  })();
  syncTheme();

  // ----- mobile menu -----
  var menuBtn = document.querySelector('.menu-btn');
  var links = document.getElementById('nav-links');
  function setMenu(open) {
    links.classList.toggle('open', open);
    menuBtn.setAttribute('aria-expanded', String(open));
    menuBtn.setAttribute('aria-label', open ? L.close : L.open);
  }
  menuBtn.addEventListener('click', function () { setMenu(!links.classList.contains('open')); });
  links.addEventListener('click', function (e) { if (e.target.closest('a')) setMenu(false); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && links.classList.contains('open')) { setMenu(false); menuBtn.focus(); }
  });

  // ----- nav: border on scroll, active section with sliding underline -----
  var nav = document.querySelector('.nav'), bar = links.querySelector('.bar');
  var anchors = Array.prototype.slice.call(links.querySelectorAll('a'));
  function onScroll() {
    nav.classList.toggle('scrolled', window.scrollY > 10);
    var current = null;
    anchors.forEach(function (a) {
      var s = document.querySelector(a.getAttribute('href'));
      if (s && s.getBoundingClientRect().top < innerHeight * .4) current = a;
    });
    anchors.forEach(function (a) { a.classList.toggle('active', a === current); if (a === current) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current'); });
    if (bar) {
      if (current) { bar.style.opacity = 1; bar.style.width = (current.offsetWidth - 22) + 'px'; bar.style.transform = 'translateX(' + (current.offsetLeft + 11) + 'px)'; }
      else bar.style.opacity = 0;
    }
  }
  addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  // ----- scroll reveal, number count-up, citation meters -----
  function fillMeters(scope) { scope.querySelectorAll('.meter i').forEach(function (m) { m.style.width = m.dataset.w + '%'; }); }
  if ('IntersectionObserver' in window && !reduce) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        e.target.classList.remove('pre');
        fillMeters(e.target);
        io.unobserve(e.target);
      });
    }, { rootMargin: '0px 0px -8% 0px' });
    document.querySelectorAll('.reveal').forEach(function (el) {
      if (el.getBoundingClientRect().top > innerHeight) el.classList.add('pre');
      io.observe(el);
    });
    document.querySelectorAll('.stats dd').forEach(function (el) {
      var n = parseInt(el.textContent, 10), t0 = performance.now();
      if (!(n > 0)) return;
      (function tick(t) {
        var p = Math.min(1, (t - t0) / 1400);
        el.textContent = Math.round(n * (1 - Math.pow(1 - p, 3)));
        if (p < 1) requestAnimationFrame(tick);
      })(t0);
    });
  } else {
    fillMeters(document);
  }

  // ----- copy BibTeX and email -----
  function copyText(text, btn, label, done) {
    var restore = function () { btn.textContent = done; setTimeout(function () { btn.textContent = label; }, 1500); };
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(restore, function () {});
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest('.copy-btn');
    if (b) copyText(b.parentNode.querySelector('code').textContent, b, b.dataset.label || (b.dataset.label = b.textContent), b.dataset.copied);
    var m = e.target.closest('.copy-mail');
    if (m) copyText(m.dataset.email, m, L.copy, L.copied);
  });

  // ----- publication filter: chips with counts, animated reflow (FLIP) -----
  var filter = document.querySelector('.pub-filter');
  if (filter) {
    var chips = Array.prototype.slice.call(filter.querySelectorAll('.fchip'));
    var items = Array.prototype.slice.call(document.querySelectorAll('.pubs > .pub'));
    var countEl = document.querySelector('.pub-count');
    var busy = false;
    function matches(li, f) { return f === 'all' || (' ' + li.dataset.tags + ' ').indexOf(' ' + f + ' ') > -1; }
    function markTags(f) {
      items.forEach(function (li) {
        var keys = (li.dataset.tags || '').split(' ');
        li.querySelectorAll('.ptags li').forEach(function (tag, i) { tag.classList.toggle('hit', keys[i] === f); });
      });
    }
    function setCount(n) {
      if (countEl) countEl.textContent = countEl.dataset.template.replace('{n}', n).replace('{total}', items.length);
    }
    function applyFilter(f) {
      if (busy) return;
      var show = items.filter(function (li) { return matches(li, f); });
      var leaving = items.filter(function (li) { return !li.hidden && show.indexOf(li) < 0; });
      var entering = show.filter(function (li) { return li.hidden; });
      var staying = show.filter(function (li) { return !li.hidden; });
      markTags(f); setCount(show.length);
      if (reduce) { items.forEach(function (li) { li.hidden = show.indexOf(li) < 0; li.classList.remove('pre'); }); return; }
      busy = true;
      leaving.forEach(function (li) { li.classList.add('is-out'); });
      setTimeout(function () {
        var first = staying.map(function (li) { return li.getBoundingClientRect(); });
        leaving.forEach(function (li) { li.hidden = true; li.classList.remove('is-out'); });
        entering.forEach(function (li) { li.classList.remove('pre'); li.classList.add('is-in'); li.hidden = false; });
        staying.forEach(function (li, i) {
          var last = li.getBoundingClientRect();
          var dx = first[i].left - last.left, dy = first[i].top - last.top;
          if (dx || dy) { li.style.transition = 'none'; li.style.transform = 'translate(' + dx + 'px,' + dy + 'px)'; }
        });
        requestAnimationFrame(function () {
          requestAnimationFrame(function () {
            staying.forEach(function (li) { li.style.transition = ''; li.style.transform = ''; });
            entering.forEach(function (li, i) { setTimeout(function () { li.classList.remove('is-in'); fillMeters(li); }, 60 * i); });
            setTimeout(function () { busy = false; }, 500);
          });
        });
      }, leaving.length ? 280 : 0);
    }
    filter.addEventListener('click', function (e) {
      var chip = e.target.closest('.fchip');
      if (!chip || chip.classList.contains('active')) return;
      chips.forEach(function (c) { var on = c === chip; c.classList.toggle('active', on); c.setAttribute('aria-pressed', String(on)); });
      applyFilter(chip.dataset.filter);
    });
  }

  var year = document.querySelector('[data-year]');
  if (year) year.textContent = new Date().getFullYear();
})();
