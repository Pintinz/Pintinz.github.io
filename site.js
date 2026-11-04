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
