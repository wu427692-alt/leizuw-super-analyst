/* Transitional navigation for a frozen SPA. No API, cookie or storage access. */
(() => {
  'use strict';

  const singleton = '__leziwuAiNewsNavBridge';
  if (window[singleton]) return;

  const selector = 'nav[aria-label="主导航"], nav[aria-label="Main navigation"]';
  const marker = 'data-leziwu-ai-news-nav';
  const svgNamespace = 'http://www.w3.org/2000/svg';
  let scheduledFrame = 0;

  function isCurrent(link) {
    return (link.hasAttribute('aria-current') && link.getAttribute('aria-current') !== 'false')
      || link.getAttribute('data-active') === 'true'
      || link.classList.contains('active')
      || link.classList.contains('is-active');
  }

  function inactiveClass(value, icon = false) {
    const classes = String(value || '').split(/\s+/).filter(Boolean).filter((token) => (
      token !== 'active' && token !== 'is-active' && token !== 'font-medium'
      && !token.includes('--nav-active-') && !token.includes('--nav-icon-active')
      && !/^text-\[hsl\(var\(--primary\)\)\]$/.test(token)
    ));
    if (icon && !classes.includes('text-current')) classes.push('text-current');
    return classes.join(' ');
  }

  function writeAttribute(element, name, value) {
    if (element.getAttribute(name) !== value) element.setAttribute(name, value);
  }

  function createIcon() {
    const svg = document.createElementNS(svgNamespace, 'svg');
    for (const [name, value] of Object.entries({
      width: '20', height: '20', viewBox: '0 0 24 24', fill: 'none',
      stroke: 'currentColor', 'stroke-width': '1.7', 'stroke-linecap': 'round',
      'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false',
    })) svg.setAttribute(name, value);
    // Independent newspaper outline, not a cloned source-menu icon or logo.
    for (const pathData of [
      'M5 3h15v16a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9h2',
      'M5 3v16a2 2 0 0 1-2 2',
      'M8 7h9M8 11h3v4H8zM14 11h3M14 15h3M8 18h9',
    ]) {
      const path = document.createElementNS(svgNamespace, 'path');
      path.setAttribute('d', pathData);
      svg.appendChild(path);
    }
    return svg;
  }

  function ensureLink(nav) {
    const links = Array.from(nav.querySelectorAll('a[href]'));
    const source = links.find((link) => link.getAttribute('href') === '/essay-radar');
    if (!source) return;
    const existing = links.filter((link) => link.getAttribute('href') === '/ai-news');
    // A future SPA may ship this route natively. Never replace its owned DOM.
    const nativeLink = existing.find((link) => !link.hasAttribute(marker));
    if (nativeLink) {
      existing.filter((link) => link.hasAttribute(marker)).forEach((link) => link.remove());
      return;
    }

    let link = existing.find((entry) => entry.hasAttribute(marker));
    existing.filter((entry) => entry !== link && entry.hasAttribute(marker)).forEach((entry) => entry.remove());
    if (!link) {
      link = document.createElement('a');
      link.setAttribute(marker, '');
      link.setAttribute('href', '/ai-news');
      link.appendChild(createIcon());
      // The SPA's delegated handlers must not turn this into client routing.
      // Not preventing default preserves modifiers, native navigation and menus.
      link.addEventListener('click', (event) => event.stopPropagation());
    }

    const text = nav.getAttribute('aria-label') === 'Main navigation' ? 'AI News' : 'AI 热点';
    const styleSource = !isCurrent(source) ? source : links.find((entry) => (
      entry !== link && !entry.hasAttribute(marker) && !isCurrent(entry)
      && entry.parentElement === source.parentElement && entry.querySelector('svg')
    ));
    const reference = styleSource || source;
    const classes = styleSource ? reference.getAttribute('class') || '' : inactiveClass(source.getAttribute('class'));
    writeAttribute(link, 'class', classes);
    writeAttribute(link, 'aria-label', text);
    writeAttribute(link, 'title', text);
    link.removeAttribute('aria-current');
    link.removeAttribute('data-active');

    const icon = link.querySelector('svg');
    writeAttribute(icon, 'class', inactiveClass(reference.querySelector('svg')?.getAttribute('class'), true));
    // Match the source's compact/expanded structure without copying badges,
    // event handlers, IDs, selected-state attributes or text from its subtree.
    const referenceLabel = Array.from(source.children).find((element) => element.tagName === 'SPAN');
    let label = link.querySelector('span');
    if (referenceLabel) {
      if (!label) { label = document.createElement('span'); link.appendChild(label); }
      writeAttribute(label, 'class', inactiveClass(referenceLabel.getAttribute('class')));
      if (label.textContent !== text) label.textContent = text;
    } else if (label) label.remove();

    if (source.nextElementSibling !== link) source.insertAdjacentElement('afterend', link);
  }

  function synchronize() {
    scheduledFrame = 0;
    document.querySelectorAll(selector).forEach(ensureLink);
  }

  function schedule() {
    if (!scheduledFrame) scheduledFrame = window.requestAnimationFrame(synchronize);
  }

  function containsMainNav(node) {
    return node instanceof Element && (node.matches(selector) || node.querySelector(selector));
  }

  const observer = new MutationObserver((records) => {
    if (records.some((record) => (
      record.target instanceof Element && record.target.closest(selector)
    ) || Array.from(record.addedNodes).some(containsMainNav)
      || Array.from(record.removedNodes).some(containsMainNav))) schedule();
  });

  window[singleton] = {
    disconnect() {
      observer.disconnect();
      if (scheduledFrame) window.cancelAnimationFrame(scheduledFrame);
      scheduledFrame = 0;
      delete window[singleton];
    },
  };
  observer.observe(document.documentElement, {
    childList: true, subtree: true, attributes: true,
    attributeFilter: ['aria-label', 'aria-current', 'data-active', 'class'],
  });
  schedule();
})();
