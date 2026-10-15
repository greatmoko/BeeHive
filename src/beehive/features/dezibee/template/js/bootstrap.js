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
