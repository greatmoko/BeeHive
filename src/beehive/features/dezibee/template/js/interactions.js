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
