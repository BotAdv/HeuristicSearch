/* 沙盒页：三个子页签（手动试玩 / 策略自动 / 多策略对比）
 *
 * 设计要点
 * --------
 * 1. **每个子页签各有一套控制栏与卡片**（DOM 完全独立），
 *    所以「所有策略各走一步」这类按钮永远和它控制的内容同屏，不会跑到页面顶栏；
 *    切换页签也不会互相清空状态（各自持有自己的对局与槽位）。
 * 2. 卡片由 JS 生成：单策略视图 = 候选矩阵 / 猜测序列 / 对局历史 / 决策解释。
 * 3. **CORRECT 锁定**：某位置已被历史判定 CORRECT，则该位置在沙盒里不可再改
 *    （矩阵点选会跳过它，槽位不可清空），矩阵上对应的组合也会打上 🔒。
 */
(() => {
  const state = { meta: null, strategies: [], active: 'manual', views: {} };

  const $ = sel => document.querySelector(sel);
  const paneEl = name => document.querySelector(`[data-pane="${name}"]`);
  const findStrategy = key => state.strategies.find(s => s.key === key);

  // ------------------------------------------------------------ 参数控件工具
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

  function fillStrategySelect(sel, prefer, keys) {
    const list = keys ? state.strategies.filter(s => keys.includes(s.key)) : state.strategies;
    sel.innerHTML = '';
    list.forEach(s => {
      const o = document.createElement('option');
      o.value = s.key;
      o.textContent = s.name;
      sel.appendChild(o);
    });
    if (prefer && list.some(s => s.key === prefer)) sel.value = prefer;
  }

  // ------------------------------------------------------------ 单策略视图
  const CARD_TEMPLATE = `
    <section class="panel">
      <header>
        <h2>候选组合矩阵 <span class="badge" data-role="paletteCount">45</span></h2>
        <p class="hint">行 = 前一位 a，列 = 后一位 b；<b>a-b 与 b-a 是同一个组合</b>，
          任点一格即选中规范形式 (min,max)。虚线划掉的 20 格 = 被规则删除的 10 个组合。</p>
        <div class="legend">
          <span><i class="dot c"></i>CORRECT 已就位</span>
          <span><i class="dot m"></i>MISPLACED 属于秘密</span>
          <span><i class="dot p"></i>PARTIAL 不属于秘密</span>
          <span><i class="dot w"></i>WRONG 不属于秘密</span>
        </div>
        <p class="hint">颜色只是反馈备忘，不影响点选；<b>🔒 表示该组合已锁定在某个位置</b>。</p>
      </header>
      <div class="palette-wrap" data-role="palette"><div data-role="matrix"></div></div>
    </section>
    <section class="panel">
      <header>
        <h2>猜测序列 <span class="badge" data-role="slotCount">0/10</span></h2>
        <p class="hint" data-role="lockHint">槽位颜色 = 该位的反馈；🔒 的槽位已判定 CORRECT，不可再改。</p>
      </header>
      <div class="slots" data-role="slots"></div>
      <div class="legend">
        <span><i class="dot c"></i>CORRECT</span>
        <span><i class="dot m"></i>MISPLACED</span>
        <span><i class="dot p"></i>PARTIAL</span>
        <span><i class="dot w"></i>WRONG</span>
      </div>
      <div class="secret" data-role="secret" hidden></div>
    </section>
    <section class="panel">
      <header>
        <h2>对局历史 <span class="badge" data-role="roundBadge">0 轮</span></h2>
        <p class="hint">每行是一轮的猜测与逐位反馈；点「理由」可看该轮为什么这么选。</p>
      </header>
      <div class="history" data-role="history"></div>
    </section>
    <section class="panel" data-role="explainPanel" hidden>
      <header>
        <h2>决策解释：这一步为什么这么选</h2>
        <p class="hint" data-role="explainSource">—</p>
      </header>
      <div class="dec-box" data-role="explainBox"></div>
    </section>`;

  class SingleView {
    /** @param name 'manual' | 'strategy' */
    constructor(name) {
      this.name = name;
      this.pane = paneEl(name);
      this.game = null;
      this.slots = new Array(10).fill(null);
      this.advice = null;
      this.$ = role => this.pane.querySelector(`[data-role="${role}"]`);
      this.btn = suffix => this.pane.querySelector(`#${name}-${suffix}`);
      this.input = suffix => this.pane.querySelector(`#${name}-${suffix}`);
      this.$('cards').innerHTML = CARD_TEMPLATE;
      this.status = document.getElementById(`${name}-status`);
      this.bind();
    }

    get isManual() { return this.name === 'manual'; }

    // -------------------------------------------------------------- 状态
    /** 已被历史判定 CORRECT 的位置 → 组合（1-based 位置） */
    lockedMap() {
      const map = new Map();
      ((this.game && this.game.history) || []).forEach(r => {
        (r.feedback || []).forEach((kind, i) => {
          if (kind === 'CORRECT') map.set(i + 1, { combo: r.guess[i], round: r.index });
        });
      });
      return map;
    }

    /** 第一个「没填 且 未锁定」的槽位下标 */
    nextFreeSlot() {
      const locked = this.lockedMap();
      return this.slots.findIndex((s, i) => s === null && !locked.has(i + 1));
    }

    bind() {
      this.btn('btnNew').addEventListener('click', () => this.newGame());
      this.btn('btnStep').addEventListener('click', () => this.step());
      if (this.isManual) {
        // 手动模式专有：清空未锁定槽位 / 把历史交给策略
        this.btn('btnClear').addEventListener('click', () => this.clearSlots());
        this.btn('btnAdvise').addEventListener('click', () => this.advise());
        const sel = this.input('adviseStrategy');
        sel.addEventListener('change', () => this.renderAdviseParams());
        this.renderAdviseParams();
      } else {
        // 策略模式专有：自动跑完
        this.btn('btnPlay').addEventListener('click', () => this.play());
        const sel = this.strategySelect();
        sel.addEventListener('change', () => this.renderParamControls());
        this.renderParamControls();
      }
      this.input('reveal').addEventListener('change', () => this.revealSecret());
    }

    renderParamControls() {
      buildParamControls(document.getElementById('strategy-params'),
        findStrategy(this.strategySelect().value), 'p_', null);
    }

    /** 策略下拉（id 固定为 strategy-key，不套 pane 前缀） */
    strategySelect() {
      return document.getElementById('strategy-key');
    }

    renderAdviseParams() {
      buildParamControls(document.getElementById('manual-adviseParams'),
        findStrategy(this.input('adviseStrategy').value), 'a_', null);
    }

    // -------------------------------------------------------------- 渲染
    renderPalette() {
      const locked = this.lockedMap();
      const lockedCombos = new Set([...locked.values()].map(v => comboKey(v.combo)));
      this.$('paletteCount').textContent = state.meta.candidateCount;
      renderComboMatrix(this.$('matrix'), {
        objects: state.meta.objects,
        removed: removedKeySet(state.meta.removedCombos),
        marks: accumulateMarks((this.game && this.game.history) || []),
        picked: new Set(this.slots.filter(Boolean).map(comboKey)),
        locked: lockedCombos,
        onPick: combo => this.place(combo),
      });
    }

    renderSlots(lastFeedback) {
      const host = this.$('slots');
      const locked = this.lockedMap();
      host.innerHTML = '';
      this.slots.forEach((combo, i) => {
        const info = locked.get(i + 1);
        const div = document.createElement('div');
        div.className = 'slot' + (combo ? ' filled' : '') + (info ? ' locked' : '');
        div.innerHTML = `<span class="idx">${i + 1}</span><span>${combo ? comboLabel(combo) : '—'}</span>` +
          (info ? '<span class="lockicon" title="该位置已判定 CORRECT，已锁定">🔒</span>' : '');
        if (info) div.title = `第 ${info.round} 轮已判定 CORRECT（${comboLabel(info.combo)}），此位置已锁定`;
        div.addEventListener('click', () => this.onSlotClick(i));
        host.appendChild(div);
      });
      const filled = this.slots.filter(Boolean).length;
      this.$('slotCount').textContent = `${filled}/10`;
      this.$('lockHint').textContent = locked.size
        ? `已锁定 ${locked.size} 位（🔒）：这些位置已判定 CORRECT，不能改选其它组合。`
        : '槽位颜色 = 该位的反馈；🔒 的槽位已判定 CORRECT，不可再改。';
      if (lastFeedback) {
        const cells = host.children;
        this.slots.forEach((_, i) => {
          if (cells[i] && lastFeedback[i]) cells[i].classList.add(FEEDBACK_CLASS[lastFeedback[i]]);
        });
      }
    }

    renderHistory() {
      const host = this.$('history');
      host.innerHTML = '';
      if (!this.game) return;
      this.game.history.forEach(r => renderRound(host, r.index, r.guess, r.feedback, {
        decision: r.decision,
        onReason: (decision, index) => this.showDecision(decision,
          `第 ${index} 轮（${r.letters}）的逐位理由`),
      }));
      this.$('roundBadge').textContent = `${this.game.rounds} 轮`;
      host.scrollTop = host.scrollHeight;
    }

    renderSecret() {
      const box = this.$('secret');
      if (!box) return;
      const show = this.input('reveal').checked;
      if (show && this.game && this.game.secret) {
        box.hidden = false;
        box.textContent = '秘密序列：' + this.game.secret.map(comboLabel).join(' ');
      } else {
        box.hidden = true;
      }
    }

    renderExplain(decision, source) {
      const panel = this.$('explainPanel');
      if (!decision) { panel.hidden = true; this.$('explainBox').innerHTML = ''; return; }
      panel.hidden = false;
      this.$('explainSource').textContent = source || '';
      renderDecision(this.$('explainBox'), decision, {
        strategyName: (this.game && this.game.strategy) || '',
      });
    }

    showDecision(decision, source) {
      this.renderExplain(decision, source);
      const panel = this.$('explainPanel');
      if (panel.scrollIntoView) panel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    refreshStatus() {
      if (!this.game) return;
      const g = this.game;
      const parts = [`对局 ${g.gameId}`];
      if (g.strategy) parts.push(`策略 ${g.strategy}`);
      parts.push(`已用 ${g.rounds} 轮 / 上限 ${g.maxRounds}`);
      const locked = this.lockedMap().size;
      if (locked) parts.push(`已锁定 ${locked} 位`);
      if (g.solved) parts.push('✅ 已解出');
      else if (g.exhausted) parts.push('⚠️ 达到轮次上限仍未解出');
      if (this.advice) parts.push(`本轮猜测由 ${this.advice.strategyName || this.advice.strategy} 预测`);
      setStatus(this.status, parts.join(' · '), g.solved ? 'ok' : '');
    }

    updateButtons() {
      const has = !!this.game;
      const locked = this.lockedMap();
      const busy = has && (this.game.solved || this.game.exhausted);
      this.btn('btnStep').disabled = !has || busy;
      const clearBtn = this.btn('btnClear');
      if (clearBtn) clearBtn.disabled = !this.slots.some((s, i) => s && !locked.has(i + 1));
      const playBtn = this.btn('btnPlay');
      if (playBtn) playBtn.disabled = !has || busy;
      const adviseBtn = this.btn('btnAdvise');
      if (adviseBtn) adviseBtn.disabled = !has || busy;
      this.$('palette').style.opacity = this.isManual ? 1 : 0.45;
    }

    // -------------------------------------------------------------- 交互
    onSlotClick(i) {
      if (!this.isManual) return;
      if (this.lockedMap().has(i + 1)) {
        toast(`第 ${i + 1} 位已判定 CORRECT 并锁定，不能改选其它组合（如需改动请先撤销上一轮）`, true);
        return;
      }
      this.slots[i] = null;
      this.advice = null;
      this.renderSlots();
      this.renderPalette();
    }

    place(combo) {
      if (!this.isManual) return;
      const idx = this.nextFreeSlot();
      if (idx < 0) {
        toast('没有可填的位置了（10 个槽位要么已填、要么已锁定）');
        return;
      }
      this.slots[idx] = combo;
      this.advice = null;
      this.renderSlots();
      this.renderPalette();
    }

    clearSlots() {
      const locked = this.lockedMap();
      const before = this.slots.filter(Boolean).length;
      this.slots = this.slots.map((s, i) => (locked.has(i + 1) ? s : null));
      const after = this.slots.filter(Boolean).length;
      this.advice = null;
      this.renderSlots();
      this.renderPalette();
      if (locked.size) toast(`已清空 ${before - after} 个未锁定槽位（保留 ${locked.size} 个锁定位置）`);
    }

    // -------------------------------------------------------------- 动作
    async newGame() {
      const body = {
        mode: this.isManual ? 'manual' : 'strategy',
        maxRounds: parseInt(this.input('maxRounds').value, 10) || 40,
        reveal: this.input('reveal').checked,
      };
      const seed = this.input('seed').value;
      if (seed !== '') body.seed = parseInt(seed, 10);
      if (!this.isManual) {
        body.strategy = this.strategySelect().value;
        body.params = readParams('p_', findStrategy(body.strategy));
      }
      try {
        const payload = await API.post('/api/games', body);
        this.slots = new Array(10).fill(null);
        this.advice = null;
        this.$('history').innerHTML = '';
        this.renderExplain(null);
        this.apply(payload);
        toast('已开新局');
      } catch (e) { toast(e.message, true); }
    }

    async step() {
      if (!this.game) return;
      const body = {};
      if (this.isManual) {
        if (this.slots.some(s => s === null)) { toast('请先填满 10 个槽位'); return; }
        body.guess = this.slots;
      }
      try {
        const res = await API.post(`/api/games/${this.game.gameId}/step`, body);
        const last = res.state.history[res.state.history.length - 1];
        this.advice = null;
        if (!this.isManual && last && last.decision) {
          this.apply(res.state, last.feedback);
          this.showDecision(last.decision, `第 ${last.index} 轮（${last.letters}）的逐位理由`);
        } else {
          this.renderExplain(null);
          this.apply(res.state, last ? last.feedback : null);
        }
        if (res.state.solved) toast(`解出！共 ${res.state.rounds} 轮`);
      } catch (e) { toast(e.message, true); }
    }

    async play() {
      if (!this.game) return;
      try {
        this.btn('btnPlay').disabled = true;
        const res = await API.post(`/api/games/${this.game.gameId}/play`, {
          reveal: this.input('reveal').checked,
        });
        this.apply(res.state);
        const last = res.state.history[res.state.history.length - 1];
        if (last && last.decision) {
          this.showDecision(last.decision, `最后一轮（第 ${last.index} 轮）的逐位理由`);
        }
        toast(res.state.solved ? `解出！共 ${res.state.rounds} 轮`
          : `达到轮次上限仍未解出（${res.state.rounds} 轮）`, !res.state.solved);
      } catch (e) { toast(e.message, true); }
      finally { this.updateButtons(); }
    }

    async advise() {
      if (!this.game) return;
      const key = this.input('adviseStrategy').value;
      try {
        const res = await API.post('/api/advise', {
          strategy: key,
          params: readParams('a_', findStrategy(key)),
          history: this.game.history || [],
          explain: true,
        });
        this.advice = res;
        const locked = this.lockedMap();
        // 锁定位置保持不动，其余按策略建议填；若建议与锁定不符，只提示不覆盖
        const guess = res.guess.map(c => c.slice());
        const conflicts = [];
        locked.forEach((info, pos) => {
          if (comboKey(guess[pos - 1]) !== comboKey(info.combo)) {
            conflicts.push(pos);
            guess[pos - 1] = info.combo.slice();
          }
        });
        this.slots = guess;
        this.renderSlots();
        this.renderPalette();
        this.renderExplain(res.decision,
          `${res.strategyName}（${key}）基于当前 ${res.rounds} 轮历史给出的下一步；你仍可手动修改后再提交`);
        this.refreshStatus();
        toast(conflicts.length
          ? `${res.strategyName} 已给出下一步（第 ${conflicts.join('、')} 位保持已锁定组合）`
          : `${res.strategyName} 已给出下一步预测`);
      } catch (e) { toast(e.message, true); }
    }

    async revealSecret() {
      if (!this.game) return;
      try {
        const payload = await API.get(`/api/games/${this.game.gameId}?reveal=1`);
        this.game = payload;
        this.renderSecret();
      } catch (e) { /* 忽略 */ }
    }

    apply(payload, lastFeedback) {
      this.game = payload;
      if (payload.history && payload.history.length) {
        const last = payload.history[payload.history.length - 1];
        // 锁定位置沿用其组合，其余用本轮猜测回填
        const locked = this.lockedMap();
        this.slots = last.guess.slice();
        locked.forEach((info, pos) => { this.slots[pos - 1] = info.combo.slice(); });
      }
      this.renderSlots(lastFeedback);
      this.renderPalette();
      this.renderHistory();
      this.renderSecret();
      this.updateButtons();
      this.refreshStatus();
    }
  }

  // ------------------------------------------------------------ 多策略视图
  class MultiView {
    constructor() {
      this.pane = paneEl('multi');
      this.plans = [];
      this.multi = null;
      this.$ = role => this.pane.querySelector(`[data-role="${role}"]`);
      this.buildPlanUI();
      this.bind();
    }

    buildPlanUI() {
      fillStrategySelect(document.getElementById('multi-planAddKey'), 'two_phase');
      ['two_phase', 'position_entropy', 'naive_position'].forEach(k => this.addPlan(k));
    }

    maxPlans() { return 6; }

    addPlan(key) {
      if (this.plans.length >= this.maxPlans()) { toast(`最多同时对比 ${this.maxPlans()} 个策略`); return; }
      this.plans.push({ key: key || 'two_phase', params: {} });
      this.renderPlans();
    }

    renderPlans() {
      const host = document.getElementById('multi-planList');
      host.innerHTML = '';
      document.getElementById('multi-planCount').textContent = this.plans.length;
      this.plans.forEach((plan, idx) => {
        const row = document.createElement('div');
        row.className = 'plan-row';
        const name = document.createElement('div');
        name.className = 'name';
        name.textContent = `#${idx + 1}`;
        row.appendChild(name);
        const sel = document.createElement('select');
        fillStrategySelect(sel, plan.key);
        sel.addEventListener('change', () => {
          this.plans[idx] = { key: sel.value, params: {} };
          this.renderPlans();
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
          this.plans.splice(idx, 1);
          this.renderPlans();
        });
        row.appendChild(del);
        host.appendChild(row);
      });
    }

    currentPlans() {
      return this.plans.map((plan, idx) => ({
        key: plan.key,
        params: readParams(`mp_${idx}_`, findStrategy(plan.key)),
      }));
    }

    bind() {
      document.getElementById('multi-btnNew').addEventListener('click', () => this.newGame());
      document.getElementById('multi-btnStep').addEventListener('click', () => this.step(null));
      document.getElementById('multi-btnPlay').addEventListener('click', () => this.play());
      document.getElementById('multi-btnPlanAdd').addEventListener('click',
        () => this.addPlan(document.getElementById('multi-planAddKey').value));
      document.getElementById('multi-reveal').addEventListener('change', () => this.revealSecret());
    }

    // -------------------------------------------------------------- 渲染
    renderStatus() {
      const box = document.getElementById('multi-status');
      if (!this.multi) return;
      const st = this.multi;
      const detail = st.entries.map(e => `${e.strategy}：${e.rounds} 轮${e.solved ? ' ✅' : ''}`).join(' · ');
      setStatus(box, `对局 ${st.gameId} · 共 ${st.entries.length} 个策略 | ${detail}`, st.solved ? 'ok' : '');
    }

    updateButtons() {
      const st = this.multi;
      document.getElementById('multi-btnStep').disabled = !st || st.finished;
      document.getElementById('multi-btnPlay').disabled = !st || st.finished;
    }

    /** 从历史里取“已 CORRECT”的位置 */
    static lockedOf(entry) {
      const map = new Map();
      (entry.history || []).forEach(r => {
        (r.feedback || []).forEach((kind, i) => {
          if (kind === 'CORRECT') map.set(i + 1, r.guess[i]);
        });
      });
      return map;
    }

    render() {
      const grid = this.$('cards');
      grid.innerHTML = '';
      const st = this.multi;
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
        stepBtn.disabled = !!entry.solved || st.finished;
        stepBtn.addEventListener('click', () => this.step(idx));
        actions.appendChild(stepBtn);
        card.appendChild(actions);

        const last = (entry.history || [])[entry.history.length - 1];
        const locked = MultiView.lockedOf(entry);
        const slots = document.createElement('div');
        slots.className = 'slots slots-compact';
        const guess = last ? last.guess : new Array(10).fill(null);
        guess.forEach((combo, i) => {
          const isLocked = locked.has(i + 1);
          const div = document.createElement('div');
          div.className = 'slot filled' + (isLocked ? ' locked' : '');
          div.innerHTML = `<span class="idx">${i + 1}</span><span>${combo ? comboLabel(combo) : '—'}</span>` +
            (isLocked ? '<span class="lockicon">🔒</span>' : '');
          if (last && last.feedback && last.feedback[i]) div.classList.add(FEEDBACK_CLASS[last.feedback[i]]);
          div.title = isLocked
            ? `第 ${i + 1} 位已锁定：${comboLabel(combo)}`
            : (last ? `第 ${last.index} 轮：${comboLabel(combo)} → ${LETTER[last.feedback[i]]}` : '');
          slots.appendChild(div);
        });
        card.appendChild(slots);

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
          locked: new Set([...locked.values()].map(comboKey)),
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
          details.addEventListener('toggle', () => { if (details.open) box.scrollIntoView({ block: 'nearest' }); });
          card.appendChild(details);
        }
        grid.appendChild(card);
      });
    }

    // -------------------------------------------------------------- 动作
    async newGame() {
      const plans = this.currentPlans();
      if (!plans.length) { toast('至少添加一个策略'); return; }
      const body = {
        mode: 'multi',
        plan: plans,
        maxRounds: parseInt(document.getElementById('multi-maxRounds').value, 10) || 40,
        reveal: document.getElementById('multi-reveal').checked,
      };
      const seed = document.getElementById('multi-seed').value;
      if (seed !== '') body.seed = parseInt(seed, 10);
      try {
        this.multi = await API.post('/api/games', body);
        this.render();
        this.updateButtons();
        this.renderStatus();
        toast(`已开新局：${this.multi.entries.length} 个策略面对同一条秘密`);
      } catch (e) { toast(e.message, true); }
    }

    async step(only) {
      if (!this.multi) return;
      try {
        const body = (only === null || only === undefined) ? {} : { only };
        const res = await API.post(`/api/games/${this.multi.gameId}/step`, body);
        this.multi = res.state;
        this.render();
        this.updateButtons();
        this.renderStatus();
      } catch (e) { toast(e.message, true); }
    }

    async play() {
      if (!this.multi) return;
      try {
        document.getElementById('multi-btnPlay').disabled = true;
        const res = await API.post(`/api/games/${this.multi.gameId}/play`,
          { reveal: document.getElementById('multi-reveal').checked });
        this.multi = res.state;
        this.render();
        this.renderStatus();
        toast(res.trace.map(t => `${t.index}：${t.solved ? `${t.rounds} 轮解出` : '未解出'}`).join('　'));
      } catch (e) { toast(e.message, true); }
      finally { this.updateButtons(); }
    }

    async revealSecret() {
      if (!this.multi) return;
      try {
        const payload = await API.get(`/api/games/${this.multi.gameId}?reveal=1`);
        this.multi = payload;
        this.render();
      } catch (e) { /* 忽略 */ }
    }
  }

  // ------------------------------------------------------------ 子页签
  function activate(name) {
    state.active = name;
    document.querySelectorAll('#subtabs .subtab').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === name);
    });
    document.querySelectorAll('.tabpane').forEach(pane => {
      pane.hidden = pane.dataset.pane !== name;
    });
    const view = state.views[name];
    if (view && view.updateButtons) view.updateButtons();
    if (view && view.refreshStatus) view.refreshStatus();
    if (view && view.renderStatus) view.renderStatus();
  }

  // ------------------------------------------------------------ 初始化
  async function boot() {
    try {
      state.meta = await API.get('/api/meta');
      state.strategies = (await API.get('/api/strategies')).strategies;

      fillStrategySelect(document.getElementById('strategy-key'), 'two_phase');
      fillStrategySelect(document.getElementById('manual-adviseStrategy'), 'two_phase');

      state.views.manual = new SingleView('manual');
      state.views.strategy = new SingleView('strategy');
      state.views.multi = new MultiView();

      document.getElementById('manual-maxRounds').value = state.meta.defaults.maxRounds;
      document.getElementById('strategy-maxRounds').value = state.meta.defaults.maxRounds;
      document.getElementById('multi-maxRounds').value = state.meta.defaults.maxRounds;

      document.querySelectorAll('#subtabs .subtab').forEach(btn => {
        btn.addEventListener('click', () => activate(btn.dataset.tab));
      });
      activate('manual');

      state.views.manual.renderPalette();
      state.views.manual.renderSlots();
      state.views.manual.updateButtons();
      state.views.strategy.renderPalette();
      state.views.strategy.renderSlots();
      state.views.strategy.updateButtons();
      state.views.multi.updateButtons();
    } catch (e) { toast(e.message, true); }
  }

  document.addEventListener('DOMContentLoaded', () => {
    boot();
    document.addEventListener('keydown', ev => {
      if (ev.target.tagName === 'INPUT' || ev.target.tagName === 'SELECT') return;
      if (ev.key !== 'Enter') return;
      ev.preventDefault();
      const view = state.views[state.active];
      if (view && view.step) view.step(state.active === 'multi' ? null : undefined);
    });
  });
})();
