/* ═══════════════ DOM 构建 ═══════════════ */
const messages = document.getElementById('messages');
const olderBtn = document.getElementById('olderBtn');
const statusbar = document.getElementById('statusbar');
const statusText = document.getElementById('statusText');

function el(tag, cls, html) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (html !== undefined) node.innerHTML = html;
  return node;
}

function avatarFor(role, kind) {
  if (role === 'user') return '👤';
  if (role === 'ai') return '🤖';
  if (role === 'error') return '⚠️';
  if (role === 'tool') return '🔧';
  if (role === 'thinking') return '💭';
  if (kind === 'approval') return '✅';
  if (kind === 'lesson') return '📝';
  if (kind === 'deliverables') return '📦';
  return '💻';
}

function bubbleCls(role, kind) {
  if (role === 'user') return 'user';
  if (role === 'error') return 'error';
  if (role === 'ai') return 'ai';
  if (role === 'tool') return 'tool';
  if (role === 'thinking') return 'thinking';
  if (kind === 'approval') return 'system approval';
  if (kind === 'lesson') return 'system lesson';
  if (kind === 'deliverables') return 'system deliverables';
  return 'system';
}

function headLabel(role, kind, time) {
  const tag = {
    user: '用户', ai: 'AI', error: '错误', tool: '工具', thinking: '思考',
    system: { approval: '审批', lesson: '经验', info: '系统', deliverables: '交付物' }[kind] || '系统',
  }[role];
  return (tag || kind || '') + (time ? ' · ' + time : '');
}

function fileUrl(path) {
  return 'file:///' + encodeURI(String(path || '').replace(/\\/g, '/'));
}

/* 附件列（图片直接展示，文档以 chip 展示） */
function makeAttachments(d) {
  const atts = Array.isArray(d.attachments) ? d.attachments : [];
  if (!atts.length) return null;
  const col = el('div', 'att-col');
  for (const a of atts) {
    const path = String(a.path || '');
    if (!path) continue;
    if (a.kind === 'image') {
      const img = el('img', 'att-img');
      img.src = fileUrl(path);
      img.alt = String(a.name || '');
      img.title = String(a.name || '');
      img.addEventListener('error', () => img.remove());
      col.appendChild(img);
    } else {
      col.appendChild(el('div', 'att-chip', '📎 ' + escapeHtml(String(a.name || path))));
    }
  }
  return col.children.length ? col : null;
}

/* 普通气泡行（user / ai / error / system / 独立 tool callback） */
function makeMsg(d) {
  const role = d.role;
  const row = el('div', 'msg' + (role === 'user' ? ' user' : ''));
  const bubble = el('div', 'bubble ' + bubbleCls(role, d.kind));
  bubble.appendChild(el('div', 'bubble-head', escapeHtml(headLabel(role, d.kind, d.time))));
  const text = String(d.content || '').replace(/^\[📎 .+\]\s*$/gm, '').trim();
  // .md 必须始终存在：流式渲染先建空 .md 再逐帧 setMd
  const md = el('div', 'md');
  if (text) setMd(md, text);
  bubble.appendChild(md);
  const att = makeAttachments(d);
  if (att) bubble.appendChild(att);
  if (role === 'user') {
    row.appendChild(bubble);
    row.appendChild(el('div', 'avatar user', '👤'));
  } else {
    row.appendChild(el('div', 'avatar ' + role, avatarFor(role, d.kind)));
    row.appendChild(bubble);
  }
  return row;
}

/* 交付物快捷链接气泡（点击打开交付物目录） */
function makeDeliverables(d) {
  const row = el('div', 'msg');
  row.appendChild(el('div', 'avatar system', '📦'));
  const bubble = el('div', 'bubble system deliverables');
  bubble.appendChild(el('div', 'bubble-head', escapeHtml(headLabel('system', 'deliverables', d.time))));
  const btn = el('div', 'deliverables-btn', '📂 打开交付物目录');
  btn.addEventListener('click', () => {
    try {
      const pid = (d.project_id || '').toString();
      if (bridge && typeof bridge.open_deliverables === 'function') { bridge.open_deliverables(pid, 0); }
    } catch (e) {}
  });
  bubble.appendChild(btn);
  bubble.appendChild(el('div', 'deliverables-hint', 'AI 任务已完成，点击查看交付成果'));
  row.appendChild(bubble);
  return row;
}

/* 思考气泡行：左侧 💭 头像 + 蓝色思考气泡（点击头部折叠/展开） */
function makeThinking(d) {
  const row = el('div', 'msg');
  row.appendChild(el('div', 'avatar thinking', '💭'));
  const bubble = el('div', 'bubble thinking');
  const head = el('div', 'thinking-head', '💭 思考过程');
  const bodyWrap = el('div', 'thinking-body');
  const md = el('div', 'md');
  setMd(md, d.content);
  bodyWrap.appendChild(md);
  bubble.appendChild(head);
  bubble.appendChild(bodyWrap);
  head.addEventListener('click', () => {
    const hidden = bodyWrap.classList.toggle('collapsed');
    head.textContent = hidden ? '💭 思考过程（已折叠）' : '💭 思考过程';
  });
  row.appendChild(bubble);
  return row;
}

/* ═══════════════ 工具步骤行（call ↔ callback 配对） ═══════════════ */
const toolRows = new Map();
const pendingRows = new Set();
let toolSeq = 0;

function setChip(chipEl, text, status) {
  chipEl.textContent = text;
  chipEl.className = 'chip ' + status;
}

function makeToolCall(d) {
  const t = d.tool || {};
  toolSeq++;
  const row = el('div', 'msg');
  row.appendChild(el('div', 'avatar tool', '🔧'));
  const bubble = el('div', 'bubble tool');
  const head = el('div', 'tool-head');
  const name = el('span', 'name', escapeHtml(t.name || 'tool'));
  const chip = el('span', 'chip running', '调用中');
  head.appendChild(name); head.appendChild(chip);
  bubble.appendChild(head);
  const bodyWrap = el('div', 'tool-body');
  const md = el('div', 'md');
  setMd(md, d.content);
  bodyWrap.appendChild(md);
  bubble.appendChild(bodyWrap);
  row.appendChild(bubble);
  const rec = { el: row, head, chip, bodyWrap, md, status: 'running', name: t.name };
  const id = t.id || '__noid__' + toolSeq;
  toolRows.set(id, rec);
  return row;
}

function finishToolCall(d) {
  const t = d.tool || {};
  const id = t.id || '__noid__' + toolSeq;
  let rec = toolRows.get(id);
  if (!rec) {
    for (const [k, v] of toolRows) {
      if (v.name === t.name && v.status === 'running') { rec = v; toolRows.delete(k); break; }
    }
  } else {
    toolRows.delete(id);
  }
  if (!rec) return null;
  pendingRows.delete(id);
  const ok = String(t.status || 'success') !== 'error';
  const empty = !(d.content && d.content.trim());
  rec.status = ok ? (empty ? 'empty' : 'ok') : 'failed';
  setChip(rec.chip, { ok: '成功', empty: '返回为空', failed: '失败' }[rec.status], rec.status);
  // 追加 callback 结果到同一气泡（保留 call 内容，用分隔线隔开，不替换）
  const md = rec.md;
  md.innerHTML += '<div class="cb-sep"></div>' + mdToHtml(d.content);
  rec.md = md;
  if (rec.status === 'failed') rec.el.querySelector('.bubble.tool').classList.add('failed');
  setupClamp(md);
  return rec;
}

function renderTool(d) {
  const isCall = (d.tool && d.tool.phase === 'call');
  return isCall ? makeToolCall(d) : (finishToolCall(d) ? null : makeMsg(d));
}

function onApprovalPending() {
  for (const [id, rec] of toolRows) {
    if (rec.status === 'running') {
      setChip(rec.chip, '待确认', 'pending');
      pendingRows.add(id);
    }
  }
}

function onResumeApproval(approved) {
  for (const id of pendingRows) {
    const rec = toolRows.get(id);
    if (!rec) continue;
    if (approved) { setChip(rec.chip, '调用中', 'running'); rec.status = 'running'; }
    else { setChip(rec.chip, '未完成', 'skipped'); rec.status = 'skipped'; }
  }
  pendingRows.clear();
}

/* ═══════════════ 正文截断 + 展开 ═══════════════ */
/* 截断行数可用 URL 参数覆盖：chat.html?clamp=full 不截断；clamp=N 截 N 行；默认 1 行 */
const CLAMP_LINES = (() => {
  const m = (location.search || '').match(/[?&]clamp=(\d+|full)/);
  if (!m) return 1;
  if (m[1] === 'full') return 1e9;
  const n = parseInt(m[1], 10);
  return n > 0 ? n : 1;
})();
