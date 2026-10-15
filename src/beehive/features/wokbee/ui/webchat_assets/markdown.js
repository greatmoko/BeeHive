'use strict';

document.addEventListener('contextmenu', (event) => event.preventDefault());

/* ═══════════════ 工具：Markdown 渲染（无外部依赖，已先转义 HTML） ═══════════════ */
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function inlineMd(s) {
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
  s = s.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return s;
}

function blockMd(escaped) {
  const lines = escaped.split(/\r?\n/);
  let html = '';
  let listType = null;
  const closeList = () => {
    if (listType) { html += listType === 'ul' ? '</ul>' : '</ol>'; listType = null; }
  };
  for (const raw of lines) {
    const ln = raw.trim();
    if (!ln) { closeList(); continue; }
    const h = ln.match(/^(#{1,6})\s+(.*)$/);
    if (h) { closeList(); html += '<h' + h[1].length + '>' + inlineMd(h[2]) + '</h' + h[1].length + '>'; continue; }
    const ul = ln.match(/^[-*+]\s+(.*)$/);
    if (ul) {
      if (listType !== 'ul') { closeList(); html += '<ul>'; listType = 'ul'; }
      html += '<li>' + inlineMd(ul[1]) + '</li>'; continue;
    }
    const ol = ln.match(/^\d+[.)]\s+(.*)$/);
    if (ol) {
      if (listType !== 'ol') { closeList(); html += '<ol>'; listType = 'ol'; }
      html += '<li>' + inlineMd(ol[1]) + '</li>'; continue;
    }
    closeList();
    html += '<p>' + inlineMd(ln) + '</p>';
  }
  closeList();
  return html;
}

function mdToHtml(text) {
  if (!text) return '';
  const parts = String(text).split(/```/);
  let html = '';
  let inCode = false;
  for (const part of parts) {
    if (inCode) {
      const body = part.replace(/^\s*\n/, '').replace(/\n\s*$/, '');
      html += '<pre><code>' + escapeHtml(body) + '</code></pre>';
    } else {
      html += blockMd(escapeHtml(part));
    }
    inCode = !inCode;
  }
  return html;
}

function setMd(el, text) { el.innerHTML = mdToHtml(text || ''); }
