/* Keep frozen SPA links on the same shared, server-selected account form. */
(function () {
  'use strict';
  if (window.__leziwuAccountDocumentNavigation) return;
  window.__leziwuAccountDocumentNavigation = true;
  // The document already came from the access route; never reload it in a loop.
  if (window.location.pathname.replace(/\/+$/, '') === '/access') return;
  var pending = false;
  var landingObserver = null;
  function refreshLandingCopy() {
    if (window.location.pathname !== '/') return;
    var root = document.getElementById('root');
    if (!root) return;
    var copy = root.querySelector('.landing-final p');
    if (copy && copy.textContent === '姓名与密码即可提交注册申请，审核通过后使用个人研究空间。') {
      copy.textContent = '登录或注册，开启你的个人研究空间。';
    }
    var link = root.querySelector('.landing-final-actions a[href*="mode=register"]');
    if (link && link.firstChild && link.firstChild.nodeType === 3 && link.firstChild.nodeValue === '注册申请') {
      link.firstChild.nodeValue = '注册账号';
    }
  }
  function watchLanding() {
    if (window.location.pathname !== '/') {
      if (landingObserver) landingObserver.disconnect();
      landingObserver = null;
      return;
    }
    var root = document.getElementById('root');
    if (root && !landingObserver) {
      landingObserver = new MutationObserver(refreshLandingCopy);
      landingObserver.observe(root, {childList: true, subtree: true});
    }
    refreshLandingCopy();
  }
  function enterAccountDocument() {
    watchLanding();
    if (pending || window.location.pathname.replace(/\/+$/, '') !== '/access') return;
    pending = true;
    window.location.replace(window.location.pathname + window.location.search + window.location.hash);
  }
  ['pushState', 'replaceState'].forEach(function (method) {
    var original = window.history[method];
    window.history[method] = function () {
      var result = original.apply(this, arguments);
      queueMicrotask(enterAccountDocument);
      return result;
    };
  });
  window.addEventListener('popstate', enterAccountDocument);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watchLanding, {once: true});
  else watchLanding();
})();
