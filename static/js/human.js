/* 逐步猜测模式：策略出招 → 人工录入逐位反馈 → 下一轮，直到全部 CORRECT 后落盘 */
(() => {
  const LETTERS = ['C', 'M', 'P', 'W'];
  const KIND = { C: 'CORRECT', M: 'MISPLACED', P: 'PARTIAL', W: 'WRONG' };
  const KIND_DESC = {
    C: '完全正确：g_i == x_i',
    M: '正确但位置错误：该组合出现在别的位（会掩盖本位的真实信息）',
    P: '部分正确：该组合不在秘密里，但与 x_i 至少共享一个对象',
    W: '完全错误：该组合不在秘密里，且与 x_i 无共享对象',
  };

  const state = { meta: null, strategies: [], game: null, answers: new Array(10).fill(null), viewing: null };
  const $ = id => document.getElementById(id);
  let cellButtons = [];

  // ------------------------------------------------------------ 参数控件
  function currentParams() {
    const opt = state.strategies.find(s => s.key === $('strategy').value);
    if (!opt) return {};
    const params = {};
    opt.params.forEach(spec => {
      const el = document.getElementById(`p_${spec.name}`);
      if (!el) return;
      if (spec.type === 'bool') params[spec.name] = el.checked;
      else if (spec.type === 'int') params[spec.name] = parseInt(el.value, 10);
      else if (spec.type === 'float') params[spec.name] = parseFloat(el.value);
      else params[spec.name] = el.value;
    });
    return params;
  }

  function renderParamControls() {
    const host = $('strategyParams');
    host.innerHTML = '';
    const opt = state.strategies.find(s => s.key === $('strategy').value);
    if (!opt) return;
    opt.params.forEach(spec => {
      const label = document.createElement('label');
      label.innerHTML = `<span class="muted">${spec.label}</span> `;
      let input;
      if (spec.type === 'bool') {
        input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = spec.default;
      } else if (spec.type === 'choice') {
        input = document.createElement('select');
        spec.choices.forEach(c => {
          const o = document.createElement('option');
          o.value = c; o.textContent = c;
          if (c === spec.default) o.selected = true;
          input.appendChild(o);
        });
      } else {
        input = document.createElement('input');
        input.type = 'number';
        input.value = spec.default;
        if (spec.min !== undefined && spec.min !== null) input.min = spec.min;
        if (spec.max !== undefined && spec.max !== null) input.max = spec.max;
        if (spec.step) input.step = spec.step;
        input.style.width = '78px';
      }
      input.id = `p_${spec.name}`;
      input.title = spec.help || '';
      label.appendChild(input);
      host.appendChild(label);
    });
  }

  // ------------------------------------------------------------ 渲染
  function banner(text, kind) {
    const box = $('banner');
    if (!text) { box.hidden = true; return; }
    box.hidden = false;
    box.className = 'banner' + (kind ? ' ' + kind : '');
    box.innerHTML = text;
  }

  // 决策解释：默认看当前待反馈的猜测，点历史行可以回看该轮
  function renderDecisionPanel(decision, source) {
    const box = $('decisionBox');
    if (!decision) {
      box.innerHTML = '<div class="muted">当前没有可解释的猜测（先让策略出招，或点历史里某一轮的「理由」）。</div>';
      return;
    }
    renderDecision(box, decision, { strategyName: state.game ? state.game.strategyName : '' });
    if (source) $('decHint').textContent = source;
  }

  function renderGrid() {
    const host = $('guessGrid');
    host.innerHTML = '';
    cellButtons = [];
    const guess = state.game && state.game.pendingGuess;
    if (!guess) {
      const tip = !state.game ? '还没有对局，先点「开始新对局」。'
        : state.game.solved ? '本局已全部 CORRECT。'
          : '当前没有待反馈的猜测，点下面的「让策略出招」。';
      host.innerHTML = `<div class="muted" style="padding:8px 2px">${tip}</div>`;
      if (state.game && !state.game.solved && state.game.status === 'awaiting_guess') {
        const btn = document.createElement('button');
        btn.className = 'primary';
        btn.textContent = '让策略出招';
        btn.addEventListener('click', nextGuess);
        host.appendChild(btn);
      }
      updateSubmitState();
      return;
    }
    // 已判定 CORRECT 的位置：锁定（预填 C，但仍可修改——人工录入的是事实，不是选择）
    const locked = lockedPositions();
    locked.forEach((info, pos) => {
      if (!state.answers[pos - 1]) {
        state.answers[pos - 1] = (comboKey(guess[pos - 1]) === comboKey(info.combo)) ? 'C' : null;
      }
    });
    guess.forEach((combo, i) => {
      const info = locked.get(i + 1);
      const cell = document.createElement('div');
      cell.className = 'fgcell' + (info ? ' locked' : '');
      const head = document.createElement('div');
      head.className = 'fghead';
      head.innerHTML = `<span class="fgpos">第 ${i + 1} 位${info ? '<span class="lockicon" title="该位已判定 CORRECT">🔒</span>' : ''}</span>` +
        `<span class="fgcombo">${comboLabel(combo)}</span>`;      cell.appendChild(head);
      if (info) cell.title = `第 ${info.round} 轮已判定 CORRECT（${comboLabel(info.combo)}）`;
      const row = document.createElement('div');
      row.className = 'fbrow';
      LETTERS.forEach(letter => {
        const b = document.createElement('button');
        b.className = 'fbbtn ' + FEEDBACK_CLASS[KIND[letter]] + (state.answers[i] === letter ? ' sel' : '');
        b.textContent = letter;
        b.title = KIND_DESC[letter];
        b.addEventListener('click', () => {
          state.answers[i] = letter;
          renderGrid();
          renderMatrix();
          focusCell(i + 1);
        });
        row.appendChild(b);
      });
      cell.appendChild(row);
      host.appendChild(cell);
      cellButtons.push([...row.children]);
    });
    updateSubmitState();
  }

  /** 历史上已判定 CORRECT 的位置 → {位置: {combo, round}} */
  function lockedPositions() {
    const map = new Map();
    const source = state.viewing || state.game;
    const history = state.viewing
      ? (state.viewing.trace || []).map(t => ({ index: t.index, guess: t.guess, feedback: t.feedback }))
      : ((source && source.history) || []);
    history.forEach(r => {
      (r.feedback || []).forEach((kind, i) => {
        if (kind === 'CORRECT') map.set(i + 1, { combo: r.guess[i], round: r.index });
      });
    });
    return map;
  }

  function renderMatrix() {
    if (!state.meta) return;
    const source = state.viewing || state.game;
    const rounds = state.viewing
      ? (state.viewing.trace || [])          // 看存档：只有已提交的轮次
      : (source ? (source.history || []) : []);
    let marks = accumulateMarks(rounds);
    if (!state.viewing && state.game && state.game.pendingGuess) {
      // 正在录入的反馈即时叠加，方便边看边对（标注为“待提交”）
      marks = overlayMarks(marks, state.game.pendingGuess, state.answers, '待提交');
    }
    const pending = state.viewing ? [] : ((state.game && state.game.pendingGuess) || []);
    renderComboMatrix($('comboMatrix'), {
      objects: state.meta.objects,
      removed: removedKeySet(state.meta.removedCombos),
      marks,
      picked: new Set(pending.filter(Boolean).map(comboKey)),
      locked: new Set([...lockedPositions().values()].map(v => comboKey(v.combo))),
    });
    const badge = $('matrixBadge');
    if (badge) badge.textContent = `${state.meta.candidateCount} 候选`;
  }

  function focusCell(index) {
    if (index >= 0 && index < cellButtons.length && cellButtons[index] && cellButtons[index][0]) {
      cellButtons[index][0].focus();
    }
  }

  function updateSubmitState() {
    const filled = state.answers.filter(Boolean).length;
    $('answerHint').textContent = `已标注 ${filled}/10`;
    $('btnSubmit').disabled = filled !== 10 || !state.game || !state.game.pendingGuess;
  }

  function renderHistory() {
    const host = $('history');
    host.innerHTML = '';
    const source = state.viewing || state.game;
    if (!source) { $('histBadge').textContent = '0 轮'; return; }
    const history = state.viewing ? (state.viewing.trace || []).map(t => ({ guess: t.guess, feedback: t.feedback }))
      : (source.history || []).map(h => ({ guess: h.guess, feedback: h.feedback }));
    const warnings = state.viewing ? (state.viewing.trace || []).map(t => t.warnings || []) : (source.roundWarnings || []);
    if (state.viewing) {
      const back = document.createElement('div');
      back.className = 'row';
      back.innerHTML = `<span class="muted">正在查看已落盘对局 <b>${state.viewing.label || state.viewing.gameId}</b>` +
        `（${state.viewing.solved ? '已解出' : '未解出'}，${state.viewing.rounds} 轮）</span>`;
      const btn = document.createElement('button');
      btn.className = 'small';
      btn.textContent = '返回当前对局';
      btn.addEventListener('click', () => { state.viewing = null; renderHistory(); renderMatrix(); $('histHint').textContent = '每一轮由策略出招、你录入反馈。'; });
      back.appendChild(btn);
      host.appendChild(back);
    }
    history.forEach((h, i) => {
      const decisions = state.viewing ? (state.viewing.decisions || []) : (source.decisions || []);
      renderRound(host, i + 1, h.guess, h.feedback, {
        decision: decisions[i],
        onReason: (decision, index) => {
          renderDecisionPanel(decision, null);
          $('decHint').textContent = `正在查看第 ${index} 轮的逐位理由（反馈 ` +
            `${[...h.feedback].map(k => LETTER[k]).join('')}）`;
          const box = $('decisionBox');
          if (box.scrollIntoView) box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        },
      });
      const warn = warnings[i];
      if (warn && warn.length) {
        const wline = document.createElement('div');
        wline.className = 'warnline';
        wline.textContent = '⚠️ ' + warn.join('；');
        host.appendChild(wline);
      }
    });
    $('histBadge').textContent = `${history.length} 轮`;
  }

  function refreshStatus() {
    const g = state.game;
    if (!g) { setStatus($('status'), '选好策略后点击「开始新对局」：策略会先给出首轮猜测，你只需照实录入逐位反馈。'); return; }
    const parts = [`对局 ${g.gameId}`, `策略 ${g.strategyName || g.strategy}`, `模式 逐步猜测`];
    if (g.label) parts.push(`标签 ${g.label}`);
    parts.push(`已用 ${g.rounds} 轮`);
    parts.push(g.status === 'awaiting_feedback' ? '等待录入反馈'
      : g.status === 'awaiting_guess' ? '等待策略出招' : '已全部 CORRECT');
    const lockedN = lockedPositions().size;
    if (lockedN) parts.push(`已锁定 ${lockedN} 位 🔒`);
    if (g.saved) parts.push(`✅ 已落盘`);
    setStatus($('status'), parts.join(' · '), g.solved ? 'ok' : '');
    const support = g.support || {};
    $('histHint').textContent =
      `推断出的支持集：属于 ${support.knownIn || 0} 个 · 不属于 ${support.knownOut || 0} 个 · 已定位 ${support.placed || 0} 位`;
  }

  function applyState(next) {
    const prevKey = state.game && state.game.pendingGuess ? JSON.stringify(state.game.pendingGuess) : null;
    const nextKey = next && next.pendingGuess ? JSON.stringify(next.pendingGuess) : null;
    state.game = next;
    if (prevKey !== nextKey) {
      // 换了一轮猜测：清空之前的标注
      state.answers = new Array(10).fill(null);
      const quick = $('quick');
      if (quick) quick.value = '';
    }
    renderGrid();
    renderHistory();
    renderMatrix();
    refreshStatus();
    // 换了一轮猜测：解释面板回到“当前待反馈猜测”
    if (!state.viewing) {
      const g = next;
      const decision = g ? (g.pendingDecision || null) : null;
      renderDecisionPanel(decision, decision
        ? `当前待反馈的猜测（第 ${decision.round} 轮）由 ${g.strategyName || g.strategy} 给出，逐位理由如下。`
        : null);
    }
    $('btnUndo').disabled = !next || (!(next.history || []).length && !next.pendingGuess);
    $('btnSave').disabled = !next || !(next.history || []).length;
  }

  // ------------------------------------------------------------ 动作
  async function newGame() {
    const body = {
      strategy: $('strategy').value,
      params: currentParams(),
      label: $('label').value,
      strict: $('strict').checked,
      autoSave: $('autoSave').checked,
      assumeDistinct: $('assumeDistinct').checked,
    };
    try {
      banner('');
      state.viewing = null;
      state.answers = new Array(10).fill(null);
      $('quick').value = '';
      const payload = await API.post('/api/human/games', body);
      applyState(payload);
      toast('已开始新对局，请照实录入第 1 轮的反馈');
    } catch (e) { banner(e.message, 'error'); }
  }

  async function nextGuess() {
    try {
      const payload = await API.post(`/api/human/games/${state.game.gameId}/next`, {});
      state.answers = new Array(10).fill(null);
      $('quick').value = '';
      applyState(payload);
    } catch (e) { banner(e.message, 'error'); }
  }

  async function submit() {
    if (state.answers.filter(Boolean).length !== 10) { banner('请先把 10 个位置的反馈都标好', 'error'); return; }
    try {
      const res = await API.post(`/api/human/games/${state.game.gameId}/feedback`, { feedback: state.answers });
      if (res.warnings && res.warnings.length) {
        banner('⚠️ 反馈已记录，但与已有记录存在矛盾，请核对：<br>' +
          res.warnings.map(w => '· ' + w).join('<br>'), 'warn');
      } else {
        banner('✅ 第 ' + res.state.rounds + ' 轮已记录（一致性校验通过）', 'ok');
      }
      state.answers = new Array(10).fill(null);
      $('quick').value = '';
      applyState(res.state);
      if (res.state.solved) {
        const saved = res.state.saved ? `已自动落盘：<code>${res.state.savedPath || ''}</code>` : '请点「落盘并记录」保存。';
        banner(`🎉 全部位置 CORRECT，共 ${res.state.rounds} 轮。${saved}`, 'ok');
        refreshArchive();
      } else {
        await nextGuess();
      }
    } catch (e) { banner(e.message, 'error'); }
  }

  async function undo() {
    try {
      const res = await API.post(`/api/human/games/${state.game.gameId}/undo`, {});
      state.answers = new Array(10).fill(null);
      $('quick').value = '';
      state.viewing = null;
      applyState(res.state);
      banner(res.undone ? '已撤销上一轮' : '已丢弃待反馈的猜测', 'ok');
    } catch (e) { banner(e.message, 'error'); }
  }

  async function save() {
    try {
      const res = await API.post(`/api/human/games/${state.game.gameId}/save`, {});
      applyState(res.state);
      banner(`💾 已落盘：<code>${res.path}</code>，并已写入 <code>Doc/08_对局与模拟日志.md</code>`, 'ok');
      refreshArchive();
    } catch (e) { banner(e.message, 'error'); }
  }

  async function refreshArchive() {
    try {
      const data = await API.get('/api/human/archive?limit=30');
      const rows = (data.games || []).map(g => {
        const when = g.savedAt ? new Date(g.savedAt * 1000).toLocaleString() : '';
        const view = `<button class="small" data-gid="${g.gameId}">查看</button>`;
        return [
          g.label || '（无标签）',
          g.strategy || '',
          `${g.rounds || 0} 轮`,
          g.solved ? '✅ 已解出' : '—',
          when,
          view,
        ];
      });
      const table = $('archiveTable');
      table.innerHTML = tableHtml(['标签', '策略', '轮数', '结果', '落盘时间', ''], rows);
      table.querySelectorAll('button[data-gid]').forEach(btn => {
        btn.addEventListener('click', () => viewArchive(btn.dataset.gid));
      });
    } catch (e) { /* 忽略 */ }
  }

  async function viewArchive(gid) {
    try {
      const record = await API.get(`/api/human/archive/${gid}`);
      state.viewing = record;
      renderHistory();
      renderMatrix();       // 看存档时矩阵也只展示已提交的轮次
    } catch (e) { toast(e.message, true); }
  }

  // ------------------------------------------------------------ 初始化
  document.addEventListener('DOMContentLoaded', async () => {
    try {
      const res = await API.get('/api/strategies');
      state.strategies = res.strategies;
      state.meta = await API.get('/api/meta');
      const sel = $('strategy');
      state.strategies.forEach(s => {
        const o = document.createElement('option');
        o.value = s.key;
        o.textContent = s.name;
        sel.appendChild(o);
      });
      sel.value = 'two_phase';
      sel.addEventListener('change', renderParamControls);
      renderParamControls();
      renderMatrix();
      refreshArchive();
    } catch (e) { toast(e.message, true); }

    $('btnNew').addEventListener('click', newGame);
    $('btnSubmit').addEventListener('click', submit);
    $('btnUndo').addEventListener('click', undo);
    $('btnSave').addEventListener('click', save);
    $('btnRefreshArchive').addEventListener('click', refreshArchive);
    $('btnAllC').addEventListener('click', () => {
      state.answers = new Array(10).fill('C');
      renderGrid();
      renderMatrix();
    });
    $('btnClear').addEventListener('click', () => {
      state.answers = new Array(10).fill(null);
      $('quick').value = '';
      renderGrid();
      renderMatrix();
    });
    $('quick').addEventListener('input', ev => {
      const letters = ev.target.value.toUpperCase().replace(/[^CMPW]/g, '').slice(0, 10).split('');
      state.answers = new Array(10).fill(null);
      letters.forEach((ch, i) => { state.answers[i] = ch; });
      renderGrid();
      renderMatrix();
    });
    document.addEventListener('keydown', ev => {
      if (ev.target.tagName === 'INPUT' || ev.target.tagName === 'SELECT') return;
      if (ev.key === 'Enter') { ev.preventDefault(); submit(); }
      if (ev.key === 'Backspace') { ev.preventDefault(); undo(); }
    });
  });
})();
