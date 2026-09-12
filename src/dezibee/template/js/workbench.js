/* ═══════════════════════════════════════════════════════════════════
 * DeziBee Prototype Workspace 引擎
 * 职责（框架能力，业务无关）：
 *   1. 渲染导航树（分组/页面/状态），页面切换
 *   2. 无限画布：平移/缩放/原点/适应内容/聚焦卡片
 *   3. 原型卡片：按 WORKBENCH_DATA.pages[].cards 渲染（iframe 沙箱承载真实 HTML）
 *   4. 卡片关系连线（SVG，来源/触发/条件 → 提示悬停）
 *   5. 双向关联（唯一 ID）：卡片点击 → PRD 定位高亮；PRD 引用 → 画布聚焦
 * 数据全部来自 index.html 的 WORKBENCH_DATA；AI 只改数据，不改本文件。
 * ═══════════════════════════════════════════════════════════════════ */
'use strict';

/* ── 工具 ── */
const $ = (sel) => document.querySelector(sel);
const SVG_NS = 'http://www.w3.org/2000/svg';

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

/* ── 全局状态 ── */
const D = window.WORKBENCH_DATA;
const state = {
  currentPageId: D.pages.length ? D.pages[0].id : null,
  scale: 1,
  tx: 0, ty: 0,          // world 平移
  selectedCardId: null,
  dragging: null,        // {cardId, dx, dy}
  panning: null,         // {sx, sy, tx, ty}
};

/* 卡片索引：id → {card, page}（全页面共用一个索引，跳转/连线跨页可达）。
   每次渲染前重建：AI 修改 WORKBENCH_DATA 后无需关心缓存。 */
const cardIndex = new Map();

function buildCardIndex() {
  cardIndex.clear();
  D.pages.forEach(p => (p.cards || []).forEach(c => cardIndex.set(c.id, { card: c, page: p })));
}

/* ═══════════════ 1. 导航树 ═══════════════ */

function buildNavTree() {
  const tree = $('#navTree');
  tree.innerHTML = '';
  // 按 group 路径分组（支持 "一级/二级" 多级；框架不限制层级）
  const groups = new Map(); // groupPath → pages[]
  for (const p of D.pages) {
    const key = (p.group || '').replace(/^\/+|\/+$/g, '');
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(p);
  }
  const paths = [...groups.keys()].sort();
  for (const path of paths) {
    if (path) {
      // 多级分组逐级渲染标签
      const parts = path.split('/');
      for (let i = 0; i < parts.length; i++) {
        tree.appendChild(el('div', 'nav-group-label', '  '.repeat(i) + parts[i]));
      }
    }
    for (const p of groups.get(path)) {
      const item = el('div', 'nav-item');
      item.dataset.pageId = p.id;
      item.appendChild(el('span', 'dot'));
      item.appendChild(el('span', '', p.name || p.id));
      const st = p.cards?.[0]?.status;
      if (st && st !== 'draft') {
        item.appendChild(el('span', 'status-tag ' + st, st));
      }
      if (p.id === state.currentPageId) item.classList.add('active');
      item.addEventListener('click', () => switchPage(p.id));
      tree.appendChild(item);
    }
  }
  $('#navPageCount').textContent = D.pages.length + ' 页';
}

function switchPage(pageId) {
  if (!cardIndexHasPage(pageId)) return;
  state.currentPageId = pageId;
  state.selectedCardId = null;
  document.querySelectorAll('.nav-item').forEach(n => {
    n.classList.toggle('active', n.dataset.pageId === pageId);
  });
  renderPage();
}

function cardIndexHasPage(pageId) { return D.pages.some(p => p.id === pageId); }

/* ═══════════════ 2. 画布：平移/缩放/聚焦 ═══════════════ */

function applyTransform() {
  $('#world').style.transform = `translate(${state.tx}px, ${state.ty}px) scale(${state.scale})`;
  $('#zoomLabel').textContent = Math.round(state.scale * 100) + '%';
  drawLinks();
}

function zoomAt(factor, cx, cy) {
  // 以视口点 (cx,cy) 为中心缩放
  const ns = Math.min(4, Math.max(0.15, state.scale * factor));
  const k = ns / state.scale;
  state.tx = cx - (cx - state.tx) * k;
  state.ty = cy - (cy - state.ty) * k;
  state.scale = ns;
  applyTransform();
}

function viewportCenter() {
  const v = $('#viewport');
  return { x: v.clientWidth / 2, y: v.clientHeight / 2 };
}

function fitAll() {
  // 适应当前页面全部卡片
  const page = D.pages.find(p => p.id === state.currentPageId);
  const cards = page?.cards || [];
  if (!cards.length) { resetOrigin(); return; }
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const c of cards) {
    minX = Math.min(minX, c.x); minY = Math.min(minY, c.y);
    maxX = Math.max(maxX, c.x + (c.w || 360)); maxY = Math.max(maxY, c.y + (c.h || 560));
  }
  const v = $('#viewport');
  const pad = 60;
  const sx = v.clientWidth / (maxX - minX + pad * 2);
  const sy = v.clientHeight / (maxY - minY + pad * 2);
  state.scale = Math.min(1.5, Math.max(0.15, Math.min(sx, sy)));
  state.tx = pad + (v.clientWidth - (maxX - minX) * state.scale) / 2 - minX * state.scale;
  state.ty = pad + (v.clientHeight - (maxY - minY) * state.scale) / 2 - minY * state.scale;
  applyTransform();
}

function resetOrigin() { state.scale = 1; state.tx = 0; state.ty = 0; applyTransform(); }

function focusCard(cardId, opts = {}) {
  // 聚焦卡片：切到所属页面（若需要）→ 居中 → 高亮闪烁
  const rec = cardIndex.get(cardId);
  if (!rec) return;
  if (rec.page.id !== state.currentPageId) switchPage(rec.page.id);
  state.selectedCardId = cardId;
  const v = $('#viewport');
  const c = rec.card;
  const w = (c.w || 360), h = (c.h || 560);
  state.scale = opts.scale || Math.min(1.2, Math.max(0.3,
    Math.min((v.clientWidth - 120) / w, (v.clientHeight - 120) / h)));
  state.tx = v.clientWidth / 2 - (c.x + w / 2) * state.scale;
  state.ty = v.clientHeight / 2 - (c.y + h / 2) * state.scale;
  applyTransform();
  markSelected();
  const node = $(`#world [data-card-id="${CSS.escape(cardId)}"]`);
  if (node) {
    node.classList.remove('flash'); void node.offsetWidth; node.classList.add('flash');
  }
}

function markSelected() {
  document.querySelectorAll('.proto-card').forEach(n =>
    n.classList.toggle('selected', n.dataset.cardId === state.selectedCardId));
}

/* 视口交互：空白拖拽平移；滚轮缩放；快捷键 */
function bindViewport() {
  const vp = $('#viewport');
  vp.addEventListener('mousedown', (e) => {
    if (e.target.closest('.proto-card')) return;
    state.panning = { sx: e.clientX, sy: e.clientY, tx: state.tx, ty: state.ty };
    vp.classList.add('panning');
  });
  window.addEventListener('mousemove', (e) => {
    if (state.panning) {
      state.tx = state.panning.tx + (e.clientX - state.panning.sx);
      state.ty = state.panning.ty + (e.clientY - state.panning.sy);
      applyTransform();
    } else if (state.dragging) {
      const d = state.dragging;
      const rec = cardIndex.get(d.cardId);
      if (!rec) return;
      rec.card.x = Math.round(d.cx + (e.clientX - d.sx) / state.scale);
      rec.card.y = Math.round(d.cy + (e.clientY - d.sy) / state.scale);
      const node = $(`#world [data-card-id="${CSS.escape(d.cardId)}"]`);
      if (node) { node.style.left = rec.card.x + 'px'; node.style.top = rec.card.y + 'px'; }
      drawLinks();
    }
  });
  window.addEventListener('mouseup', () => {
    state.panning = null; state.dragging = null;
    vp.classList.remove('panning');
  });
  vp.addEventListener('wheel', (e) => {
    e.preventDefault();
    const rect = vp.getBoundingClientRect();
    zoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - rect.left, e.clientY - rect.top);
  }, { passive: false });
  vp.addEventListener('dblclick', (e) => {
    if (e.target.closest('.proto-card')) return;
    fitAll();
  });
  window.addEventListener('keydown', (e) => {
    if (e.target.matches('input, textarea')) return;
    if (e.key === '0' && !e.ctrlKey && !e.metaKey) resetOrigin();
    if (e.key === '1' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); fitAll(); }
    if (e.key === 'Escape') { state.selectedCardId = null; markSelected(); }
  });
}

/* ═══════════════ 3. 原型卡片渲染 ═══════════════ */

function renderPage() {
  buildCardIndex();
  const world = $('#world');
  // 只清卡片，保留连线层 SVG（它在 world 内、随画布变换）
  world.querySelectorAll('.proto-card').forEach(n => n.remove());
  const page = D.pages.find(p => p.id === state.currentPageId);
  $('#canvasPageName').textContent = page ? (page.name || page.id) : '';
  const cards = page?.cards || [];

  if (!cards.length) {
    const hint = $('#cardHint');
    hint.classList.remove('hidden');
    hint.textContent = '本页面还没有原型卡片 — 在 DeziBee 对话里让 AI 创建';
  } else {
    $('#cardHint').classList.add('hidden');
  }

  for (const c of cards) {
    const node = el('div', 'proto-card');
    node.dataset.cardId = c.id;
    node.style.left = (c.x || 0) + 'px';
    node.style.top = (c.y || 0) + 'px';
    node.style.width = (c.w || 360) + 'px';
    node.style.height = (c.h || 560) + 'px';

    const head = el('div', 'card-head');
    head.appendChild(el('span', 'card-name', c.name || c.id));
    if (c.type) head.appendChild(el('span', 'card-type', c.type));
    if (c.status) head.appendChild(el('span', 'card-status', c.status));
    node.appendChild(head);

    const body = el('div', 'card-body');
    // 真实 HTML 用 iframe 沙箱承载：样式隔离 + 交互真实可用（禁止图片模拟页面）
    const frame = document.createElement('iframe');
    frame.setAttribute('sandbox', 'allow-scripts allow-forms allow-modals allow-popups');
    body.appendChild(frame);
    node.appendChild(body);
    world.appendChild(node);

    frame.srcdoc =
      '<!DOCTYPE html><html><head><meta charset="UTF-8">' +
      '<style>html,body{margin:0;padding:0;overflow-x:hidden}</style>' +
      '</head><body>' + (c.html || '') + '</body></html>';

    // 卡片交互：单击选中；点击选中后再次点击 → PRD 联动；头部拖拽移动
    node.addEventListener('mousedown', (e) => {
      e.stopPropagation();
      if (state.selectedCardId === c.id && !e.target.closest('.card-head')) {
        openPrdForCard(c.id);   // 已选中 → 双向联动到 PRD
        return;
      }
      state.selectedCardId = c.id;
      markSelected();
      if (e.target.closest('.card-head')) {
        const rect = node.getBoundingClientRect();
        state.dragging = {
          cardId: c.id, sx: e.clientX, sy: e.clientY,
          cx: c.x, cy: c.y, rx: rect.left, ry: rect.top,
        };
      }
    });
  }
  drawLinks();
}

/* ═══════════════ 4. 关系连线（SVG） ═══════════════ */

function cardAnchor(cardId) {
  const rec = cardIndex.get(cardId);
  if (!rec) return null;
  const c = rec.card;
  return {
    cx: c.x + (c.w || 360) / 2,
    cy: c.y + (c.h || 560) / 2,
    onCurrentPage: rec.page.id === state.currentPageId,
    node: $(`#world [data-card-id="${CSS.escape(cardId)}"]`),
  };
}

function drawLinks() {
  const svg = $('#links');
  svg.innerHTML = '';
  const page = D.pages.find(p => p.id === state.currentPageId);
  if (!page) return;
  const pageCardIds = new Set((page.cards || []).map(c => c.id));
  const world = $('#world');

  for (const lnk of D.links || []) {
    // 连线只画两端都在当前页面的（跨页关系在 PRD 里表达）
    if (!pageCardIds.has(lnk.from) || !pageCardIds.has(lnk.to)) continue;
    const a = cardAnchor(lnk.from), b = cardAnchor(lnk.to);
    if (!a || !b || !a.node || !b.node) continue;

    const x1 = a.cx, y1 = a.cy, x2 = b.cx, y2 = b.cy;
    const dx = x2 - x1, dy = y2 - y1;
    const len = Math.max(1, Math.hypot(dx, dy));
    const ux = dx / len, uy = dy / len;
    // 从卡片边缘起止
    const aw = (parseFloat(a.node.style.width) || 360) / 2;
    const ah = (parseFloat(a.node.style.height) || 560) / 2;
    const bw = (parseFloat(b.node.style.width) || 360) / 2;
    const bh = (parseFloat(b.node.style.height) || 560) / 2;
    const sx = x1 + ux * Math.min(Math.abs(aw / (ux || 1e-6)), Math.abs(ah / (uy || 1e-6))) * 1;
    const sy = y1 + uy * Math.min(Math.abs(ah / (uy || 1e-6)), Math.abs(aw / (ux || 1e-6))) * 1;
    const ex = x2 - ux * Math.min(Math.abs(bw / (ux || 1e-6)), Math.abs(bh / (uy || 1e-6))) * 1;
    const ey = y2 - uy * Math.min(Math.abs(bh / (uy || 1e-6)), Math.abs(bw / (ux || 1e-6))) * 1;

    // 贝塞尔曲线
    const mx = (sx + ex) / 2 + (dy / len) * 30;
    const my = (sy + ey) / 2 - (dx / len) * 30;
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', `M ${sx} ${sy} Q ${mx} ${my} ${ex} ${ey}`);
    path.setAttribute('class', 'link-line');
    path.dataset.linkId = lnk.id || '';

    // 箭头
    const ang = Math.atan2(ey - my, ex - mx);
    const arrow = document.createElementNS(SVG_NS, 'polygon');
    const ax = ex, ay = ey, as = 7;
    arrow.setAttribute('points',
      `${ax},${ay} ${ax - as * Math.cos(ang - 0.45)},${ay - as * Math.sin(ang - 0.45)} ` +
      `${ax - as * Math.cos(ang + 0.45)},${ay - as * Math.sin(ang + 0.45)}`);
    arrow.setAttribute('class', 'link-arrow');

    // 标签：触发（+条件）
    const label = document.createElementNS(SVG_NS, 'text');
    label.setAttribute('x', mx); label.setAttribute('y', my - 6);
    label.setAttribute('text-anchor', 'middle');
    label.setAttribute('class', 'link-label');
    label.textContent = lnk.trigger || lnk.note || '';

    svg.appendChild(path); svg.appendChild(arrow); svg.appendChild(label);

    // 悬停显示完整信息；点击 → 聚焦目标卡片并联动 PRD
    const tip = [lnk.trigger, lnk.condition, lnk.note].filter(Boolean).join('　|　');
    if (tip) {
      path.addEventListener('mouseenter', () => { label.textContent = tip; path.classList.add('hl'); arrow.classList.add('hl'); label.classList.add('hl'); });
      path.addEventListener('mouseleave', () => { label.textContent = lnk.trigger || lnk.note || ''; path.classList.remove('hl'); arrow.classList.remove('hl'); label.classList.remove('hl'); });
    }
    path.addEventListener('click', () => focusCard(lnk.to));
  }
}

/* ═══════════════ 5. PRD 渲染 + 双向关联 ═══════════════ */

function renderPrd() {
  const body = $('#prdBody');
  const toc = $('#prdToc');
  body.innerHTML = ''; toc.innerHTML = '';
  const sections = D.prd?.sections || [];

  for (const sec of sections) {
    // 目录项
    const t = el('div', `toc-item lvl${sec.level || 2}`, sec.title || sec.id);
    t.dataset.secId = sec.id;
    t.addEventListener('click', () => openPrdSection(sec.id));
    toc.appendChild(t);

    // 章节
    const wrap = el('section', `prd-section`);
    wrap.id = 'sec-' + sec.id;
    wrap.dataset.secId = sec.id;
    const h = el('h' + (sec.level || 2), '', sec.title || sec.id);
    wrap.appendChild(h);
    const content = document.createElement('div');
    content.innerHTML = sec.html || '';
    // 把 <a data-prd-ref="card-xxx"> 或 <span class="prd-ref" data-card="..."> 转成可点击引用
    content.querySelectorAll('[data-card-ref]').forEach(refEl => {
      refEl.classList.add('prd-ref');
      refEl.addEventListener('click', () => focusCard(refEl.dataset.cardRef));
    });
    wrap.appendChild(content);
    body.appendChild(wrap);
  }
}

/* 卡片 → PRD：定位对应章节并高亮（唯一 ID：card.prdId ↔ section.id） */
function openPrdForCard(cardId) {
  const rec = cardIndex.get(cardId);
  if (!rec) return;
  const prdId = rec.card.prdId;
  if (!prdId) return;
  openPrdSection(prdId, { fromCard: cardId });
}

function openPrdSection(secId, opts = {}) {
  const node = document.getElementById('sec-' + CSS.escape(secId));
  if (!node) return;
  node.scrollIntoView({ behavior: 'smooth', block: 'start' });
  node.classList.remove('section-flash'); void node.offsetWidth;
  node.classList.add('section-flash');
  // TOC 高亮
  document.querySelectorAll('.toc-item').forEach(t =>
    t.classList.toggle('active', t.dataset.secId === secId));
  // 反向：来源卡片闪烁
  if (opts.fromCard) {
    const n = $(`#world [data-card-id="${CSS.escape(opts.fromCard)}"]`);
    if (n) { n.classList.remove('flash'); void n.offsetWidth; n.classList.add('flash'); }
  }
}

/* ═══════════════ 6. 启动 ═══════════════ */

function init() {
  $('#navProjectTitle').textContent = D.project?.title || '原型导航';
  buildNavTree();
  renderPrd();
  renderPage();
  bindViewport();
  // 首屏自动适应内容
  requestAnimationFrame(() => fitAll());

  $('#zoomInBtn').addEventListener('click', () => { const c = viewportCenter(); zoomAt(1.2, c.x, c.y); });
  $('#zoomOutBtn').addEventListener('click', () => { const c = viewportCenter(); zoomAt(1 / 1.2, c.x, c.y); });
  $('#fitAllBtn').addEventListener('click', fitAll);
  $('#originBtn').addEventListener('click', resetOrigin);
  $('#focusBtn').addEventListener('click', () => {
    if (state.selectedCardId) focusCard(state.selectedCardId);
  });
  $('#prdTocBtn').addEventListener('click', () => $('#prdToc').classList.toggle('hidden'));
}

document.addEventListener('DOMContentLoaded', init);
