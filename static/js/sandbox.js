/* 沙盒页：三个子页签（手动试玩 / 策略自动 / 多策略对比）
 *
 * 关键约定
 * --------
 * 1. 每个子页签各有一套卡片与操作按钮，**互不共用 DOM 与状态**；
 *    单策略页签的卡片是「候选组合矩阵」+「历史序列」（上：对局历史 / 下：猜测序列）+ 决策解释，
 *    操作按钮（开新局 / 提交猜测 / 让策略预测 / 清空槽位）放在「历史序列」卡片右上角。
 * 2. 单策略页签的参数栏默认收起，按 F9（或点提示按钮）显示/隐藏。
 * 3. 候选矩阵：只保留上三角（a<=b）可选，下三角与“被删除的 10 个组合”一样置灰；
 *    左键选中、右键取消；本轮选中的格子用自身状态色高亮闪烁（不画蓝色框线）。
 * 4. 猜测序列：默认按顺序依次填入；也可以先点某个槽位指定填入位置；右键槽位清空。
 * 5. 状态保持：当前对局、槽位、选中的位置、策略清单与所有参数都存 localStorage，
 *    刷新后自动恢复（对局本体在服务端会话里）。
 */
(() => {
  const STORE_KEY = 'hs.sandbox.v1';
  const state = { meta: null, strategies: [], active: 'manual', views: {}, toolbars: {} };

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
    if (!host) return;
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

  function fillStrategySelect(sel, prefer) {
    if (!sel) return;
    sel.innerHTML = '';
    state.strategies.forEach(s => {
      const o = document.createElement('option');
      o.value = s.key;
      o.textContent = s.name;
      sel.appendChild(o);
    });
    if (prefer && findStrategy(prefer)) sel.value = prefer;
  }

  // ------------------------------------------------------------ 状态保持
  function saveState() {
    try {
      const snap = { v: 1, active: state.active, toolbars: {}, plans: [], ui: {} };
      Object.keys(state.views).forEach(name => {
        const view = state.views[name];
        if (!view) return;
        snap.toolbars[name] = !!state.toolbars[name];
        if (view.snapshot) snap[name] = view.snapshot();
        if (view.snapshotUI) snap.ui[name] = view.snapshotUI();
      });
      localStorage.setItem(STORE_KEY, JSON.stringify(snap));
    } catch (e) { /* 隐私模式等场景忽略 */ }
  }

  function loadState() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }

  function clearGameInStore(name) {
    const snap = loadState();
    if (snap && snap[name]) {
      snap[name].gameId = null;
      try { localStorage.setItem(STORE_KEY, JSON.stringify(snap)); } catch (e) { /* ignore */ }
    }
  }

  // ------------------------------------------------------------ 单策略视图
  const CARD_TEMPLATE = `
    <section class="panel">
      <header>
        <h2>候选组合矩阵 <span class="badge" data-role="paletteCount">45</span></h2>
        <p class="hint">行 = 前一位 a，列 = 后一位 b。只保留 <b>a ≤ b 的上三角</b>：
          下三角（a &gt; b）与「被删除的 10 个组合」一样置灰不可选，两格语义相同。
          <b>左键</b>选中、<b>右键</b>取消；本轮选中的格子会用自身状态色高亮闪烁。</p>
        <div class="legend">
          <span><i class="dot c"></i>CORRECT 已就位</span>
          <span><i class="dot m"></i>MISPLACED 属于秘密</span>
          <span><i class="dot p"></i>PARTIAL 不属于秘密</span>
          <span><i class="dot w"></i>WRONG 不属于秘密</span>
        </div>
        <p class="hint">颜色只是反馈备忘，不影响点选；<b>🔒</b> 表示该组合已锁定在某个位置。</p>
      </header>
      <div class="palette-wrap" data-role="palette"><div data-role="matrix"></div></div>
    </section>
    <section class="panel seq-card">
      <header>
        <div class="seq-head">
          <h2>历史序列 <span class="badge" data-role="roundBadge">0 轮</span></h2>
          <span class="hint" data-role="lockHint">槽位颜色 = 该位的反馈；🔒 的槽位已判定 CORRECT，不可再改。</span>
        </div>
        <div class="card-actions" data-role="actions"></div>
      </header>
      <div class="seq-block seq-history">
        <div class="seq-title">对局历史（上）<span class="sub-note">每行是一轮的 10 位猜测与反馈；点「理由」看该轮依据</span></div>
        <div class="history" data-role="history"></div>
      </div>
      <div class="seq-block seq-guess">
        <div class="seq-title">猜测序列（下，1 行 10 列）
          <span class="badge" data-role="slotCount">0/10</span>
          <span class="sub-note" data-role="guessHint">左键点矩阵依次填入；先点槽位可指定填入位置；右键（矩阵或槽位）取消</span>
        </div>
        <div class="slots slots-row" data-role="slots"></div>
        <div class="legend">
          <span><i class="dot c"></i>CORRECT</span>
          <span><i class="dot m"></i>MISPLACED</span>
          <span><i class="dot p"></i>PARTIAL</span>
          <span><i class="dot w"></i>WRONG</span>
        </div>
        <div class="secret" data-role="secret" hidden></div>
      </div>
    </section>
    <section class="panel" data-role="explainPanel" hidden>
      <header>
        <h2>决策解释：这一步为什么这么选</h2>
        <p class="hint" data-role="explainSource">—</p>
      </header>
      <div class="dec-box" data-role="explainBox"></div>
    </section>`;

  const ACTIONS = {
    manual: [
      { id: 'btnNew', label: '开新局', primary: true, act: v => v.newGame() },
      { id: 'btnStep', label: '提交猜测', act: v => v.step() },
      { id: 'btnAdvise', label: '让策略预测', act: v => v.advise() },
      { id: 'btnClear', label: '清空槽位', act: v => v.clearSlots() },
    ],
    strategy: [
      { id: 'btnNew', label: '开新局', primary: true, act: v => v.newGame() },
      { id: 'btnStep', label: '走一步', act: v => v.step() },
      { id: 'btnPlay', label: '自动跑完', act: v => v.play() },
    ],
  };

  class SingleView {
    /** @param name 'manual' | 'strategy' */
    constructor(name) {
      this.name = name;
      this.pane = paneEl(name);
      this.game = null;
      this.slots = new Array(10).fill(null);
      this.selectedSlot = null;      // 手动指定的填入位置（0-based）
      this.advice = null;
      this.buttons = {};
      this.$ = role => this.pane.querySelector(`[data-role="${role}"]`);
      this.input = suffix => document.getElementById(`${name}-${suffix}`);
      this.$('cards').innerHTML = CARD_TEMPLATE;
      this.status = document.getElementById(`${name}-status`);
      this.buildActions();
      this.bind();
    }

    get isManual() { return this.name === 'manual'; }

    // -------------------------------------------------------------- 操作按钮
    buildActions() {
      const host = this.$('actions');
      host.innerHTML = '';
      const actions = ACTIONS[this.name] || [];
      actions.forEach(spec => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.id = `${this.name}-${spec.id}`;
        btn.className = spec.primary ? 'primary' : '';
        btn.textContent = spec.label;
        btn.addEventListener('click', () => spec.act(this));
        host.appendChild(btn);
        this.buttons[spec.id] = btn;
      });
    }

    bind() {
      if (this.isManual) {
        const sel = this.input('adviseStrategy');
        sel.addEventListener('change', () => {
          this.renderAdviseParams();
          saveState();
        });
        this.renderAdviseParams();
      } else {
        const sel = document.getElementById('strategy-key');
        sel.addEventListener('change', () => {
          this.renderParamControls();
          saveState();
        });
        this.renderParamControls();
      }
      this.input('reveal').addEventListener('change', () => {
        this.revealSecret();
        saveState();
      });
      ['seed', 'maxRounds'].forEach(k => {
        const el = this.input(k);
        if (el) el.addEventListener('change', saveState);
      });
      this.renderActionState();
    }

    renderParamControls() {
      buildParamControls(document.getElementById('strategy-params'),
        findStrategy(document.getElementById('strategy-key').value), 'p_', null);
    }

    renderAdviseParams() {
      buildParamControls(this.input('adviseParams'),
        findStrategy(this.input('adviseStrategy').value), 'a_', null);
    }

    // -------------------------------------------------------------- 持久化
    snapshot() {
      return {
        gameId: this.game ? this.game.gameId : null,
        slots: this.slots,
        selectedSlot: this.selectedSlot,
      };
    }

    snapshotUI() {
      const ui = {};
      ['seed', 'maxRounds'].forEach(k => {
        const el = this.input(k);
        if (el) ui[k] = el.value;
      });
      const reveal = this.input('reveal');
      if (reveal) ui.reveal = reveal.checked;
      if (this.isManual) {
        const sel = this.input('adviseStrategy');
        if (sel) {
          ui.adviseStrategy = sel.value;
          ui.adviseParams = readParams('a_', findStrategy(sel.value));
        }
      } else {
        const sel = document.getElementById('strategy-key');
        if (sel) {
          ui.strategy = sel.value;
          ui.params = readParams('p_', findStrategy(sel.value));
        }
      }
      return ui;
    }

    restoreUI(ui) {
      if (!ui) return;
      ['seed', 'maxRounds'].forEach(k => {
        const el = this.input(k);
        if (el && ui[k] !== undefined && ui[k] !== '') el.value = ui[k];
      });
      const reveal = this.input('reveal');
      if (reveal && ui.reveal) reveal.checked = true;
      if (this.isManual) {
        if (ui.adviseStrategy) this.input('adviseStrategy').value = ui.adviseStrategy;
        this.renderAdviseParams();
        this.applyParams('a_', findStrategy(this.input('adviseStrategy').value), ui.adviseParams);
      } else {
        const sel = document.getElementById('strategy-key');
        if (ui.strategy) sel.value = ui.strategy;
        this.renderParamControls();
        this.applyParams('p_', findStrategy(sel.value), ui.params);
      }
    }

    applyParams(prefix, opt, params) {
      if (!opt || !params) return;
      opt.params.forEach(spec => {
        const el = document.getElementById(`${prefix}${spec.name}`);
        if (!el || params[spec.name] === undefined) return;
        if (spec.type === 'bool') el.checked = !!params[spec.name];
        else el.value = params[spec.name];
      });
    }

    // -------------------------------------------------------------- 状态
    /** 已被历史判定 CORRECT 的位置 → {combo, round}（1-based 位置） */
    lockedMap() {
      const map = new Map();
      ((this.game && this.game.history) || []).forEach(r => {
        (r.feedback || []).forEach((kind, i) => {
          if (kind === 'CORRECT') map.set(i + 1, { combo: r.guess[i], round: r.index });
        });
      });
      return map;
    }

    firstFreeSlot(exclude) {
      const locked = this.lockedMap();
      return this.slots.findIndex((s, i) => s === null && !locked.has(i + 1) && i !== exclude);
    }

    /** firstFreeSlot() 的“可空”版本：没有空位时返回 null（而不是 -1） */
    nextTarget() {
      const idx = this.firstFreeSlot();
      return idx >= 0 ? idx : null;
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
        triangle: 'upper',
        onPick: combo => this.place(combo),
        onCancel: combo => this.cancelCombo(combo),
      });
    }

    renderSlots(lastFeedback) {
      const host = this.$('slots');
      const locked = this.lockedMap();
      host.innerHTML = '';
      this.slots.forEach((combo, i) => {
        const info = locked.get(i + 1);
        const div = document.createElement('div');
        div.className = 'slot' + (combo ? ' filled' : '') + (info ? ' locked' : '') +
          (this.selectedSlot === i ? ' selected' : '');
        div.innerHTML = `<span class="idx">${i + 1}</span><span>${combo ? comboLabel(combo) : '—'}</span>` +
          (info ? '<span class="lockicon" title="该位置已判定 CORRECT，已锁定">🔒</span>' : '');
        div.title = info
          ? `第 ${info.round} 轮已判定 CORRECT（${comboLabel(info.combo)}），此位置已锁定`
          : '左键：指定此处为填入位置；右键：清空该槽位';
        div.addEventListener('click', () => this.onSlotClick(i));
        div.addEventListener('contextmenu', ev => {
          ev.preventDefault();
          this.onSlotCancel(i);
        });
        host.appendChild(div);
      });
      const filled = this.slots.filter(Boolean).length;
      this.$('slotCount') && (this.$('slotCount').textContent = `${filled}/10`);
      this.$('lockHint').textContent = locked.size
        ? `已锁定 ${locked.size} 位（🔒）：这些位置已判定 CORRECT，不能改选其它组合。`
          + (this.selectedSlot !== null ? ` 下一个填入位置：第 ${this.selectedSlot + 1} 位。` : '')
        : (this.selectedSlot !== null
          ? `下一个填入位置已指定为第 ${this.selectedSlot + 1} 位（再点一次该槽位可取消指定）。`
          : '槽位颜色 = 该位的反馈；点槽位可指定填入位置，右键清空。');
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
        onReason: (decision, index) => this.showDecision(decision, `第 ${index} 轮（${r.letters}）的逐位理由`),
      }));
      const badge = this.$('roundBadge');
      if (badge) badge.textContent = `${this.game.rounds} 轮`;
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

    /** 手动改了猜测序列后，把上一次「让策略预测」的解释标记为已过期 */
    markAdviceStale() {
      if (!this.advice) return;
      this.advice = null;
      const panel = this.$('explainPanel');
      if (panel.hidden) return;
      const src = this.$('explainSource');
      if (!src.textContent.startsWith('【已过期】')) {
        src.textContent = `【已过期】你手动改动过猜测序列，下面的建议已不代表当前序列。` + src.textContent;
      }
      panel.classList.add('stale');
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

    renderActionState() {
      const has = !!this.game;
      const busy = has && (this.game.solved || this.game.exhausted);
      const locked = this.lockedMap();
      const set = (id, disabled) => { if (this.buttons[id]) this.buttons[id].disabled = disabled; };
      set('btnStep', !has || busy);
      set('btnPlay', !has || busy);
      set('btnAdvise', !has || busy);
      set('btnClear', !this.slots.some((s, i) => s && !locked.has(i + 1)));
      this.$('palette').style.opacity = this.isManual ? 1 : 0.55;
    }

    // -------------------------------------------------------------- 交互
    onSlotClick(i) {
      if (this.lockedMap().has(i + 1)) {
        toast(`第 ${i + 1} 位已判定 CORRECT 并锁定，不能改选其它组合（如需改动请先撤销上一轮）`, true);
        return;
      }
      if (!this.isManual) {
        toast('策略自动模式下猜测序列由策略给出，不能手工修改');
        return;
      }
      this.selectedSlot = this.selectedSlot === i ? null : i;
      this.renderSlots();
      saveState();
    }

    onSlotCancel(i) {
      if (this.lockedMap().has(i + 1)) {
        toast(`第 ${i + 1} 位已锁定，不能清空`, true);
        return;
      }
      if (this.slots[i] === null) {
        if (this.selectedSlot === i) this.selectedSlot = null;
        this.renderSlots();
        return;
      }
      this.slots[i] = null;
      this.advice = null;
      this.markAdviceStale();
      if (this.selectedSlot === null) this.selectedSlot = i;
      this.renderSlots();
      this.renderPalette();
      saveState();
    }

    place(combo) {
      if (!this.isManual) return;
      const locked = this.lockedMap();
      let target = this.selectedSlot;
      if (target !== null && (target < 0 || locked.has(target + 1))) target = null;
      if (target === null) target = this.firstFreeSlot();
      if (target < 0) {
        toast('10 个槽位都已填满：先点一个槽位指定要替换的位置，或点「清空槽位」');
        return;
      }
      this.slots[target] = combo;
      this.advice = null;
      this.markAdviceStale();
      // 填完自动挪到下一个空位，方便连续“依次填入”
      this.selectedSlot = this.nextTarget();
      this.renderSlots();
      this.renderPalette();
      saveState();
    }

    cancelCombo(combo) {
      if (!this.isManual) return;
      const locked = this.lockedMap();
      const key = comboKey(combo);
      let cleared = 0;
      this.slots.forEach((s, i) => {
        if (s && comboKey(s) === key && !locked.has(i + 1)) {
          this.slots[i] = null;
          cleared += 1;
          if (this.selectedSlot === null) this.selectedSlot = i;
        }
      });
      if (!cleared) { toast(`${comboLabel(combo)} 不在猜测序列里（或已锁定），无需取消`); return; }
      this.advice = null;
      this.markAdviceStale();
      this.renderSlots();
      this.renderPalette();
      saveState();
      toast(`已取消 ${comboLabel(combo)}（清空 ${cleared} 个位置）`);
    }

    clearSlots() {
      const locked = this.lockedMap();
      const before = this.slots.filter(Boolean).length;
      this.slots = this.slots.map((s, i) => (locked.has(i + 1) ? s : null));
      const after = this.slots.filter(Boolean).length;
      this.advice = null;
      this.markAdviceStale();
      this.selectedSlot = this.nextTarget();
      this.renderSlots();
      this.renderPalette();
      this.renderActionState();
      saveState();
      toast(locked.size
        ? `已清空 ${before - after} 个未锁定槽位（保留 ${locked.size} 个锁定位置）`
        : `已清空 ${before - after} 个槽位`);
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
        body.strategy = document.getElementById('strategy-key').value;
        body.params = readParams('p_', findStrategy(body.strategy));
      }
      try {
        const payload = await API.post('/api/games', body);
        this.slots = new Array(10).fill(null);
        this.selectedSlot = 0;
        this.advice = null;
        this.$('history').innerHTML = '';
        this.renderExplain(null);
        this.apply(payload);
        toast('已开新局');
      } catch (e) { toast(e.message, true); }
    }

    async step() {
      if (!this.game) { toast('先点「开新局」', true); return; }
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
          // 手动模式：保留“让策略预测”的解释面板（它描述的正是刚提交的那一步）
          this.apply(res.state, last ? last.feedback : null);
        }
        if (res.state.solved) toast(`解出！共 ${res.state.rounds} 轮`);
      } catch (e) { toast(e.message, true); }
    }

    async play() {
      if (!this.game) { toast('先点「开新局」', true); return; }
      try {
        if (this.buttons.btnPlay) this.buttons.btnPlay.disabled = true;
        const res = await API.post(`/api/games/${this.game.gameId}/play`, {
          reveal: this.input('reveal').checked,
        });
        this.apply(res.state);
        const last = res.state.history[res.state.history.length - 1];
        if (last && last.decision) this.showDecision(last.decision, `最后一轮（第 ${last.index} 轮）的逐位理由`);
        toast(res.state.solved ? `解出！共 ${res.state.rounds} 轮`
          : `达到轮次上限仍未解出（${res.state.rounds} 轮）`, !res.state.solved);
      } catch (e) { toast(e.message, true); }
      finally { this.renderActionState(); }
    }

    async advise() {
      if (!this.game) { toast('先点「开新局」', true); return; }
      const sel = this.input('adviseStrategy');
      const key = sel ? sel.value : 'two_phase';
      try {
        const res = await API.post('/api/advise', {
          strategy: key,
          params: readParams('a_', findStrategy(key)),
          history: this.game.history || [],
          explain: true,
        });
        this.advice = res;
        this.$('explainPanel').classList.remove('stale');
        const locked = this.lockedMap();
        const guess = res.guess.map(c => c.slice());
        const conflicts = [];
        locked.forEach((info, pos) => {
          if (comboKey(guess[pos - 1]) !== comboKey(info.combo)) {
            conflicts.push(pos);
            guess[pos - 1] = info.combo.slice();
          }
        });
        this.slots = guess;
        this.selectedSlot = this.nextTarget();
        this.renderSlots();
        this.renderPalette();
        this.renderActionState();
        this.renderExplain(res.decision,
          `${res.strategyName}（${key}）基于当前 ${res.rounds} 轮历史给出的下一步；你仍可手动修改后再提交`);
        this.refreshStatus();
        saveState();
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
      const keepSlots = this._restoring ? this.slots.slice() : null;
      this.game = payload;
      if (keepSlots) {
        // 刷新恢复：优先用本地保存的槽位（可能包含还没提交的手工编辑）
        this.slots = keepSlots;
      } else if (payload.history && payload.history.length) {
        const last = payload.history[payload.history.length - 1];
        const locked = this.lockedMap();
        this.slots = last.guess.slice();
        locked.forEach((info, pos) => { this.slots[pos - 1] = info.combo.slice(); });
      }
      if (this.selectedSlot === null || this.selectedSlot < 0 || this.slots[this.selectedSlot]) {
        this.selectedSlot = this.nextTarget();
      }
      this.renderSlots(lastFeedback);
      this.renderPalette();
      this.renderHistory();
      this.renderSecret();
      this.renderActionState();
      this.refreshStatus();
      saveState();
    }

    /** 刷新后恢复：拉取服务端会话并按本地快照回填 */
    async restore(snap) {
      if (!snap || !snap.gameId) return false;
      try {
        const payload = await API.get(`/api/games/${snap.gameId}`);
        this.slots = Array.isArray(snap.slots) ? snap.slots : new Array(10).fill(null);
        this.selectedSlot = typeof snap.selectedSlot === 'number' ? snap.selectedSlot : null;
        this._restoring = true;
        this.apply(payload);
        this._restoring = false;
        return true;
      } catch (e) {
        clearGameInStore(this.name);
        toast('上一局已失效（服务重启或会话过期），已重置', true);
        return false;
      }
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
      const addSel = document.getElementById('multi-planAddKey');
      fillStrategySelect(addSel, 'two_phase');
      ['two_phase', 'position_entropy', 'naive_position'].forEach(k => this.addPlan(k, {}, true));
      this.renderPlans();
    }

    maxPlans() { return 6; }

    addPlan(key, params, silent) {
      if (this.plans.length >= this.maxPlans()) {
        if (!silent) toast(`最多同时对比 ${this.maxPlans()} 个策略`);
        return;
      }
      this.plans.push({ key: key || 'two_phase', params: params || {} });
      if (!silent) this.renderPlans();
    }

    renderPlans() {
      const host = document.getElementById('multi-planList');
      host.innerHTML = '';
      document.getElementById('multi-planCount').textContent = this.plans.length;
      document.getElementById('multi-btnPlanAdd').disabled = this.plans.length >= this.maxPlans();
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
          saveState();
        });
        row.appendChild(sel);
        const params = document.createElement('span');
        params.className = 'params-inline';
        row.appendChild(params);
        buildParamControls(params, findStrategy(plan.key), `mp_${idx}_`, plan.params);
        params.addEventListener('change', saveState);
        const del = document.createElement('button');
        del.className = 'small';
        del.textContent = '移除';
        del.addEventListener('click', () => {
          this.plans.splice(idx, 1);
          this.renderPlans();
          saveState();
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
      document.getElementById('multi-reveal').addEventListener('change', () => {
        this.revealSecret();
        saveState();
      });
      ['multi-seed', 'multi-maxRounds'].forEach(id => {
        document.getElementById(id).addEventListener('change', saveState);
      });
    }

    // -------------------------------------------------------------- 持久化
    snapshot() {
      return { gameId: this.multi ? this.multi.gameId : null };
    }

    snapshotUI() {
      return {
        seed: document.getElementById('multi-seed').value,
        maxRounds: document.getElementById('multi-maxRounds').value,
        reveal: document.getElementById('multi-reveal').checked,
        plans: this.currentPlans(),
      };
    }

    restoreUI(ui) {
      if (!ui) return;
      if (ui.seed !== undefined) document.getElementById('multi-seed').value = ui.seed;
      if (ui.maxRounds) document.getElementById('multi-maxRounds').value = ui.maxRounds;
      if (ui.reveal) document.getElementById('multi-reveal').checked = true;
      if (Array.isArray(ui.plans) && ui.plans.length) {
        this.plans = [];
        ui.plans.forEach(p => this.addPlan(p.key, p.params, true));
        this.renderPlans();
      }
    }

    async restore(snap, ui) {
      if (ui) this.restoreUI(ui);
      if (!snap || !snap.gameId) return false;
      try {
        const payload = await API.get(`/api/games/${snap.gameId}`);
        this.multi = payload;
        this.render();
        this.updateButtons();
        this.renderStatus();
        return true;
      } catch (e) {
        clearGameInStore('multi');
        return false;
      }
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
          triangle: 'upper',
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
        saveState();
        toast(`已开新局：${this.multi.entries.length} 个策略面对同一条秘密`);
      } catch (e) { toast(e.message, true); }
    }

    async step(only) {
      if (!this.multi) { toast('先点「开新局」', true); return; }
      try {
        const body = (only === null || only === undefined) ? {} : { only };
        const res = await API.post(`/api/games/${this.multi.gameId}/step`, body);
        this.multi = res.state;
        this.render();
        this.updateButtons();
        this.renderStatus();
        saveState();
      } catch (e) { toast(e.message, true); }
    }

    async play() {
      if (!this.multi) { toast('先点「开新局」', true); return; }
      try {
        document.getElementById('multi-btnPlay').disabled = true;
        const res = await API.post(`/api/games/${this.multi.gameId}/play`,
          { reveal: document.getElementById('multi-reveal').checked });
        this.multi = res.state;
        this.render();
        this.renderStatus();
        saveState();
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

  // ------------------------------------------------------------ 子页签 / 参数栏
  function activate(name) {
    state.active = name;
    document.querySelectorAll('#subtabs .subtab').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === name);
    });
    document.querySelectorAll('.tabpane').forEach(pane => {
      pane.hidden = pane.dataset.pane !== name;
    });
    const view = state.views[name];
    if (view && view.renderActionState) view.renderActionState();
    if (view && view.updateButtons) view.updateButtons();
    if (view && view.refreshStatus) view.refreshStatus();
    if (view && view.renderStatus) view.renderStatus();
    saveState();
  }

  /** F9：显示/隐藏当前页签的参数栏 */
  function toggleBar(name) {
    const target = name || state.active;
    const pane = paneEl(target);
    if (!pane) return;
    const bar = pane.querySelector('[data-role="toolbar"]');
    const hint = pane.querySelector('[data-role="toggleBar"]');
    if (!bar) return;
    bar.hidden = !bar.hidden;
    state.toolbars[target] = bar.hidden;
    if (hint) hint.textContent = bar.hidden ? '显示参数栏（F9）' : '收起参数栏（F9）';
    saveState();
  }

  function applyBarVisibility(name, hidden) {
    const pane = paneEl(name);
    if (!pane) return;
    const bar = pane.querySelector('[data-role="toolbar"]');
    const hint = pane.querySelector('[data-role="toggleBar"]');
    if (!bar) return;
    bar.hidden = !!hidden;
    state.toolbars[name] = !!hidden;
    if (hint) hint.textContent = hidden ? '显示参数栏（F9）' : '收起参数栏（F9）';
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

      document.querySelectorAll('#subtabs .subtab').forEach(btn => {
        btn.addEventListener('click', () => activate(btn.dataset.tab));
      });
      document.querySelectorAll('[data-role="toggleBar"]').forEach(btn => {
        btn.addEventListener('click', () => toggleBar(btn.closest('.tabpane').dataset.pane));
      });
      document.addEventListener('keydown', ev => {
        if (ev.key === 'F9') {
          ev.preventDefault();
          toggleBar(state.active);
          return;
        }
        if (ev.key === 'Enter' && ev.target.tagName !== 'INPUT' && ev.target.tagName !== 'SELECT') {
          ev.preventDefault();
          const view = state.views[state.active];
          if (view && view.step) view.step();
        }
      });

      // ---- 恢复上次状态（对局在服务端会话里，这里只存 id / 槽位 / 参数）----
      const snap = loadState();
      if (snap) {
        ['manual', 'strategy'].forEach(name => {
          const view = state.views[name];
          view.restoreUI((snap.ui || {})[name]);
          const saved = (snap.toolbars || {})[name];
          // 没存过时：手动模式默认收起（需求），策略模式默认展开（要选策略）
          applyBarVisibility(name, saved === undefined ? name === 'manual' : !!saved);
        });
        if (snap.ui && snap.ui.multi) state.views.multi.restoreUI(snap.ui.multi);
        const restored = [];
        for (const name of ['manual', 'strategy']) {
          const ok = await state.views[name].restore(snap[name]);
          if (ok) restored.push(name);
        }
        if (await state.views.multi.restore(snap.multi, null)) restored.push('multi');
        activate(snap.active || 'manual');
        if (restored.length) toast(`已恢复上次状态：${restored.length} 个页签的对局`);
      } else {
        // 首次访问：默认收起参数栏（按 F9 显示）
        applyBarVisibility('manual', true);
        applyBarVisibility('strategy', false);
        activate('manual');
      }

      state.views.manual.renderPalette();
      state.views.manual.renderSlots();
      state.views.manual.renderActionState();
      state.views.manual.refreshStatus();
      state.views.strategy.renderPalette();
      state.views.strategy.renderSlots();
      state.views.strategy.renderActionState();
      state.views.strategy.refreshStatus();
      state.views.multi.updateButtons();
      state.views.multi.renderStatus();
    } catch (e) { toast(e.message, true); }
  }

  document.addEventListener('DOMContentLoaded', boot);

  // 关页面前再存一次，保证“刷新后数据不变”
  window.addEventListener('beforeunload', saveState);
})();
