/* 候选组合矩阵（10×10）
 *
 * 规则：组合 (a,b) 与 (b,a) 在游戏语义上完全等价（组合是无序对），
 * 因此 10×10 的表格里「a-b」与「b-a」两格表示**同一个**组合：
 *   - 两格状态永远一致（灰掉 / 颜色标记都同步）；
 *   - 点选任意一格都表示选择规范形式 (min,max)。
 * 被规则删除的 10 个组合都是 a<b 的形式，所以在表里各占 2 格 = 20 格灰色。
 *
 * 反馈颜色标记（只是备忘，**不影响**点选交互）：
 *   CORRECT 绿 / MISPLACED 蓝 / PARTIAL 紫 / WRONG 红
 */
const MARK_RANK = { CORRECT: 4, MISPLACED: 3, PARTIAL: 2, WRONG: 1 };
const MARK_LETTER = { CORRECT: 'C', MISPLACED: 'M', PARTIAL: 'P', WRONG: 'W' };
const MARK_TEXT = {
  CORRECT: '已就位（该位置就是它）',
  MISPLACED: '属于秘密，但不在此位置',
  PARTIAL: '不属于秘密（与本位至少共享一个对象）',
  WRONG: '不属于秘密',
};

/** 组合的规范形式（小在前） */
function canonicalCombo(a, b) { return a <= b ? [a, b] : [b, a]; }
/** 规范化键，用作矩阵格子的索引 */
function comboKey(c) { return `${c[0]}-${c[1]}`; }
/** 矩阵单元格标签（保留书写顺序，便于“点 6-3 即选 3-6”的直觉） */
function cellLabel(a, b) { return `${a}-${b}`; }

/**
 * 累积若干轮的组合状态标记。
 * @param {Array} rounds 每项形如 {index, guess, feedback}
 * @param {Object} opts {minRound} 只要 round >= minRound 的轮次（默认全部）
 * @returns {Object} { "a-b": {kind, notes:[{round, position, kind}]} }
 */
function accumulateMarks(rounds, opts) {
  const options = opts || {};
  const minRound = options.minRound || 0;
  const marks = {};
  (rounds || []).forEach((r, i) => {
    const round = r.index || i + 1;
    if (round < minRound) return;
    (r.guess || []).forEach((combo, pos) => {
      const kind = (r.feedback || [])[pos];
      if (!kind || !MARK_RANK[kind]) return;
      const key = comboKey(combo);
      const entry = marks[key] || (marks[key] = { kind, notes: [] });
      entry.notes.push({ round, position: pos + 1, kind });
      if (MARK_RANK[kind] > MARK_RANK[entry.kind]) entry.kind = kind;
    });
  });
  return marks;
}

/** 把「当前待提交的输入」覆盖到已有标记上（用于逐步猜测页的即时提示） */
function overlayMarks(marks, guess, answers, label) {
  const out = {};
  Object.keys(marks || {}).forEach(k => {
    out[k] = { kind: marks[k].kind, notes: (marks[k].notes || []).slice() };
  });
  if (!guess) return out;
  guess.forEach((combo, pos) => {
    const letter = (answers || [])[pos];
    if (!letter) return;
    const kind = typeof letter === 'string' && letter.length > 1
      ? letter : Object.keys(MARK_LETTER).find(k => MARK_LETTER[k] === letter);
    if (!kind) return;
    const key = comboKey(combo);
    const entry = out[key] || (out[key] = { kind, notes: [] });
    entry.notes.push({ round: null, position: pos + 1, kind, pending: true, label: label || '待提交' });
    if (MARK_RANK[kind] > MARK_RANK[entry.kind]) entry.kind = kind;
  });
  return out;
}

/** 生成一个格子的提示文案 */
function cellTooltip(a, b, combo, removed, mark, picked, locked) {
  const lines = [];
  lines.push(removed
    ? `(${combo[0]}, ${combo[1]}) 已被规则删除，不属于候选集合 S`
    : `(${combo[0]}, ${combo[1]})`);
  if (a !== b) lines.push(`对称格 ${b}-${a} 与它等价，两格状态始终一致`);
  if (locked) lines.push('🔒 已锁定：该组合已经在某个位置判定 CORRECT，不会再作为待选');
  if (picked) lines.push('本轮猜测用到了它');
  if (mark && mark.notes && mark.notes.length) {
    const notes = mark.notes.map(n =>
      n.pending
        ? `${n.label}第 ${n.position} 位：${MARK_TEXT[n.kind]}`
        : `第 ${n.round} 轮第 ${n.position} 位：${MARK_TEXT[n.kind]}`);
    lines.push(notes.join('；'));
    if (mark.notes.some(n => !n.pending)) {
      lines.push(`当前标记：${MARK_LETTER[mark.kind]}（${MARK_TEXT[mark.kind]}）`);
    }
  } else if (!removed) {
    lines.push('还没有反馈信息');
  }
  if (!removed) lines.push('点击即把它填入下一个空槽');
  return lines.join('\n');
}

/**
 * 渲染 10×10 候选组合矩阵。
 * @param {HTMLElement} host 容器（其内容会被清空）
 * @param {Object} opts
 *   objects   [1..10]
 *   removed   Set<"a-b"> 被规则删除的组合键
 *   marks     accumulateMarks() 的结果
 *   picked    Set<"a-b"> 本轮猜测用到的组合（描边高亮）
 *   locked    Set<"a-b"> 已锁定（该组合已在某个位置判定 CORRECT）
 *   onPick    (combo) => void
 */
function renderComboMatrix(host, opts) {
  if (!host) return;
  const objects = opts.objects || [];
  const removed = opts.removed || new Set();
  const marks = opts.marks || {};
  const picked = opts.picked || new Set();
  const locked = opts.locked || new Set();
  const onPick = opts.onPick;

  const table = document.createElement('table');
  table.className = 'cg-table' + (opts.compact ? ' cg-compact' : '');

  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  const corner = document.createElement('th');
  corner.className = 'cg-corner';
  corner.textContent = 'a\\b';
  corner.title = '行 = 前一位 a，列 = 后一位 b；(a,b) 与 (b,a) 是同一个组合';
  headRow.appendChild(corner);
  objects.forEach(b => {
    const th = document.createElement('th');
    th.textContent = b;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  objects.forEach(a => {
    const tr = document.createElement('tr');
    const rowHead = document.createElement('th');
    rowHead.className = 'cg-rowhead';
    rowHead.textContent = a;
    tr.appendChild(rowHead);
    objects.forEach(b => {
      const combo = canonicalCombo(a, b);
      const key = comboKey(combo);
      const gone = removed.has(key);
      const mark = marks[key];
      const isPicked = picked.has(key);
      const isLocked = locked.has(key);

      const td = document.createElement('td');
      td.className = 'cg-cell' + (a === b ? ' cg-diag' : '');
      if (gone) td.classList.add('cg-removed');
      if (isPicked) td.classList.add('cg-picked');
      if (isLocked) td.classList.add('cg-locked');
      if (mark && MARK_RANK[mark.kind]) td.classList.add('mark-' + FEEDBACK_CLASS[mark.kind]);

      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'cg-btn';
      btn.textContent = cellLabel(a, b) + (isLocked ? ' 🔒' : '');
      btn.title = cellTooltip(a, b, combo, gone, mark, isPicked, isLocked);
      if (gone) {
        btn.disabled = true;
        btn.setAttribute('aria-disabled', 'true');
      } else if (onPick) {
        btn.addEventListener('click', () => onPick(combo));
      }
      td.appendChild(btn);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);

  host.innerHTML = '';
  host.appendChild(table);
}

/** 把候选集合（45 个组合）渲染成键集合，便于矩阵判灰 */
function removedKeySet(removedCombos) {
  return new Set((removedCombos || []).map(c => comboKey(c)));
}
