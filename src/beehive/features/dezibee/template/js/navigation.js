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
