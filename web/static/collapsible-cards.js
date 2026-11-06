/* Collapsible cards, shared by every Clockwork page.
 *
 * A card folds to its title row when the title is clicked (or focused and Enter/Space is pressed). A card
 * qualifies when it is a .card, .panel, .detail-card, .tpl-card or a section directly under main, or a home-page .group whose first child is a heading, or a
 * head row (.card-head, .detail-card-head) holding one. Cards with no heading, modal dialogs, full-screen
 * and pop-out views are left alone. Opt a card out with data-cc="off"; a full-page view that is
 * marked as a dialog opts its cards back in with data-cc="on".
 *
 * The choice is remembered per page in this browser (localStorage). Cards are rebuilt often by page
 * scripts, so a cheap MutationObserver re-applies the state instead of binding each card.
 */
(function () {
  'use strict';
  const CARDS = 'section.card, article.card, section.panel, aside.panel, div.panel, .detail-card, .tpl-card, main > section, .page > section, .group';
  const HEADINGS = 'h1, h2, h3, h4, .detail-card-title, .group-label';
  const HEADS = '.card-head, .detail-card-head';
  const KEY = 'cw.cardsClosed';
  const SKIP = '.fs-card, .chart-fs, [data-cc="off"]';
  const MODAL = 'dialog, [role="dialog"], [aria-modal], .modal';  // cards inside a modal stay put unless it says data-cc="on"

  const readAll = () => { try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (err) { return {}; } };
  const read = () => new Set(readAll()[location.pathname] || []);
  function write(set) {
    const all = readAll();
    if (set.size) all[location.pathname] = [...set]; else delete all[location.pathname];
    try { localStorage.setItem(KEY, JSON.stringify(all)); } catch (err) { /* private mode: not remembered */ }
  }

  // The title element of a qualifying card, and the direct child that is its head row.
  function parts(card) {
    const first = card.firstElementChild;
    if (!first || card.closest(SKIP)) return null;
    const modal = card.closest(MODAL);
    if (modal && !modal.closest('[data-cc="on"]')) return null;
    if (first.matches(HEADINGS)) return {title: first, head: first};
    if (first.matches(HEADS)) { const t = first.querySelector(HEADINGS); if (t) return {title: t, head: first}; }
    return null;
  }
  const keyOf = (card, title) => card.id || (title.textContent || '').trim().toLowerCase();

  function apply() {
    const closed = read();
    document.querySelectorAll(CARDS).forEach(card => {
      const p = parts(card);
      if (!p || !(p.title.textContent || '').trim()) { card.classList.remove('cc-collapsed'); return; }
      card.classList.add('cc-card');
      p.head.classList.add('cc-head');
      if (!p.title.classList.contains('cc-title')) {
        p.title.classList.add('cc-title'); p.title.setAttribute('role', 'button'); p.title.tabIndex = 0;
      }
      const shut = closed.has(keyOf(card, p.title));
      card.classList.toggle('cc-collapsed', shut);
      p.title.setAttribute('aria-expanded', String(!shut));
    });
  }

  function toggle(title) {
    const card = title.closest('.cc-card');
    if (!card || card.matches('.fs-card, .chart-fs') || document.body.matches('.popout, .popout-chart')) return;
    const set = read(), key = keyOf(card, title);
    if (set.has(key)) set.delete(key); else set.add(key);
    write(set);
    apply();
  }

  document.addEventListener('click', e => { const t = e.target.closest && e.target.closest('.cc-title'); if (t) toggle(t); });
  document.addEventListener('keydown', e => {
    const t = e.target.closest && e.target.closest('.cc-title');
    if (t && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); toggle(t); }
  });

  let queued = false;
  const schedule = muts => {
    if (queued || !muts.some(m => m.addedNodes.length)) return;
    queued = true; requestAnimationFrame(() => { queued = false; apply(); });
  };
  function start() {
    apply();
    new MutationObserver(schedule).observe(document.body, {childList: true, subtree: true});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
