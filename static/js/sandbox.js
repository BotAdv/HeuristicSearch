/* 沙盒页：手动试玩 + 策略自动对局 */
(() => {
  const state = { meta: null, strategies: [], game: null, slots: new Array(10).fill(null) };

  const $ = id => document.getElementById(id);
  const statusBox = () => $('status');

  function currentParams() {
    const sel = $('strategy');
    const opt = state.strategies.find(s => s.key === sel.value);
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
      label.style.marginRight = '10px';
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
        input.style.width = '80px';
      }
      input.id = `p_${spec.name}`;
      input.title = spec.help || '';
      label.appendChild(input);
      host.appendChild(label);
    });
  }

  function renderPalette() {
    $('paletteCount').textContent = state.meta.candidateCount;
    // 逐轮累积每个组合的反馈标记（手动 / 策略自动都一样）
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
    if (isStrategyMode()) return;
    const idx = state.slots.findIndex(s => s === null);
    if (idx < 0) { toast('10 个槽位已填满，请先清空或删除某个槽位'); return; }
    state.slots[idx] = combo;
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
        if (isStrategyMode()) return;
        state.slots[i] = null;
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
    state.game.history.forEach(r => renderRound(host, r.index, r.guess, r.feedback));
    $('roundBadge').textContent = `${state.game.rounds} 轮`;
    host.scrollTop = host.scrollHeight;
  }

  function renderSecret() {
    const box = $('secretBox');
    const show = $('reveal').checked;
    if (show && state.game && state.game.secret) {
      box.hidden = false;
      const text = state.game.secret.map(comboLabel).join(' ');
      box.innerHTML = `<b>秘密序列：</b>${text}`;
    } else {
      box.hidden = true;
    }
  }

  function isStrategyMode() { return $('mode').value === 'strategy'; }

  function updateButtons() {
    const has = !!state.game;
    $('btnStep').disabled = !has || state.game.solved || state.game.exhausted;
    $('btnPlay').disabled = !has || state.game.solved || state.game.exhausted || !isStrategyMode();
    $('btnClear').disabled = !has || isStrategyMode();
    $('strategyGroup').hidden = !isStrategyMode();
    $('palette').style.opacity = isStrategyMode() ? 0.45 : 1;
    $('btnStep').textContent = isStrategyMode() ? '走一步' : '提交猜测';
  }

  function refreshStatus() {
    if (!state.game) { setStatus(statusBox(), '点击「开新局」开始。'); return; }
    const g = state.game;
    const parts = [`对局 ${g.gameId}`, `模式 ${g.mode === 'strategy' ? '策略自动' : '手动'}`];
    if (g.strategy) parts.push(`策略 ${g.strategy}`);
    parts.push(`已用 ${g.rounds} 轮 / 上限 ${g.maxRounds}`);
    if (g.solved) parts.push('✅ 已解出');
    else if (g.exhausted) parts.push('⚠️ 达到轮次上限仍未解出');
    setStatus(statusBox(), parts.join(' · '), g.solved ? 'ok' : '');
  }

  async function applyState(payload, lastFeedback) {
    state.game = payload;
    if (payload.history && payload.history.length) {
      const last = payload.history[payload.history.length - 1];
      state.slots = last.guess.slice();
    }
    renderSlots(lastFeedback);
    renderPalette();      // 每轮结束后刷新候选矩阵的颜色标记
    renderHistory();
    renderSecret();
    updateButtons();
    refreshStatus();
  }

  async function newGame() {
    const body = {
      mode: $('mode').value,
      maxRounds: parseInt($('maxRounds').value, 10) || 40,
      reveal: $('reveal').checked,
    };
    const seed = $('seed').value;
    if (seed !== '') body.seed = parseInt(seed, 10);
    if (isStrategyMode()) {
      body.strategy = $('strategy').value;
      body.params = currentParams();
    }
    try {
      const payload = await API.post('/api/games', body);
      state.slots = new Array(10).fill(null);
      $('history').innerHTML = '';
      await applyState(payload);
      toast('已开新局');
    } catch (e) { toast(e.message, true); }
  }

  async function step() {
    if (!state.game) return;
    const body = {};
    if (!isStrategyMode()) {
      if (state.slots.some(s => s === null)) { toast('请先填满 10 个槽位'); return; }
      body.guess = state.slots;
    }
    try {
      const res = await API.post(`/api/games/${state.game.gameId}/step`, body);
      const last = res.state.history[res.state.history.length - 1];
      await applyState(res.state, last ? last.feedback : null);
      if (res.state.solved) toast('解出！共 ' + res.state.rounds + ' 轮');
    } catch (e) { toast(e.message, true); }
  }

  async function play() {
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

  async function reveal() {
    if (!state.game) return;
    try {
      const payload = await API.get(`/api/games/${state.game.gameId}?reveal=1`);
      state.game = payload;
      renderSecret();
    } catch (e) { /* 忽略 */ }
  }

  async function boot() {
    try {
      state.meta = await API.get('/api/meta');
      const res = await API.get('/api/strategies');
      state.strategies = res.strategies;
      const sel = $('strategy');
      state.strategies.forEach(s => {
        const o = document.createElement('option');
        o.value = s.key;
        o.textContent = s.name;
        sel.appendChild(o);
      });
      sel.value = 'adaptive_hybrid';
      sel.addEventListener('change', renderParamControls);
      renderParamControls();
      $('maxRounds').value = state.meta.defaults.maxRounds;
      renderPalette();
      renderSlots();
      updateButtons();
    } catch (e) { toast(e.message, true); }
  }

  document.addEventListener('DOMContentLoaded', () => {
    boot();
    $('mode').addEventListener('change', updateButtons);
    $('reveal').addEventListener('change', reveal);
    $('btnNew').addEventListener('click', newGame);
    $('btnStep').addEventListener('click', step);
    $('btnPlay').addEventListener('click', play);
    $('btnClear').addEventListener('click', () => {
      state.slots = new Array(10).fill(null);
      renderSlots();
      renderPalette();
    });
    document.addEventListener('keydown', ev => {
      if (ev.key === 'Enter') { ev.preventDefault(); step(); }
    });
  });
})();
