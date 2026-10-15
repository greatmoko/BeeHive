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
