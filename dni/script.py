"""The website snippet (CH-460): /api/dni/avx-dni.js

    <script src="https://<our api>/api/dni/avx-dni.js" data-pool="pk_..." async></script>

Swaps every phone number the pool's landers listed (and the fallback number
written in the page), in text and in tel: links, plus any element with
class="avx-number". Keeps the number while the page is open (every 60 s),
and tells us when the page closes. Leaves the page untouched if anything
fails, so the number in the HTML still works.
"""

SCRIPT = r"""/*! Avortyx number pool */
(function () {
  var s = document.currentScript;
  if (!s || !window.fetch) return;
  var POOL = s.getAttribute('data-pool');
  var API = s.getAttribute('data-api') || s.src.replace(/\/api\/dni\/avx-dni\.js.*$/, '');
  if (!POOL) return;

  function vid() {
    var k = 'avx_vid', v = null;
    try { v = localStorage.getItem(k); } catch (e) {}
    if (!v || !/^[A-Za-z0-9_-]{8,64}$/.test(v)) {
      v = Date.now().toString(36) + Math.random().toString(36).slice(2, 12) + Math.random().toString(36).slice(2, 6);
      try { localStorage.setItem(k, v); } catch (e) {}
    }
    return v;
  }
  var VID = vid();
  var current = null;

  function pattern(d) {
    return new RegExp('(\\+?1[\\s.\\-]?)?\\(?' + d.slice(0, 3) + '\\)?[\\s.\\-]?' + d.slice(3, 6) + '[\\s.\\-]?' + d.slice(6), 'g');
  }
  function digits(t) { var d = String(t || '').replace(/\D/g, ''); return d.length === 11 && d[0] === '1' ? d.slice(1) : d; }

  function apply(d) {
    if (!d || !d.number) return;
    var targets = (d.swap || []).concat(current ? [current] : []).filter(function (x) { return x && x !== d.number; });
    var els = document.querySelectorAll('.avx-number');
    for (var i = 0; i < els.length; i++) {
      els[i].textContent = d.display;
      if (els[i].tagName === 'A') els[i].setAttribute('href', 'tel:+1' + d.number);
    }
    if (targets.length) {
      var links = document.querySelectorAll('a[href^="tel:"]');
      for (var j = 0; j < links.length; j++) {
        if (targets.indexOf(digits(links[j].getAttribute('href').slice(4))) >= 0) links[j].setAttribute('href', 'tel:+1' + d.number);
      }
      var walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null), node, nodes = [];
      while ((node = walk.nextNode())) {
        var p = node.parentNode && node.parentNode.nodeName;
        if (p !== 'SCRIPT' && p !== 'STYLE' && /\d{3}/.test(node.nodeValue)) nodes.push(node);
      }
      for (var k = 0; k < nodes.length; k++) {
        var text = nodes[k].nodeValue;
        for (var t = 0; t < targets.length; t++) text = text.replace(pattern(targets[t]), d.display);
        if (text !== nodes[k].nodeValue) nodes[k].nodeValue = text;
      }
    }
    current = d.number;
  }

  function qs(full) {
    var q = new URLSearchParams();
    q.set('pool', POOL); q.set('vid', VID);
    if (full) {
      q.set('q', location.search.slice(0, 1000));
      q.set('url', location.href.split('?')[0].slice(0, 1000));
      q.set('ref', (document.referrer || '').slice(0, 1000));
    }
    return q.toString();
  }

  function getNumber() {
    return fetch(API + '/api/dni/v1/number?' + qs(true), { credentials: 'omit' })
      .then(function (r) { return r.ok ? r.json() : null; }).then(apply).catch(function () {});
  }
  function ping() {
    if (document.visibilityState !== 'visible') return;
    fetch(API + '/api/dni/v1/ping?' + qs(false), { credentials: 'omit' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.ok) apply(d); else if (d) getNumber(); })
      .catch(function () {});
  }
  function bye() {
    var u = API + '/api/dni/v1/release?' + qs(false);
    if (navigator.sendBeacon) navigator.sendBeacon(u); else fetch(u, { keepalive: true, credentials: 'omit' });
  }
  function start() { getNumber(); setInterval(ping, 60000); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
  document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'visible') ping(); });
  window.addEventListener('pagehide', bye);
})();
"""
