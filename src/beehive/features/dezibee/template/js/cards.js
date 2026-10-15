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
