/* 通用请求与渲染工具 */
const API = {
  async request(url, options) {
    const resp = await fetch(url, options);
    const text = await resp.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = { error: text }; }
    if (!resp.ok) {
      throw new Error((data && data.error) || `HTTP ${resp.status}`);
    }
    return data;
  },
  get(url) { return API.request(url); },
  post(url, body) {
    return API.request(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
  },
  del(url) { return API.request(url, { method: 'DELETE' }); },
};

const FEEDBACK_CLASS = {
  CORRECT: 'correct',
  MISPLACED: 'misplaced',
  PARTIAL: 'partial',
  WRONG: 'wrong',
};

const LETTER = { CORRECT: 'C', MISPLACED: 'M', PARTIAL: 'P', WRONG: 'W' };

function comboLabel(c) { return `${c[0]}·${c[1]}`; }

function fmt(x, digits) {
  if (x === null || x === undefined || Number.isNaN(x)) return '—';
  const d = digits === undefined ? 2 : digits;
  return Number(x).toFixed(d);
}

let _toastTimer = null;
function toast(message, isError) {
  const box = document.getElementById('toast');
  if (!box) return;
  box.textContent = message;
  box.className = 'toast' + (isError ? ' error' : '');
  box.hidden = false;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { box.hidden = true; }, 4200);
}

function setStatus(box, text, kind) {
  if (!box) return;
  box.textContent = text;
  box.className = 'status' + (kind ? ' ' + kind : '');
}

/** 渲染一轮历史（猜测 + 逐位反馈） */
function renderRound(container, index, guess, feedback) {
  const row = document.createElement('div');
  row.className = 'hrow';
  const idx = document.createElement('div');
  idx.className = 'hidx';
  idx.textContent = `#${index}`;
  const cells = document.createElement('div');
  cells.className = 'hcells';
  guess.forEach((combo, i) => {
    const kind = feedback[i];
    const cell = document.createElement('div');
    cell.className = 'cell ' + (FEEDBACK_CLASS[kind] || '');
    cell.innerHTML = `<span>${comboLabel(combo)}</span><span class="fb">${LETTER[kind] || '?'}</span>`;
    cell.title = `${comboLabel(combo)} → ${kind}`;
    cells.appendChild(cell);
  });
  row.appendChild(idx);
  row.appendChild(cells);
  container.appendChild(row);
}

/** 横向柱状图：柱=均值，刻度=中位数/P90/P99/最坏 */
function svgBarChart(rows, options) {
  const opts = Object.assign({ width: 980, rowHeight: 36, labelWidth: 210, rightPad: 80 }, options || {});
  const height = rows.length * opts.rowHeight + 34;
  const plotW = opts.width - opts.labelWidth - opts.rightPad;
  const maxV = Math.max(1, ...rows.map(r => Math.max(r.value, r.marks.max || 0)));
  const scale = v => (v / maxV) * plotW;
  const parts = [];
  parts.push(`<svg width="${opts.width}" height="${height}" viewBox="0 0 ${opts.width} ${height}">`);
  // 轴
  const ticks = 4;
  for (let t = 0; t <= ticks; t++) {
    const v = (maxV * t) / ticks;
    const x = opts.labelWidth + scale(v);
    parts.push(`<line class="bar-line" x1="${x.toFixed(1)}" y1="18" x2="${x.toFixed(1)}" y2="${height - 16}" opacity="0.25"/>`);
    parts.push(`<text class="bar-axis" x="${x.toFixed(1)}" y="12" text-anchor="middle">${fmt(v, 1)}</text>`);
  }
  rows.forEach((r, i) => {
    const y = 24 + i * opts.rowHeight;
    const barH = 14;
    const w = Math.max(1, scale(r.value));
    parts.push(`<text class="bar-label" x="${opts.labelWidth - 10}" y="${y + 12}" text-anchor="end">${r.label}</text>`);
    parts.push(`<rect x="${opts.labelWidth}" y="${y + 2}" width="${w.toFixed(1)}" height="${barH}" rx="4" fill="url(#bg)"/>`);
    // 刻度
    const marks = r.marks || {};
    [['median', '#8e9bb0'], ['p90', '#f5a524'], ['p99', '#ff7b72'], ['max', '#ff4d4f']].forEach(([key, color]) => {
      if (marks[key] === undefined || marks[key] === null) return;
      const x = opts.labelWidth + scale(marks[key]);
      parts.push(`<line x1="${x.toFixed(1)}" y1="${y}" x2="${x.toFixed(1)}" y2="${y + barH + 4}" stroke="${color}" stroke-width="2"/>`);
    });
    parts.push(`<text class="bar-axis" x="${opts.labelWidth + w + 8}" y="${y + barH}" >${fmt(r.value, 2)}</text>`);
  });
  parts.push(`<defs><linearGradient id="bg" x1="0" x2="1"><stop offset="0" stop-color="#3f6ff0"/><stop offset="1" stop-color="#6a5bff"/></linearGradient></defs>`);
  parts.push('</svg>');
  return parts.join('');
}

/** 竖向直方图 */
function svgHistogram(entries, options) {
  const opts = Object.assign({ width: 980, height: 240, barGap: 3 }, options || {});
  if (!entries.length) return '<div class="muted">无数据</div>';
  const maxCount = Math.max(...entries.map(e => e[1]));
  const barW = Math.max(4, (opts.width - 60) / entries.length - opts.barGap);
  const plotH = opts.height - 44;
  const parts = [`<svg width="${opts.width}" height="${opts.height}" viewBox="0 0 ${opts.width} ${opts.height}">`];
  entries.forEach((e, i) => {
    const h = maxCount ? (e[1] / maxCount) * plotH : 0;
    const x = 44 + i * (barW + opts.barGap);
    const y = 18 + plotH - h;
    parts.push(`<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="3" fill="#4d9fff" opacity="0.85"><title>${e[0]} 轮：${e[1]} 局</title></rect>`);
    if (entries.length <= 24 || i % 2 === 0) {
      parts.push(`<text class="bar-axis" x="${(x + barW / 2).toFixed(1)}" y="${opts.height - 14}" text-anchor="middle">${e[0]}</text>`);
    }
    if (maxCount && e[1] > 0) {
      parts.push(`<text class="bar-axis" x="${(x + barW / 2).toFixed(1)}" y="${(y - 4).toFixed(1)}" text-anchor="middle">${e[1]}</text>`);
    }
  });
  parts.push(`<text class="bar-axis" x="10" y="24">局数</text>`);
  parts.push('</svg>');
  return parts.join('');
}

function tableHtml(headers, rows) {
  const thead = '<thead><tr>' + headers.map(h => `<th>${h}</th>`).join('') + '</tr></thead>';
  const tbody = '<tbody>' + rows.map(r => '<tr>' + r.map(c => `<td>${c}</td>`).join('') + '</tr>').join('') + '</tbody>';
  return thead + tbody;
}
