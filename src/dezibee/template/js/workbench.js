/* ═══════════════════════════════════════════════════════════════════
 * DeziBee Prototype Workspace 引擎
 * DeziBee Workbench Framework v2 — 框架文件由 DeziBee 维护，请勿修改
 * 职责（框架能力，业务无关）：
 *   1. 渲染导航树（分组/页面/状态），页面切换
 *   2. 无限画布：平移/缩放/原点/适应内容/聚焦卡片
 *      缩放用 CSS zoom（内容按目标尺寸重排渲染，缩放后不发糊）
 *   3. 原型卡片：按 WORKBENCH_DATA.pages[].cards 渲染（iframe 沙箱承载真实 HTML）
 *      可选 shell 字段：phone 手机壳 / tablet 平板壳 / browser 浏览器壳（w/h 为屏幕内容区）
 *   4. 卡片关系连线（SVG，来源/触发/条件 → 提示悬停）
 *   5. 双向关联（唯一 ID）：选中卡片/切换页面 → PRD 定位高亮；PRD 引用 → 画布聚焦
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
  prdEditing: false,     // PRD 直接编辑模式
  flowFullscreen: false, // 全屏流程原型展示模式
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

function switchPage(pageId, opts = {}) {
  if (!cardIndexHasPage(pageId)) return;
  state.currentPageId = pageId;
  state.selectedCardId = null;
  document.querySelectorAll('.nav-item').forEach(n => {
    n.classList.toggle('active', n.dataset.pageId === pageId);
  });
  renderPage();
  // 页面 → PRD 联动：右栏自动跳到该页对应章节（focusCard 聚焦卡片时自带章节定位，跳过）
  if (!opts.skipPrd) syncPrdToPage(pageId);
}

/* 页面 → PRD 章节映射：page.prdId 优先，否则取该页第一张带 prdId 的卡片 */
function syncPrdToPage(pageId) {
  const page = D.pages.find(p => p.id === pageId);
  if (!page) return;
  const prdId = page.prdId || (page.cards || []).map(c => c.prdId).find(Boolean);
  if (prdId) openPrdSection(prdId);
}

function cardIndexHasPage(pageId) { return D.pages.some(p => p.id === pageId); }

/* ═══════════════ 2. 画布：平移/缩放/聚焦 ═══════════════ */

/* 缩放实现：#panner(transform: translate 平移) > #world(zoom 缩放)。
   CSS zoom 让 iframe 内容按目标尺寸重新布局渲染（不同于 transform: scale 的
   位图缩放），缩小/放大后文字与内容依然清晰。zoom 兼容写法见 applyTransform。 */
function ensurePanner() {
  const world = $('#world');
  if (!world || (world.parentElement && world.parentElement.id === 'panner')) return;
  const pan = document.createElement('div');
  pan.id = 'panner';
  world.parentNode.insertBefore(pan, world);
  pan.appendChild(world);
}

function applyTransform() {
  const pan = $('#panner');
  if (pan) pan.style.transform = `translate(${state.tx}px, ${state.ty}px)`;
  const world = $('#world');
  if ('zoom' in world.style) {
    world.style.zoom = String(state.scale);
  } else {
    // 兜底（极旧内核）：退回 transform 缩放
    world.style.transform = `scale(${state.scale})`;
  }
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
  // 适应当前页面全部卡片（含外壳装饰）
  const page = D.pages.find(p => p.id === state.currentPageId);
  const cards = page?.cards || [];
  if (!cards.length) { resetOrigin(); return; }
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const c of cards) {
    const o = cardOuterSize(c);
    minX = Math.min(minX, c.x); minY = Math.min(minY, c.y);
    maxX = Math.max(maxX, c.x + o.w); maxY = Math.max(maxY, c.y + o.h);
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

function setFlowFullscreen(enabled) {
  state.flowFullscreen = enabled;
  document.body.classList.toggle('flow-fullscreen', enabled);
  const btn = $('#flowFullscreenBtn');
  btn.textContent = enabled ? '⛶ 退出全屏' : '⛶ 全屏原型';
  btn.title = enabled ? '退出全屏流程原型展示（Esc）' : '隐藏画布目录和 PRD，进入全屏流程原型展示';
  btn.setAttribute('aria-pressed', String(enabled));
  requestAnimationFrame(() => fitAll());
}

function focusCard(cardId, opts = {}) {
  // 聚焦卡片：切到所属页面（若需要）→ 居中 → 高亮闪烁 → PRD 跳到对应章节
  const rec = cardIndex.get(cardId);
  if (!rec) return;
  if (rec.page.id !== state.currentPageId) switchPage(rec.page.id, { skipPrd: true });
  state.selectedCardId = cardId;
  const v = $('#viewport');
  const c = rec.card;
  const o = cardOuterSize(c);
  const w = o.w, h = o.h;
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
  openPrdForCard(cardId);
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
    // 编辑 PRD 时：Ctrl+S 保存；其余画布快捷键一律让位给文本输入
    if (state.prdEditing && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault(); savePrd(); return;
    }
    if (e.target.matches('input, textarea, select') || e.target.isContentEditable) return;
    if (state.prdEditing) return;
    if (e.key === 'Escape' && state.flowFullscreen) { setFlowFullscreen(false); return; }
    if (e.key === '0' && !e.ctrlKey && !e.metaKey) resetOrigin();
    if (e.key === '1' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); fitAll(); }
    if (e.key === 'Escape') { state.selectedCardId = null; markSelected(); }
  });
}

/* ═══════════════ 3. 原型卡片渲染 ═══════════════ */

/* 设备外壳：系统组件，AI 只写 shell 字段；卡片 w/h 始终指屏幕内容区尺寸 */
const SHELLS = {
  phone:   { pad: 12, chromeH: 0 },
  tablet:  { pad: 14, chromeH: 0 },
  browser: { pad: 0,  chromeH: 34 },
};

/* 标题栏固定高度（与 CSS .card-head 保持一致），w/h 始终指屏幕内容区，
   外壳与标题栏都画在屏幕区之外，不挤占原型空间（+2 为卡片自身 1px 边框的补偿） */
const CARD_HEAD_H = 28;

function cardShell(c) {
  return c.shell && SHELLS[c.shell] ? c.shell : '';
}

/* 卡片外围尺寸（标题栏 + 外壳装饰 + 边框补偿），供画布包围盒/聚焦/连线计算 */
function cardOuterSize(c) {
  const shell = cardShell(c);
  const conf = shell ? SHELLS[shell] : null;
  const pad = conf ? conf.pad * 2 : 0;
  return {
    w: (c.w || 360) + pad + 2,
    h: (c.h || 560) + CARD_HEAD_H + pad + (conf ? conf.chromeH : 0) + 2,
  };
}

/* iframe 注入：隐藏原生滚动条（不占布局空间），并提供 3px 浮动滚动指示条——
   滚动时浮现于内容之上，停止约 0.8 秒后自动淡出（移动端 overlay 风格） */
const FRAME_EXTRAS =
  '<style>::-webkit-scrollbar{width:0;height:0}html{scrollbar-width:none}</style>' +
  '<script>(function(){var bar=null,timer=0;function refresh(){' +
  'var de=document.documentElement,vh=de.clientHeight,sh=de.scrollHeight;' +
  'var st=de.scrollTop||document.body.scrollTop||0;' +
  'if(sh<=vh+1){if(bar)bar.style.opacity="0";return;}' +
  'if(!bar){bar=document.createElement("div");' +
  'bar.style.cssText="position:fixed;top:0;right:2px;width:3px;border-radius:2px;' +
  'background:rgba(0,0,0,.22);z-index:2147483647;pointer-events:none;opacity:0;' +
  'transition:opacity .3s";document.body.appendChild(bar);}' +
  'var h=Math.max(28,vh*vh/sh),y=st/(sh-vh)*(vh-h);' +
  'bar.style.top=y+"px";bar.style.height=h+"px";bar.style.opacity="1";' +
  'clearTimeout(timer);timer=setTimeout(function(){bar.style.opacity="0"},800);}' +
  'addEventListener("scroll",refresh);addEventListener("resize",refresh);' +
  'addEventListener("load",function(){setTimeout(refresh,60)});})();<\/script>';

function renderPage() {
  buildCardIndex();
  const world = $('#world');
  // 只清卡片，保留连线层 SVG（它在 world 内、随画布变换）
  world.querySelectorAll('.proto-card').forEach(n => n.remove());
  const page = D.pages.find(p => p.id === state.currentPageId);
  $('#canvasPageName').textContent = page ? `画布详情 · ${page.name || page.id}` : '画布详情';
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

    const shell = cardShell(c);
    const outer = cardOuterSize(c);
    node.style.width = outer.w + 'px';
    node.style.height = outer.h + 'px';

    const head = el('div', 'card-head');
    head.appendChild(el('span', 'card-name', c.name || c.id));
    if (c.type) head.appendChild(el('span', 'card-type', c.type));
    if (c.status) head.appendChild(el('span', 'card-status', c.status));
    node.appendChild(head);

    let body;
    if (shell) {
      const shellEl = el('div', 'shell shell-' + shell);
      if (shell === 'browser') {
        const dots = document.createElement('span');
        dots.className = 'dots';
        dots.innerHTML = '<i class="d-r"></i><i class="d-y"></i><i class="d-g"></i>';
        shellEl.appendChild(dots);
      }
      body = el('div', 'card-body screen');
      shellEl.appendChild(body);
      node.appendChild(shellEl);
    } else {
      body = el('div', 'card-body');
      node.appendChild(body);
    }

    // 真实 HTML 用 iframe 沙箱承载：样式隔离 + 交互真实可用（禁止图片模拟页面）
    const frame = document.createElement('iframe');
    frame.setAttribute('sandbox', 'allow-scripts allow-forms allow-modals allow-popups');
    body.appendChild(frame);

    // 卡片交互：单击选中即联动 PRD；头部拖拽移动
    node.addEventListener('mousedown', (e) => {
      e.stopPropagation();
      state.selectedCardId = c.id;
      markSelected();
      openPrdForCard(c.id);   // 选中即联动：PRD 跳到对应章节（无关联章节时为空操作）
      if (e.target.closest('.card-head')) {
        state.dragging = {
          cardId: c.id, sx: e.clientX, sy: e.clientY,
          cx: c.x, cy: c.y,
        };
      }
    });

    // srcdoc 放最后写入：DOM 结构就绪后再加载，避免壳样式抖动
    frame.srcdoc =
      '<!DOCTYPE html><html><head><meta charset="UTF-8">' +
      '<style>html,body{margin:0;padding:0;overflow-x:hidden}</style>' +
      FRAME_EXTRAS +
      '</head><body>' + (c.html || '') + '</body></html>';

    world.appendChild(node);
  }
  drawLinks();
}

/* ═══════════════ 4. 关系连线（SVG） ═══════════════ */

function cardAnchor(cardId) {
  const rec = cardIndex.get(cardId);
  if (!rec) return null;
  const c = rec.card;
  const o = cardOuterSize(c);
  return {
    cx: c.x + o.w / 2,
    cy: c.y + o.h / 2,
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

  if (state.prdEditing) { renderPrdEditor(body, toc, sections); return; }

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
  if (state.prdEditing) return;   // 编辑 PRD 时不抢滚动位置
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

/* ═══════════════ 5b. PRD 直接编辑 + 持久化 ═══════════════
 * 用户在右栏直接改 PRD，保存时 POST 给 DeziBee 预览服务器，写回
 * demo/index.html 的 WORKBENCH_DATA.prd（与 AI 编辑同一份真源）。
 * 静态部署 / file:// 打开时没有该端点：编辑入口隐藏，页面保持纯只读。
 * ═══════════════════════════════════════════════════════════════ */

const PRD_ENDPOINT = '/__dezibee__/prd';
const EXPORT_ENDPOINT = '/__dezibee__/export';

/* 静态导出产物（单文件 HTML）里没有本地服务器：编辑/导出入口一律隐藏 */
function isExportBuild() { return window.__DEZIBEE_EXPORT__ === true; }

/* 只有从 DeziBee 本机预览服务器打开时，才提供「编辑 PRD / 导出单文件」这些需要服务端的能力 */
function isLocalServer() {
  return !isExportBuild() && location.protocol.startsWith('http') &&
    (location.hostname === '127.0.0.1' || location.hostname === 'localhost');
}

/* 布局偏好（PRD 宽度 / 编辑区高度）本地记忆；隐私模式等取不到 localStorage 时静默降级 */
function saveLayout(key, val) {
  try {
    if (val == null) localStorage.removeItem('dezibee.' + key);
    else localStorage.setItem('dezibee.' + key, String(val));
  } catch (e) { /* 忽略 */ }
}

function loadLayout(key) {
  try {
    const v = localStorage.getItem('dezibee.' + key);
    return v == null ? null : Number(v);
  } catch (e) { return null; }
}

function canEditPrd() {
  return isLocalServer();
}

/* 从 URL 推断需求 ID：http://127.0.0.1:port/REQ-xxx/demo/index.html → REQ-xxx */
function guessReqId() {
  const parts = location.pathname.split('/').filter(Boolean);
  if (location.protocol.startsWith('http')) return parts[0] || null;
  const i = parts.lastIndexOf('demo');
  return i > 0 ? parts[i - 1] : null;
}

function setPrdStatus(msg, kind) {
  const n = $('#prdStatus');
  if (!msg) { n.textContent = ''; n.className = 'prd-status hidden'; return; }
  n.textContent = msg;
  n.className = 'prd-status ' + (kind || 'info');
}

function setPrdButtons(editing) {
  $('#prdEditBtn').classList.toggle('hidden', editing);
  $('#prdSaveBtn').classList.toggle('hidden', !editing);
  $('#prdCancelBtn').classList.toggle('hidden', !editing);
  $('#prdTocBtn').classList.toggle('hidden', editing);
  $('#prdToc').classList.toggle('hidden', editing);
}

function enterPrdEdit() {
  state.prdEditing = true;
  setPrdButtons(true);
  renderPrd();
  setPrdStatus('编辑中：改完点「✔ 保存」（Ctrl+S）写回 demo/index.html。', 'info');
}

function cancelPrdEdit() {
  state.prdEditing = false;
  setPrdButtons(false);
  renderPrd();
  setPrdStatus('', '');
}

/* 编辑态渲染：标题输入 + 层级选择 + 富文本正文（WYSIWYG） */
function renderPrdEditor(body, toc, sections) {
  toc.innerHTML = '<div class="toc-item lvl2 active">编辑中…</div>';
  if (!sections.length) {
    body.appendChild(el('p', '', '当前没有 PRD 章节 — 可在 DeziBee 对话里让 AI 先创建。'));
    return;
  }
  sections.forEach((sec, idx) => {
    const wrap = el('section', 'prd-edit-sec');
    wrap.dataset.idx = String(idx);
    wrap.dataset.secId = sec.id || '';

    const head = el('div', 'prd-edit-head');
    const title = document.createElement('input');
    title.className = 'prd-edit-title';
    title.value = sec.title || sec.id || '';
    title.placeholder = '章节标题';
    head.appendChild(title);
    const level = document.createElement('select');
    level.className = 'prd-edit-level';
    for (const l of [2, 3, 4]) {
      const o = document.createElement('option');
      o.value = String(l); o.textContent = 'H' + l;
      if ((sec.level || 2) === l) o.selected = true;
      level.appendChild(o);
    }
    head.appendChild(level);
    wrap.appendChild(head);

    const content = document.createElement('div');
    content.className = 'prd-edit-content';
    content.contentEditable = 'true';
    content.innerHTML = sec.html || '';
    // 恢复上次拖动的高度（默认 46vh 由 CSS 给）
    const savedH = loadLayout('prdEditH');
    if (savedH && savedH >= 64) content.style.height = savedH + 'px';
    wrap.appendChild(content);

    // 高度拖拽手柄：向下拖加高编辑区
    const grip = el('div', 'prd-edit-grip');
    grip.title = '上下拖动调整编辑区高度';
    grip.addEventListener('pointerdown', (e) => startEditResize(e, content));
    wrap.appendChild(grip);

    body.appendChild(wrap);
  });
}

/* 收集编辑结果：id 保持原值 → 卡片 prdId 关联不断 */
function collectPrdEdits() {
  const orig = D.prd?.sections || [];
  const out = [];
  document.querySelectorAll('#prdBody .prd-edit-sec').forEach((wrap) => {
    const sec = orig[Number(wrap.dataset.idx)] || {};
    out.push({
      id: sec.id,
      title: wrap.querySelector('.prd-edit-title').value.trim() || sec.id,
      level: Number(wrap.querySelector('.prd-edit-level').value) || 2,
      html: wrap.querySelector('.prd-edit-content').innerHTML,
    });
  });
  return out;
}

async function savePrd() {
  if (!state.prdEditing) return;
  const reqId = guessReqId();
  if (!reqId) { setPrdStatus('无法确定需求 ID：请通过 DeziBee「预览」打开本页再编辑。', 'err'); return; }
  const sections = collectPrdEdits();
  setPrdStatus('保存中…', 'info');
  try {
    const resp = await fetch(PRD_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ req_id: reqId, prd: Object.assign({}, D.prd || {}, { sections }) }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.ok) throw new Error(data.error || ('HTTP ' + resp.status));
    D.prd = Object.assign({}, D.prd || {}, { sections });
    state.prdEditing = false;
    setPrdButtons(false);
    renderPrd();
    setPrdStatus('已保存到 demo/index.html ✔', 'ok');
  } catch (err) {
    setPrdStatus('保存失败：' + err.message, 'err');
  }
}

/* 统一的拖拽会话：用 pointer capture 保证在窗外松开也能收到 pointerup，
   不会出现「拖到一半卡住、光标不复位」的情况。 */
function startDrag(e, onMove, onEnd, bodyClass) {
  const el = e.currentTarget;
  const startX = e.clientX, startY = e.clientY;
  try { el.setPointerCapture(e.pointerId); } catch (err) { /* 忽略 */ }
  document.body.classList.add(bodyClass);

  const move = (ev) => onMove(ev, { startX, startY });
  const end = () => {
    el.removeEventListener('pointermove', move);
    el.removeEventListener('pointerup', end);
    el.removeEventListener('pointercancel', end);
    window.removeEventListener('pointerup', end);
    window.removeEventListener('mouseup', end);
    window.removeEventListener('blur', end);
    document.body.classList.remove(bodyClass);
    el.classList.remove('dragging');
    if (onEnd) onEnd();
  };
  el.addEventListener('pointermove', move);
  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', end);
  // 兜底：即使指针捕获不可用，松手/失焦也一定结束拖拽
  window.addEventListener('pointerup', end);
  window.addEventListener('mouseup', end);
  window.addEventListener('blur', end);
  el.classList.add('dragging');
}

/* 编辑区高度：拖动向下加高，上限 90vh，高度被记住 */
function startEditResize(e, content) {
  e.preventDefault();
  const startH = content.getBoundingClientRect().height;
  startDrag(e,
    (ev, { startY }) => {
      const h = Math.max(64, Math.min(window.innerHeight * 0.9, startH + (ev.clientY - startY)));
      content.style.height = h + 'px';
    },
    () => saveLayout('prdEditH', Math.round(parseFloat(content.style.height))),
    'resizing-row');
}

/* PRD 窗格宽度：拖拽左右调整，双击复位；宽度被记住 */
function bindPrdResizer() {
  const rz = $('#prdResizer');
  const prd = $('#prd');
  if (!rz || !prd) return;
  const clamp = (w) => Math.max(260, Math.min(window.innerWidth - 460, w));

  const saved = loadLayout('prdW');
  if (saved && saved >= 260) prd.style.width = clamp(saved) + 'px';

  rz.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    startDrag(e,
      (ev) => { prd.style.width = clamp(window.innerWidth - ev.clientX) + 'px'; },
      () => saveLayout('prdW', Math.round(parseFloat(prd.style.width))),
      'resizing-col');
  });

  rz.addEventListener('dblclick', () => { prd.style.width = ''; saveLayout('prdW', null); });

  window.addEventListener('resize', () => {
    if (prd.style.width) prd.style.width = clamp(parseFloat(prd.style.width)) + 'px';
  });
}

/* ═══════════════ 5c. 导出单文件 HTML ═══════════════
 * 由本机预览服务器打包（内联 css/js/图片），以附件下载；产物可直接上传 OSS。
 * ═════════════════════════════════════════════════════════════ */
async function exportSingleFile() {
  const reqId = guessReqId();
  if (!reqId) {
    setPrdStatus('无法确定需求 ID：请通过 DeziBee「预览」再导出。', 'err');
    return;
  }
  // 有未保存的手改先落盘，避免导出旧内容
  if (state.prdEditing) {
    await savePrd();
    if (state.prdEditing) { setPrdStatus('保存失败，已取消导出。', 'err'); return; }
  }
  setPrdStatus('正在打包单文件 HTML…', 'info');
  try {
    const resp = await fetch(EXPORT_ENDPOINT + '?req_id=' + encodeURIComponent(reqId));
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.error || ('HTTP ' + resp.status));
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = reqId + '.html';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setPrdStatus('已下载 ' + reqId + '.html（' + (blob.size / 1024).toFixed(0) + ' KB，自包含，可直接上传 OSS）', 'ok');
  } catch (err) {
    setPrdStatus('导出失败：' + err.message, 'err');
  }
}

/* ═══════════════ 6. 启动 ═══════════════ */

function init() {
  ensurePanner();
  // 三栏固定称呼：画布导航 | 画布详情 | 产品需求说明书（与 GUIDE 术语一致，覆盖旧模板文案）
  $('#navProjectTitle').textContent = '画布导航';
  if (D.project?.title) document.title = D.project.title;
  const prdTitle = document.querySelector('.prd-title');
  if (prdTitle) prdTitle.textContent = '产品需求说明书';
  buildNavTree();
  renderPrd();
  renderPage();
  bindViewport();
  bindPrdResizer();
  syncPrdToPage(state.currentPageId);   // 首屏 PRD 跟随初始页面
  // 首屏自动适应内容
  requestAnimationFrame(() => fitAll());

  $('#zoomInBtn').addEventListener('click', () => { const c = viewportCenter(); zoomAt(1.2, c.x, c.y); });
  $('#zoomOutBtn').addEventListener('click', () => { const c = viewportCenter(); zoomAt(1 / 1.2, c.x, c.y); });
  $('#fitAllBtn').addEventListener('click', fitAll);
  $('#originBtn').addEventListener('click', resetOrigin);
  $('#focusBtn').addEventListener('click', () => {
    if (state.selectedCardId) focusCard(state.selectedCardId);
  });
  $('#flowFullscreenBtn').addEventListener('click', () => setFlowFullscreen(!state.flowFullscreen));
  $('#prdTocBtn').addEventListener('click', () => {
    const toc = $('#prdToc');
    const btn = $('#prdTocBtn');
    const open = toc.classList.toggle('hidden') === false;
    btn.classList.toggle('is-open', open);
    btn.classList.toggle('is-closed', !open);
    btn.setAttribute('aria-expanded', String(open));
    btn.setAttribute('aria-label', open ? '收起目录' : '展开目录');
    btn.title = open ? '收起目录' : '展开目录';
  });

  // PRD 直接编辑 + 单文件导出（仅本机预览服务器下提供；静态产物隐藏入口）
  if (isLocalServer()) {
    $('#prdEditBtn').addEventListener('click', enterPrdEdit);
    $('#prdSaveBtn').addEventListener('click', savePrd);
    $('#prdCancelBtn').addEventListener('click', cancelPrdEdit);
    $('#exportBtn').classList.remove('hidden');
    $('#exportBtn').addEventListener('click', exportSingleFile);
  } else {
    $('#prdEditBtn').classList.add('hidden');
    $('#exportBtn').classList.add('hidden');
  }
}

document.addEventListener('DOMContentLoaded', init);
