// Mobile menu and footer year. The site works without this script; it only adds the menu toggle.
(function () {
  var header = document.querySelector('[data-nav]');
  var toggle = header && header.querySelector('.nav-toggle');
  if (toggle) {
    var setOpen = function (open) {
      header.classList.toggle('nav-open', open);
      toggle.setAttribute('aria-expanded', String(open));
      toggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    };
    toggle.addEventListener('click', function () {
      setOpen(toggle.getAttribute('aria-expanded') !== 'true');
    });
    header.querySelectorAll('.nav a').forEach(function (link) {
      link.addEventListener('click', function () { setOpen(false); });
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && header.classList.contains('nav-open')) {
        setOpen(false);
        toggle.focus();
      }
    });
    window.matchMedia('(min-width: 861px)').addEventListener('change', function (e) {
      if (e.matches) setOpen(false);
    });
  }

  document.querySelectorAll('[data-year]').forEach(function (el) {
    el.textContent = String(new Date().getFullYear());
  });
})();

// "Open in the app" uses an Android intent link (opens CareerOS, or Google Play if it isn't
// installed); elsewhere it would do nothing, so it's hidden and the Play badge remains.
(function () {
  var android = /Android/i.test(navigator.userAgent);
  document.querySelectorAll("[data-android-only]").forEach(function (el) {
    if (!android) el.hidden = true;
  });
})();

// Mobile "Get the app" bar: phones only (not iPhone/iPad; the app is Android-only), after the reader
// scrolls a little, never on top of the analytics question or the "Get CareerOS" panel above the
// footer. Closing it hides it for 7 days (kept in this browser only; nothing is sent).
(function () {
  var bar = document.querySelector('[data-appbar]');
  if (!bar) return;
  var KEY = 'cos_appbar_closed', WEEK = 7 * 24 * 3600 * 1000;
  var closedAt = 0;
  try { closedAt = Number(window.localStorage.getItem(KEY)) || 0; } catch (e) {}
  var phone = window.matchMedia('(max-width: 760px)');
  if (/iPhone|iPad|iPod/i.test(navigator.userAgent) || Date.now() - closedAt < WEEK) return;

  var panelInView = false, closed = false;
  var panel = document.querySelector('.getapp');
  if (panel && 'IntersectionObserver' in window) {
    new IntersectionObserver(function (entries) {
      panelInView = entries[0].isIntersecting; update();
    }).observe(panel);
  }
  function update() {
    var show = !closed && phone.matches && window.scrollY > 400 && !panelInView && !document.querySelector('.consent');
    bar.hidden = !show;
    document.body.classList.toggle('has-appbar', show);
  }
  window.addEventListener('scroll', update, { passive: true });
  phone.addEventListener('change', update);
  // The consent box is added and removed by the analytics script below.
  new MutationObserver(update).observe(document.body, { childList: true });
  bar.querySelector('[data-appbar-close]').addEventListener('click', function () {
    closed = true; update();
    try { window.localStorage.setItem(KEY, String(Date.now())); } catch (e) {}
  });
  update();
})();

// Website analytics (first-party, CareerOS's own server; no third-party scripts).
// - Asks once. "Accept" keeps a random visitor id in this browser so return visits join up;
//   "Decline" (or Global Privacy Control) keeps nothing, and page views are counted with no identifier.
// - Sends: the page, its title, how long it was visible, links clicked to other sites, and on a new
//   visit the referring site and utm_ tags. Never form input or anything typed.
(function () {
  var scriptSrc = (document.currentScript && document.currentScript.src) || location.href;
  var host = location.hostname;
  var endpoint = window.CAREEROS_COLLECT ||
    (host === 'pintinz.github.io' ? 'https://p01--careeros-api--szyhwlzghlw2.code.run/api/v1/web/collect' : null);
  var KEY = 'cos_consent', VID = 'cos_vid', SID = 'cos_sid', LAST = 'cos_last';
  var GAP = 30 * 60 * 1000;

  function store(fn) { try { return fn(window.localStorage); } catch (e) { return null; } }
  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    var b = new Uint8Array(16); crypto.getRandomValues(b);
    b[6] = (b[6] & 15) | 64; b[8] = (b[8] & 63) | 128;
    var h = Array.prototype.map.call(b, function (x) { return (x + 256).toString(16).slice(1); }).join('');
    return h.slice(0, 8) + '-' + h.slice(8, 12) + '-' + h.slice(12, 16) + '-' + h.slice(16, 20) + '-' + h.slice(20);
  }
  function choice() { return store(function (s) { return s.getItem(KEY); }); }
  var gpc = navigator.globalPrivacyControl === true;

  function ids() {
    if (choice() !== 'yes' || gpc) return { v: null, s: null, fresh: true };
    return store(function (s) {
      var v = s.getItem(VID) || uuid();
      var last = parseInt(s.getItem(LAST) || '0', 10);
      var sid = s.getItem(SID);
      var fresh = !sid || Date.now() - last > GAP;
      if (fresh) sid = uuid();
      s.setItem(VID, v); s.setItem(SID, sid); s.setItem(LAST, String(Date.now()));
      return { v: v, s: sid, fresh: fresh };
    }) || { v: null, s: null, fresh: true };
  }

  function send(events, extra) {
    if (!endpoint) return;
    var id = ids();
    var body = { v: id.v, s: id.s, e: events };
    if (extra && extra.ref !== undefined) body.ref = extra.ref;
    if (extra && extra.utm) body.utm = extra.utm;
    var data = JSON.stringify(body);
    try {
      if (navigator.sendBeacon && navigator.sendBeacon(endpoint, new Blob([data], { type: 'text/plain' }))) return;
    } catch (e) { /* fall through */ }
    try { fetch(endpoint, { method: 'POST', body: data, keepalive: true, mode: 'no-cors', headers: { 'Content-Type': 'text/plain' } }); } catch (e) { /* ignore */ }
  }

  var path = location.pathname;
  var viewId = uuid();
  var visibleMs = 0, shownAt = document.visibilityState === 'visible' ? Date.now() : null;

  function startView() {
    var id = ids();
    var extra = {};
    // The referrer and campaign tags describe how a visit started, so they go with its first page.
    if (id.fresh || !id.s) {
      extra.ref = document.referrer || '';
      var q = new URLSearchParams(location.search), utm = {};
      ['source', 'medium', 'campaign'].forEach(function (k) { var v = q.get('utm_' + k); if (v) utm[k] = v.slice(0, 120); });
      if (utm.source || utm.medium || utm.campaign) extra.utm = utm;
    }
    send([{ k: 'view', id: viewId, p: path, t: document.title.replace(/\s*\|\s*CareerOS\s*$/, '').slice(0, 160) }], extra);
  }

  function flush() {
    if (shownAt !== null) { visibleMs += Date.now() - shownAt; shownAt = null; }
    var sec = Math.round(visibleMs / 1000);
    if (sec > 0) send([{ k: 'dur', id: viewId, sec: sec }]);
  }
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') flush();
    else if (shownAt === null) shownAt = Date.now();
  });
  window.addEventListener('pagehide', flush);

  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    var url;
    try { url = new URL(a.getAttribute('href'), location.href); } catch (err) { return; }
    var external = /^(intent|market|mailto):$/.test(url.protocol) || (/^https?:$/.test(url.protocol) && url.host !== location.host);
    if (external) send([{ k: 'out', p: path, u: url.href.slice(0, 500) }]);
  }, true);

  // ---- consent banner
  function banner() {
    if (document.querySelector('.consent')) return;
    var box = document.createElement('div');
    box.className = 'consent';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-label', 'Analytics choice');
    var privacy = new URL('privacy.html#website-analytics', scriptSrc).href;
    box.innerHTML =
      '<p><strong>Help us improve this site?</strong> With your OK we measure which pages are read, for how long, ' +
      'and which links are used, with a random ID kept in your browser. No ads and no tracking on other sites. ' +
      '<a href="' + privacy + '">Privacy policy</a></p>' +
      '<div class="consent-actions"><button type="button" data-choice="no">Decline</button>' +
      '<button type="button" data-choice="yes">Accept</button></div>';
    box.addEventListener('click', function (e) {
      var value = e.target.getAttribute && e.target.getAttribute('data-choice');
      if (!value) return;
      store(function (s) {
        s.setItem(KEY, value);
        if (value === 'no') { s.removeItem(VID); s.removeItem(SID); s.removeItem(LAST); }
      });
      box.remove();
      // The page already counted anonymously is linked to the new visit (same view id), not counted twice.
      if (value === 'yes' && !accepted) { accepted = true; startView(); }
    });
    document.body.appendChild(box);
  }
  var accepted = choice() === 'yes';
  document.querySelectorAll('[data-consent-settings]').forEach(function (el) {
    el.hidden = false;
    el.addEventListener('click', function (e) { e.preventDefault(); banner(); });
  });

  startView();
  if (!choice() && !gpc) banner();
})();
