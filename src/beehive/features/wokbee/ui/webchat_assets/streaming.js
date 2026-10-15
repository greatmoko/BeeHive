function removeClampToggle(md) {
  const toggle = md.nextElementSibling;
  if (toggle && toggle.classList && toggle.classList.contains('clamp-toggle')) {
    toggle.classList.add('hidden');
  }
}

function mdLineHeight(md) {
  const cs = window.getComputedStyle(md);
  const lh = parseFloat(cs.lineHeight) || 1.65;
  const fs = parseFloat(cs.fontSize) || 14;
  return lh > 5 ? lh : lh * fs; // line-height 可能是数值或具体 px
}

function setupClamp(md) {
  if (!md || !md.parentNode) return;
  // 用户已手动展开时，复核不得再次收起
  if (md.getAttribute('data-clamp-state') === 'expanded') return;
  const hasPre = !!md.querySelector('pre');
  md.classList.add('clamped');
  if (hasPre) {
    md.classList.add('pre');
    const lineH = mdLineHeight(md);
    md.style.maxHeight = Math.round(lineH * CLAMP_LINES + 6) + 'px';
  } else {
    md.classList.remove('pre');
    md.style.maxHeight = '';
  }
  const truncated = md.scrollHeight > md.clientHeight + 2;
  if (!truncated) {
    md.classList.remove('clamped', 'pre');
    md.style.maxHeight = '';
    removeClampToggle(md);
    return;
  }
  let toggle = md.nextElementSibling;
  if (!toggle || !toggle.classList || !toggle.classList.contains('clamp-toggle')) {
    toggle = el('button', 'clamp-toggle', '展开全部');
    md.parentNode.appendChild(toggle);
    toggle.addEventListener('click', () => {
      const clamped = md.classList.toggle('clamped');
      md.classList.toggle('pre', hasPre);
      if (clamped) {
        if (hasPre) md.style.maxHeight = Math.round(mdLineHeight(md) * CLAMP_LINES + 6) + 'px';
        else md.style.maxHeight = '';
      } else {
        md.style.maxHeight = '';
      }
      md.setAttribute('data-clamp-state', clamped ? 'clamped' : 'expanded');
      toggle.textContent = clamped ? '展开全部' : '收起';
    });
  } else {
    toggle.classList.remove('hidden');
  }
}

function attachClamp(row) {
  if (row && row.querySelector) {
    const md = row.querySelector('.md');
    if (md) {
      // 同步测量 + 延时复核：字体/布局稳定后再判一次，避免离屏/未显示时误判
      setupClamp(md);
      window.setTimeout(() => setupClamp(md), 60);
      window.setTimeout(() => setupClamp(md), 300);
    }
  }
}

// 窗口尺寸变化 / 字体加载完成后重新测量所有正文，避免窄屏阶段误收起
window.addEventListener('resize', () => {
  document.querySelectorAll('.md.clamped').forEach((md) => setupClamp(md));
});
if (document.fonts && document.fonts.ready) {
  document.fonts.ready.then(() => {
    document.querySelectorAll('.md.clamped').forEach((md) => setupClamp(md));
  });
}

/* ═══════════════ 流式渲染 ═══════════════ */
const stream = { reasoning: null, text: null };
const streamText = { reasoning: '', text: '' };
const CURSOR = '<span class="cursor"></span>';

function finalizeStream(target, ended, fullEvent) {
  const row = stream[target];
  if (!row) return false;
  stream[target] = null;
  const full = fullEvent ? (fullEvent.content || '') : (streamText[target] || '');
  streamText[target] = '';
  const md = row.querySelector('.md');
  if (md) { setMd(md, full); setupClamp(md); }
  if (target === 'reasoning') {
    const head = row.querySelector('.thinking-head');
    if (head) head.textContent = ended ? '💭 思考（已结束）' : '💭 思考过程';
  } else {
    const head = row.querySelector('.bubble-head');
    if (head) {
      head.textContent = ended
        ? 'AI（已结束） · 生成中断'
        : (fullEvent && fullEvent.time ? 'AI · ' + fullEvent.time : 'AI');
    }
  }
  return true;
}

function onAppendStream(target, delta) {
  const key = target === 'reasoning' ? 'reasoning' : 'text';
  if (!stream[key]) {
    if (key === 'reasoning') {
      const row = makeThinking({ role: 'ai', kind: 'agent', time: '', content: '' });
      const head = row.querySelector('.thinking-head');
      if (head) head.textContent = '💭 思考 · 生成中…';
      stream[key] = row;
    } else {
      const row = makeMsg({ role: 'ai', kind: 'agent', time: '', content: '' });
      const head = row.querySelector('.bubble-head');
      if (head) head.textContent = 'AI · 生成中…';
      stream[key] = row;
    }
    messages.appendChild(stream[key]);
  }
  streamText[key] += delta;
  const md = stream[key].querySelector('.md');
  if (md) setMd(md, streamText[key] + CURSOR);
  scrollBottom();
}

/* ═══════════════ 滚动 ═══════════════ */
let stick = true;
let loadingOlder = false;

function scrollBottom() {
  if (!stick) return;
  messages.scrollTop = messages.scrollHeight;
}

messages.addEventListener('scroll', () => {
  const nearBottom = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 48;
  if (nearBottom) { stick = true; return; }
  stick = false;
  if (messages.scrollTop < 24 && olderRemaining > 0 && !loadingOlder) {
    loadingOlder = true;
    if (bridge) bridge.load_older();
  }
});

olderBtn.addEventListener('click', () => {
  if (!loadingOlder && olderRemaining > 0) {
    loadingOlder = true;
    if (bridge) bridge.load_older();
  }
});
