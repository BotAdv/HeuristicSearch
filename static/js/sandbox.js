/* 沙盒页：手动试玩 + 策略自动 + 多策略对比
 *
 * 三种模式：
 *   manual   手动试玩：点候选矩阵填槽位；可把当前历史交给任意策略预测下一步（附解释）
 *   strategy 策略自动：单策略逐步/一次跑完
 *   multi    多策略对比：同一条秘密下多个策略各自推进，面板里并排展示
 *            （各自的猜测序列、对局历史、候选组合矩阵、决策解释）
 */
(() => {
  const state = {
    meta: null,
    strategies: [],
    game: null,
    slots: new Array(10).fill(null),
    plans: [],            // 多策略模式：[{key, params}]
    multi: null,          // 多策略会话状态
    advice: null,         // 最近一次“交给策略预测”的结果
  };

  const $ = id => document.getElementById(id);
  const statusBox = () => $('status');
  const MAX_PLANS = 6;

  // ------------------------------------------------------------ 参数控件
  function readParams(prefix, opt) {
    const params = {};
    if (!opt) return params;
    opt.params.forEach(spec => {
      const el = document.getElementById(`${prefix}${spec.name}`);
      if (!el) return;
      if (spec.type === 'bool') params[spec.name] = el.checked;
      else if (spec.type === 'int') params[spec.name] = parseInt(el.value, 10);
      else if (spec.type === 'float') params[spec.name] = parseFloat(el.value);
      else params[spec.name] = el.value;
    });
    return params;
  }

  function buildParamControls(host, opt, prefix, params) {
    host.innerHTML = '';
    if (!opt) return;
    opt.params.forEach(spec => {
      const label = document.createElement('label');
      label.innerHTML = `<span class="muted">${spec.label}</span> `;
      const current = params && params[spec.name] !== undefined ? params[spec.name] : spec.default;
      let input;
      if (spec.type === 'bool') {
        input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = !!current;
      } else if (spec.type === 'choice') {
        input = document.createElement('select');
        spec.choices.forEach(c => {
          const o = document.createElement('option');
          o.value = c; o.textContent = c;
          if (c === current) o.selected = true;
          input.appendChild(o);
        });
      } else {
        input = document.createElement('input');
        input.type = 'number';
        input.value = current;
        if (spec.min !== undefined && spec.min !== null) input.min = spec.min;
        if (spec.max !== undefined && spec.max !== null) input.max = spec.max;
        if (spec.step) input.step = spec.step;
        input.style.width = '78px';
      }
      input.id = `${prefix}${spec.name}`;
      input.title = spec.help || '';
      label.appendChild(input);
      host.appendChild(label);
    });
  }

  const findStrategy = key => state.strategies.find(s => s.key === key);

  const renderParamControls = () =>
    buildParamControls($('strategyParams'), findStrategy($('strategy').value), 'p_', null);
  const renderAdviseParams = () =>
    buildParamControls($('adviseParams'), findStrategy($('adviseStrategy').value), 'a_', null);

  function fillStrategySelect(sel, prefer) {
    sel.innerHTML = '';
    state.strategies.forEach(s => {
      const o = document.createElement('option');
      o.value = s.key;
      o.textContent = s.name;
      sel.appendChild(o);
    });
    if (prefer) sel.value = prefer;
  }

  // ------------------------------------------------------------ 多策略清单
  function addPlan(key, params) {
    if (state.plans.length >= MAX_PLANS) { toast(`最多同时对比 ${MAX_PLANS} 个策略`); return; }
    state.plans.push({ key: key || 'two_phase', params: params || {} });
    renderPlans();
  }

  function renderPlans() {
    const host = $('planList');
    host.innerHTML = '';
    $('planCount').textContent = state.plans.length;
    state.plans.forEach((plan, idx) => {
      const row = document.createElement('div');
      row.className = 'plan-row';
      const name = document.createElement('div');
      name.className = 'name';
      name.textContent = `#${idx + 1}`;
      row.appendChild(name);

      const sel = document.createElement('select');
      fillStrategySelect(sel, plan.key);
      sel.addEventListener('change', () => {
        state.plans[idx].key = sel.value;
        state.plans[idx].params = {};
        renderPlans();
      });
      row.appendChild(sel);

      const params = document.createElement('span');
      params.className = 'params-inline';
      row.appendChild(params);
      buildParamControls(params, findStrategy(plan.key), `mp_${idx}_`, plan.params);

      const del = document.createElement('button');
      del.className = 'small';
      del.textContent = '移除';
      del.addEventListener('click', () => {
        state.plans.splice(idx, 1);
        renderPlans();
      });
      row.appendChild(del);
      host.appendChild(row);
    });
  }

  function currentPlans() {
    return state.plans.map((plan, idx) => ({
      key: plan.key,
      params: readParams(`mp_${idx}_`, findStrategy(plan.key)),
    }));
  }

  // ------------------------------------------------------------ 单策略视图
  function renderPalette() {
    $('paletteCount').textContent = state.meta.candidateCount;
    const marks = accumulateMarks(state.game ? (state.game.history || []) : []);
    const picked = new Set(state.slots.filter(Boolean).map(comboKey));
    renderComboMatrix($('comboMatrix'), {
      objects: state.meta.objects,
      removed: removedKeySet(state.meta.removedCombos),
      marks,
      picked,
      onPick: place,
    });
  }

  function place(combo) {
    if (!isManualMode()) return;
    const idx = state.slots.findIndex(s => s === null);
    if (idx < 0) { toast('10 个槽位已填满，请先清空或删除某个槽位'); return; }
    state.slots[idx] = combo;
    state.advice = null;
    renderSlots();
    renderPalette();
  }

  function renderSlots(lastFeedback) {
    const host = $('slots');
    host.innerHTML = '';
    state.slots.forEach((combo, i) => {
      const div = document.createElement('div');
      div.className = 'slot' + (combo ? ' filled' : '');
      div.innerHTML = `<span class="idx">${i + 1}</span><span>${combo ? comboLabel(combo) : '—'}</span>`;
      div.addEventListener('click', () => {
        if (!isManualMode()) return;
        state.slots[i] = null;
        state.advice = null;
        renderSlots();
        renderPalette();
      });
      host.appendChild(div);
    });
    $('slotCount').textContent = `${state.slots.filter(Boolean).length}/10`;
    if (lastFeedback) {
      const cells = host.children;
      state.slots.forEach((_, i) => {
        if (cells[i] && lastFeedback[i]) cells[i].classList.add(FEEDBACK_CLASS[lastFeedback[i]]);
      });
    }
  }

  function renderHistory() {
    const host = $('history');
    host.innerHTML = '';
    if (!state.game) return;
    state.game.history.forEach(r => renderRound(host, r.index, r.guess, r.feedback, {
      decision: r.decision,
      onReason: (decision, index) => showDecision(decision,
        `策略 ${state.game.strategy} 第 ${index} 轮（${r.letters}）的逐位理由`),
    }));
    $('roundBadge').textContent = `${state.game.rounds} 轮`;
    host.scrollTop = host.scrollHeight;
  }

  function renderSecret() {
    const box = $('secretBox');
    const show = $('reveal').checked;
    if (show && state.game && state.game.secret) {
      box.hidden = false;
      box.textContent = '秘密序列：' + state.game.secret.map(comboLabel).join(' ');
    } else {
      box.hidden = true;
    }
  }

  function renderExplain(decision, source) {
    const panel = $('explainPanel');
    if (!decision) { panel.hidden = true; $('explainBox').innerHTML = ''; return; }
    panel.hidden = false;
    $('explainSource').textContent = source || '';
    renderDecision($('explainBox'), decision, { strategyName: state.game && state.game.strategy });
  }

  /** 展示某个决策的解释（并滚动到面板） */
  function showDecision(decision, source) {
    renderExplain(decision, source);
    const panel = $('explainPanel');
    if (panel.scrollIntoView) panel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  // ------------------------------------------------------------ 多策略视图
  function renderMulti() {
    const grid = $('multiGrid');
    grid.innerHTML = '';
    const st = state.multi;
    if (!st) return;
    st.entries.forEach((entry, idx) => {
      const card = document.createElement('section');
      card.className = 'panel multi-card';
      const head = document.createElement('header');
      head.innerHTML =
        `<h2>${entry.strategyName || entry.strategy} ` +
        `<span class="badge">${entry.rounds} 轮${entry.solved ? ' · 已解出' : ''}</span></h2>` +
        `<p class="hint">key <code>${entry.strategy}</code> · 参数 <code>${JSON.stringify(entry.params || {})}</code></p>`;
      card.appendChild(head);

      const actions = document.createElement('div');
      actions.className = 'row';
      const stepBtn = document.createElement('button');
      stepBtn.className = 'small';
      stepBtn.textContent = '只让这个策略走一步';
      stepBtn.disabled = !!entry.solved;
      stepBtn.addEventListener('click', () => stepMulti(idx));
      actions.appendChild(stepBtn);
      card.appendChild(actions);

      // 本轮猜测（带反馈颜色）
      const last = (entry.history || [])[entry.history.length - 1];
      const slots = document.createElement('div');
      slots.className = 'slots slots-compact';
      const guess = last ? last.guess : new Array(10).fill(null);
      guess.forEach((combo, i) => {
        const div = document.createElement('div');
        div.className = 'slot filled';
        div.innerHTML = `<span class="idx">${i + 1}</span><span>${combo ? comboLabel(combo) : '—'}</span>`;
        if (last && last.feedback && last.feedback[i]) div.classList.add(FEEDBACK_CLASS[last.feedback[i]]);
        if (last) div.title = `第 ${last.index} 轮：${comboLabel(combo)} → ${LETTER[last.feedback[i]]}`;
        slots.appendChild(div);
      });
      card.appendChild(slots);

      // 候选矩阵（紧凑版）
      const wrap = document.createElement('div');
      wrap.className = 'palette-wrap';
      const holder = document.createElement('div');
      wrap.appendChild(holder);
      card.appendChild(wrap);
      renderComboMatrix(holder, {
        objects: state.meta.objects,
        removed: removedKeySet(state.meta.removedCombos),
        marks: accumulateMarks(entry.history || []),
        picked: new Set((last ? last.guess : []).filter(Boolean).map(comboKey)),
        compact: true,
      });

      const hist = document.createElement('div');
      hist.className = 'history history-compact';
      (entry.history || []).forEach(r => renderRound(hist, r.index, r.guess, r.feedback));
      card.appendChild(hist);

      if (last && last.decision) {
        const details = document.createElement('details');
        const summary = document.createElement('summary');
        summary.textContent = `第 ${last.index} 轮：为什么这么选（${last.letters}）`;
        details.appendChild(summary);
        const box = document.createElement('div');
        box.className = 'dec-box';
        details.appendChild(box);
        renderDecision(box, last.decision, { strategyName: entry.strategyName });
        card.appendChild(details);
      }
      grid.appendChild(card);
    });
  }

  // ------------------------------------------------------------ 模式
  const mode = () => $('mode').value;
  const isStrategyMode = () => mode() === 'strategy';
  const isMultiMode = () => mode() === 'multi';
  const isManualMode = () => mode() === 'manual';

  function updateButtons() {
    const multi = isMultiMode();
    const has = !!state.game;
    $('strategyGroup').hidden = !isStrategyMode();
    $('adviseGroup').hidden = multi;
    $('planPanel').hidden = !multi;
    $('sandboxMain').hidden = multi;
    $('multiGrid').hidden = !multi;
    $('btnClear').disabled = !has || !isManualMode();
    $('btnAdvise').disabled = !has || !isManualMode() || (!!state.game && state.game.solved);
    if (multi) {
      $('btnStep').disabled = !state.multi || state.multi.finished;
      $('btnPlay').disabled = !state.multi || state.multi.finished;
      $('btnStep').textContent = '所有策略各走一步';
    } else {
      $('btnStep').disabled = !has || state.game.solved || state.game.exhausted;
      $('btnPlay').disabled = !has || state.game.solved || state.game.exhausted || !isStrategyMode();
      $('btnStep').textContent = isStrategyMode() ? '走一步' : '提交猜测';
    }
    $('palette').style.opacity = isManualMode() ? 1 : 0.45;
  }

  function refreshStatus() {
    if (isMultiMode()) {
      if (!state.multi) {
        setStatus(statusBox(), '配置参与对比的策略（可改参数、添加变体）后点「开新局」：所有策略面对同一条秘密。');
        return;
      }
      const st = state.multi;
      const detail = st.entries.map(e => `${e.strategy}：${e.rounds} 轮${e.solved ? ' ✅' : ''}`).join(' · ');
      setStatus(statusBox(),
        `对局 ${st.gameId} · 模式 多策略对比 · 共 ${st.entries.length} 个策略 | ${detail}`,
        st.solved ? 'ok' : '');
      return;
    }
    if (!state.game) { setStatus(statusBox(), '点击「开新局」开始。'); return; }
    const g = state.game;
    const parts = [`对局 ${g.gameId}`, `模式 ${g.mode === 'strategy' ? '策略自动' : '手动'}`];
    if (g.strategy) parts.push(`策略 ${g.strategy}`);
    parts.push(`已用 ${g.rounds} 轮 / 上限 ${g.maxRounds}`);
    if (g.solved) parts.push('✅ 已解出');
    else if (g.exhausted) parts.push('⚠️ 达到轮次上限仍未解出');
    if (isManualMode() && state.advice) parts.push(`本轮猜测由 ${state.advice.strategyName || state.advice.strategy} 预测`);
    setStatus(statusBox(), parts.join(' · '), g.solved ? 'ok' : '');
  }

  async function applyState(payload, lastFeedback) {
    state.game = payload;
    if (payload.history && payload.history.length) {
      const last = payload.history[payload.history.length - 1];
      state.slots = last.guess.slice();
    }
    renderSlots(lastFeedback);
    renderPalette();
    renderHistory();
    renderSecret();
    updateButtons();
    refreshStatus();
  }

  // ------------------------------------------------------------ 动作
  async function newGame() {
    const body = {
      mode: mode(),
      maxRounds: parseInt($('maxRounds').value, 10) || 40,
      reveal: $('reveal').checked,
    };
    const seed = $('seed').value;
    if (seed !== '') body.seed = parseInt(seed, 10);
    if (isStrategyMode()) {
      body.strategy = $('strategy').value;
      body.params = readParams('p_', findStrategy($('strategy').value));
    }
    if (isMultiMode()) {
      const plans = currentPlans();
      if (!plans.length) { toast('至少添加一个策略'); return; }
      body.plan = plans;
    }
    try {
      const payload = await API.post('/api/games', body);
      state.slots = new Array(10).fill(null);
      state.advice = null;
      $('history').innerHTML = '';
      renderExplain(null);
      if (isMultiMode()) {
        state.multi = payload;
        renderMulti();
        updateButtons();
        refreshStatus();
        toast(`已开新局：${payload.entries.length} 个策略面对同一条秘密`);
        return;
      }
      await applyState(payload);
      toast('已开新局');
    } catch (e) { toast(e.message, true); }
  }

  async function step() {
    if (isMultiMode()) { await stepMulti(null); return; }
    if (!state.game) return;
    const body = {};
    if (isManualMode()) {
      if (state.slots.some(s => s === null)) { toast('请先填满 10 个槽位'); return; }
      body.guess = state.slots;
    }
    try {
      const res = await API.post(`/api/games/${state.game.gameId}/step`, body);
      const last = res.state.history[res.state.history.length - 1];
      state.advice = null;
      if (last && last.decision && isStrategyMode()) {
        showDecision(last.decision,
          `策略 ${res.state.strategy} 第 ${last.index} 轮（${last.letters}）的逐位理由`);
      } else {
        renderExplain(null);
      }
      await applyState(res.state, last ? last.feedback : null);
      if (res.state.solved) toast('解出！共 ' + res.state.rounds + ' 轮');
    } catch (e) { toast(e.message, true); }
  }

  async function stepMulti(only) {
    if (!state.multi) return;
    try {
      const body = (only === null || only === undefined) ? {} : { only };
      const res = await API.post(`/api/games/${state.multi.gameId}/step`, body);
      state.multi = res.state;
      renderMulti();
      updateButtons();
      refreshStatus();
    } catch (e) { toast(e.message, true); }
  }

  async function play() {
    if (isMultiMode()) {
      if (!state.multi) return;
      try {
        $('btnPlay').disabled = true;
        const res = await API.post(`/api/games/${state.multi.gameId}/play`, { reveal: $('reveal').checked });
        state.multi = res.state;
        renderMulti();
        toast(res.trace.map(t => `${t.index}：${t.solved ? `${t.rounds} 轮解出` : '未解出'}`).join('　'));
      } catch (e) { toast(e.message, true); }
      finally { updateButtons(); }
      return;
    }
    if (!state.game) return;
    try {
      $('btnPlay').disabled = true;
      const res = await API.post(`/api/games/${state.game.gameId}/play`, { reveal: $('reveal').checked });
      await applyState(res.state);
      toast(res.state.solved ? `解出！共 ${res.state.rounds} 轮` : `达到轮次上限仍未解出（${res.state.rounds} 轮）`,
        !res.state.solved);
    } catch (e) { toast(e.message, true); }
    finally { updateButtons(); }
  }

  /** 把当前（手动）历史交给某个策略，让它预测下一步并解释 */
  async function advise() {
    if (!state.game) return;
    const key = $('adviseStrategy').value;
    try {
      const res = await API.post('/api/advise', {
        strategy: key,
        params: readParams('a_', findStrategy(key)),
        history: state.game.history || [],
        explain: true,
      });
      state.advice = res;
      state.slots = res.guess.map(c => c.slice());
      renderSlots();
      renderPalette();
      renderExplain(res.decision,
        `${res.strategyName}（${key}）基于当前 ${res.rounds} 轮历史给出的下一步；你仍可手动修改后再提交`);
      refreshStatus();
      toast(`${res.strategyName} 已给出下一步预测`);
    } catch (e) { toast(e.message, true); }
  }

  async function reveal() {
    if (!state.game) return;
    try {
      const payload = await API.get(`/api/games/${state.game.gameId}?reveal=1`);
      state.game = payload;
      renderSecret();
    } catch (e) { /* 忽略 */ }
  }

  // ------------------------------------------------------------ 初始化
  async function boot() {
    try {
      state.meta = await API.get('/api/meta');
      const res = await API.get('/api/strategies');
      state.strategies = res.strategies;

      fillStrategySelect($('strategy'), 'two_phase');
      $('strategy').addEventListener('change', renderParamControls);
      renderParamControls();

      fillStrategySelect($('adviseStrategy'), 'two_phase');
      $('adviseStrategy').addEventListener('change', renderAdviseParams);
      renderAdviseParams();

      fillStrategySelect($('planAddKey'), 'two_phase');

      // 默认对比三个代表策略：两阶段 / 逐位置贪心 / 朴素基线
      state.plans = [];
      ['two_phase', 'position_entropy', 'naive_position'].forEach(k => addPlan(k, {}));

      $('maxRounds').value = state.meta.defaults.maxRounds;
      renderPalette();
      renderSlots();
      updateButtons();
    } catch (e) { toast(e.message, true); }
  }

  document.addEventListener('DOMContentLoaded', () => {
    boot();
    $('mode').addEventListener('change', () => { updateButtons(); refreshStatus(); });
    $('reveal').addEventListener('change', reveal);
    $('btnNew').addEventListener('click', newGame);
    $('btnStep').addEventListener('click', step);
    $('btnPlay').addEventListener('click', play);
    $('btnAdvise').addEventListener('click', advise);
    $('btnPlanAdd').addEventListener('click', () => addPlan($('planAddKey').value, {}));
    $('btnClear').addEventListener('click', () => {
      state.slots = new Array(10).fill(null);
      state.advice = null;
      renderSlots();
      renderPalette();
    });
    document.addEventListener('keydown', ev => {
      if (ev.target.tagName === 'INPUT' || ev.target.tagName === 'SELECT') return;
      if (ev.key === 'Enter') { ev.preventDefault(); step(); }
    });
  });
})();
