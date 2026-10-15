function updateOlderButton() {
  if (olderRemaining > 0) {
    olderBtn.textContent = '加载更早记录（剩 ' + olderRemaining + ' 条） ↑';
    olderBtn.classList.remove('hidden');
  } else {
    olderBtn.classList.add('hidden');
  }
}

/* ═══════════════ 事件渲染入口 ═══════════════ */
function renderEvent(d) {
  if (d.session_end) return null; // 内部轮次边界用于日志切分，不重复显示为结束气泡
  const kind = d.kind || '';
  const role = d.role || 'system';
  if (kind === 'tool') return renderTool(d);
  if (kind === 'agent' && d.phase === 'reasoning') return makeThinking(d);
  if (kind === 'deliverables') return makeDeliverables(d);
  return makeMsg(d);
}

function resetAll() {
  messages.innerHTML = '';
  messages.appendChild(olderBtn);
  toolRows.clear(); pendingRows.clear(); toolSeq = 0;
  stream.reasoning = null; stream.text = null;
  streamText.reasoning = ''; streamText.text = '';
  stick = true; loadingOlder = false;
}

function onRenderEvents(eventsJson, remaining) {
  resetAll();
  olderRemaining = remaining || 0;
  const items = JSON.parse(eventsJson || '[]');
  for (const d of items) {
    const node = renderEvent(d);
    if (node) { messages.appendChild(node); attachClamp(node); }
  }
  updateOlderButton();
  scrollBottom();
}

function onPrependEvents(eventsJson, remaining) {
  loadingOlder = false;
  olderRemaining = remaining || 0;
  const items = JSON.parse(eventsJson || '[]');
  const frag = document.createDocumentFragment();
  for (const d of items) {
    const node = renderEvent(d);
    if (node) { frag.appendChild(node); attachClamp(node); }
  }
  const anchor = messages.children[0] === olderBtn ? olderBtn : null;
  messages.insertBefore(frag, anchor ? anchor.nextSibling : messages.firstChild);
  updateOlderButton();
}

function onAppendEvent(eventJson) {
  const d = JSON.parse(eventJson || '{}');
  if (!d.kind) return;
  const phase = d.phase || '';
  let reused = false;
  if (d.kind === 'agent' && (phase === 'narration' || phase === 'answer')) {
    reused = finalizeStream('text', false, d);
  }
  if (d.kind === 'agent' && phase === 'reasoning') {
    reused = finalizeStream('reasoning', false, d);
  }
  if (reused) { scrollBottom(); return; }
  const node = renderEvent(d);
  if (node) { messages.appendChild(node); attachClamp(node); }
  scrollBottom();
}

function onBeginRun() {
  toolRows.clear(); pendingRows.clear(); toolSeq = 0;
  stream.reasoning = null; stream.text = null;
  streamText.reasoning = ''; streamText.text = '';
  stick = true;
}

function onEndRun() {
  finalizeStream('reasoning', true);
  finalizeStream('text', true);
  for (const [id, rec] of toolRows) {
    if (rec.status === 'running' || rec.status === 'pending') {
      setChip(rec.chip, '未完成', 'skipped');
    }
  }
  toolRows.clear(); pendingRows.clear();
}

function onSetStatus(text) {
  statusText.textContent = text || '';
  statusbar.classList.remove('hidden');
}

function onClearStatus() {
  statusbar.classList.add('hidden');
  statusText.textContent = '';
}

function onSetAgentRunning(running) {
  if (running) onBeginRun();
}

function onClearAll() {
  resetAll();
  updateOlderButton();
}

function onShowEmpty(text) {
  resetAll();
  olderRemaining = 0;
  const empty = el('div', 'empty-hint');
  empty.appendChild(el('div', 'pill', escapeHtml(text || '选择或新建一个项目开始。')));
  messages.appendChild(empty);
  updateOlderButton();
}

/* ═══════════════ QWebChannel 桥接 ═══════════════ */
let bridge = null;
let olderRemaining = 0;

function setup() {
  new QWebChannel(qt.webChannelTransport, (channel) => {
    bridge = channel.objects.backend;
    bridge.render_events.connect(onRenderEvents);
    bridge.prepend_events.connect(onPrependEvents);
    bridge.append_event.connect(onAppendEvent);
    bridge.append_stream.connect(onAppendStream);
    bridge.begin_run.connect(onBeginRun);
    bridge.end_run.connect(onEndRun);
    bridge.set_status.connect(onSetStatus);
    bridge.clear_status.connect(onClearStatus);
    bridge.set_agent_running.connect(onSetAgentRunning);
    bridge.approval_pending.connect(onApprovalPending);
    bridge.resume_approval.connect(onResumeApproval);
    bridge.show_empty.connect(onShowEmpty);
    if (bridge.clear_all) bridge.clear_all.connect(onClearAll);
    try { bridge.page_ready(); } catch (e) { /* 稍后由 Python 兜底重试 */ }
    window.setInterval(() => { try { bridge.page_ready(); } catch (e) {} }, 1000);
  });
}

setup();
