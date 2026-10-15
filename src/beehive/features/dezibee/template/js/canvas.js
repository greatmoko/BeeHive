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
