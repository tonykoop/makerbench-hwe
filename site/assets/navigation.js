/* Shared phone menu, theme control and legacy hash alignment (#1023). */
(function () {
  "use strict";
  var root = document.querySelector('.site-nav');
  if (!root) return;
  var button = document.getElementById('site-menu-button');
  var nav = document.getElementById('site-navigation');
  var more = root.querySelector('.site-nav-more');
  var theme = document.getElementById('theme-toggle');
  var phone = matchMedia('(max-width: 720px)');
  var outside = [];
  var overflow = '';
  var settlingHash = true;
  root.classList.add('is-enhanced');
  button.hidden = false;
  theme.hidden = false;
  try {
    var saved = localStorage.getItem('mb-theme');
    document.documentElement.setAttribute('data-theme', saved || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
  } catch (e) {}

  function updateHeaderHeight() {
    document.documentElement.style.setProperty('--site-nav-height', root.getBoundingClientRect().height + 'px');
  }
  function closePhoneMenu(restoreFocus) {
    if (button.getAttribute('aria-expanded') !== 'true') return;
    button.setAttribute('aria-expanded', 'false');
    nav.removeAttribute('data-open');
    outside.forEach(function (item) { item.node.inert = item.inert; });
    outside = [];
    document.body.style.overflow = overflow;
    more.open = false;
    if (restoreFocus) button.focus();
  }
  button.addEventListener('click', function () {
    if (button.getAttribute('aria-expanded') === 'true') {
      closePhoneMenu(true);
      return;
    }
    overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    outside = Array.from(document.body.children).filter(function (node) { return node !== root; })
      .map(function (node) { var previous = {node: node, inert: node.inert}; node.inert = true; return previous; });
    button.setAttribute('aria-expanded', 'true');
    nav.setAttribute('data-open', '');
    nav.querySelector('a').focus();
  });
  document.addEventListener('keydown', function (event) {
    var opened = phone.matches && button.getAttribute('aria-expanded') === 'true';
    if (event.key === 'Escape') {
      if (opened) { event.preventDefault(); closePhoneMenu(true); }
      else if (more.open) { more.open = false; more.querySelector('summary').focus(); }
    }
    if (event.key !== 'Tab' || !opened) return;
    var stops = Array.from(root.querySelectorAll('a[href], button, summary')).filter(function (node) {
      return !node.hidden && node.getClientRects().length;
    });
    var first = stops[0], last = stops[stops.length - 1];
    if (event.shiftKey && (document.activeElement === first || !root.contains(document.activeElement))) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  });
  nav.addEventListener('click', function (event) {
    if (event.target.closest('a')) { closePhoneMenu(true); more.open = false; }
  });
  document.addEventListener('click', function (event) {
    if (!more.contains(event.target)) more.open = false;
  });
  phone.addEventListener('change', function () { if (!phone.matches) closePhoneMenu(false); updateHeaderHeight(); });
  new ResizeObserver(updateHeaderHeight).observe(root);
  updateHeaderHeight();
  theme.addEventListener('click', function () {
    var next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('mb-theme', next); } catch (e) {}
    window.dispatchEvent(new Event('makerbench:themechange'));
  });

  function alignLegacyHash() {
    if (!settlingHash || !location.hash) return;
    var id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch (e) { return; }
    var target = document.getElementById(id);
    if (!target) return;
    if (target.classList.contains('home-detail')) {
      var disclosure = target.querySelector('details');
      if (disclosure) disclosure.open = true;
    }
    requestAnimationFrame(function () {
      if (settlingHash) target.scrollIntoView({block: 'start', behavior: 'instant'});
    });
  }
  ['wheel', 'touchstart', 'pointerdown', 'keydown'].forEach(function (name) {
    window.addEventListener(name, function () { settlingHash = false; }, {passive: true});
  });
  // Hydration can change the height above a legacy anchor after its fetch resolves.
  new ResizeObserver(function () { requestAnimationFrame(alignLegacyHash); }).observe(document.body);
  window.addEventListener('hashchange', function () { settlingHash = true; alignLegacyHash(); });
  window.addEventListener('makerbench:contentready', alignLegacyHash);
  window.addEventListener('load', alignLegacyHash);
  alignLegacyHash();
})();
