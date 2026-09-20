/* 策略库页：展示所有已注册策略与参数。
 *
 * 交互约定（1.8.0）
 * ----------------
 * 1. 每张卡片自带「快速试玩」：用它**自己面板上的参数**跑一局，
 *    对局轨迹展开在**该卡片右侧**（卡片跨两列，其它卡片自动顺延排版）。
 * 2. 顶部「跑一局」= 所有卡片各跑一局并展开轨迹；「隐藏轨迹」= 全部收起。
 * 3. 顶部不再有「试玩策略」下拉框 —— 想只试某个策略就点它卡片上的按钮。
 */
(() => {
  const state = { strategies: [], cards: new Map() };
  const $ = id => document.getElementById(id);

  function paramInput(spec, prefix) {
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
    }
    input.id = `${prefix}_${spec.name}`;
    input.title = spec.help || '';
    return input;
  }

  function collect(strategy, prefix) {
    const params = {};
    strategy.params.forEach(spec => {
      const el = document.getElementById(`${prefix}_${spec.name}`);
      if (!el) return;
      if (spec.type === 'bool') params[spec.name] = el.checked;
      else if (spec.type === 'int') params[spec.name] = parseInt(el.value, 10);
      else if (spec.type === 'float') params[spec.name] = parseFloat(el.value);
      else params[spec.name] = el.value;
    });
    return params;
  }

  // -------------------------------------------------------------- 卡片骨架
  function buildCard(strategy) {
    const card = document.createElement('div');
    card.className = 'card';
    card.dataset.key = strategy.key;

    // 左半：原有卡片内容（标题 / 标签 / 说明 / 参数 / 试玩按钮）
    const main = document.createElement('div');
    main.className = 'card-main';
    main.innerHTML = `<div class="row"><h3>${strategy.name}</h3><span class="key">${strategy.key}</span></div>`;

    const tags = document.createElement('div');
    tags.className = 'tags';
    strategy.tags.forEach(t => {
      const s = document.createElement('span');
      s.className = 'tag';
      s.textContent = t;
      tags.appendChild(s);
    });
    main.appendChild(tags);

    const desc = document.createElement('p');
    desc.textContent = strategy.description;
    main.appendChild(desc);

    const params = document.createElement('div');
    params.className = 'params';
    strategy.params.forEach(spec => {
      const row = document.createElement('div');
      row.className = 'param';
      const label = document.createElement('label');
      label.textContent = spec.label;
      row.appendChild(label);
      row.appendChild(paramInput(spec, `c_${strategy.key}`));
      if (spec.help) {
        const help = document.createElement('span');
        help.className = 'help';
        help.textContent = spec.help;
        row.appendChild(help);
      }
      params.appendChild(row);
    });
    main.appendChild(params);

    const actions = document.createElement('div');
    actions.className = 'row';
    const info = document.createElement('span');
    info.className = 'muted';
    info.textContent = `建议轮次上限 ${strategy.maxRoundsHint} · ${strategy.source}`;
    const btn = document.createElement('button');
    btn.className = 'primary small';
    btn.textContent = '快速试玩';
    btn.title = '用本卡片上的参数跑一局，轨迹出现在右侧';
    btn.addEventListener('click', () => {
      runOne(strategy.key).catch(e => setStatus($('qpResult'), e.message, 'error'));
    });
    actions.appendChild(info);
    actions.appendChild(btn);
    main.appendChild(actions);
    card.appendChild(main);

    // 右半：该卡片自己的对局轨迹（默认收起）
    const trace = document.createElement('div');
    trace.className = 'card-trace';
    trace.hidden = true;
    const head = document.createElement('div');
    head.className = 'trace-head';
    const rounds = document.createElement('div');
    rounds.className = 'history';
    trace.appendChild(head);
    trace.appendChild(rounds);
    card.appendChild(trace);

    const entry = { el: card, trace, head, rounds, name: strategy.name };
    state.cards.set(strategy.key, entry);
    return card;
  }

  // -------------------------------------------------------------- 轨迹展开/收起
  function setExpanded(key, expanded) {
    const entry = state.cards.get(key);
    if (!entry) return;
    entry.el.classList.toggle('expanded', expanded);
    entry.trace.hidden = !expanded;
  }

  function collapseAll() {
    state.cards.forEach((_, key) => setExpanded(key, false));
    setStatus($('qpResult'), `已隐藏全部 ${state.cards.size} 条对局轨迹`);
  }

  function renderTrace(key, payload) {
    const entry = state.cards.get(key);
    if (!entry) return;
    const row = state.strategies.find(s => s.key === key);
    const name = (row && row.name) || payload.strategy;
    entry.head.innerHTML = '';
    const info = document.createElement('span');
    info.innerHTML = `对局轨迹 · 策略 <b>${name}</b> · ` +
      `秘密 <b>${payload.secret.map(comboLabel).join(' ')}</b> · ` +
      `<b>${payload.solved ? `✅ ${payload.rounds} 轮解出` : `⚠️ 未解出（${payload.rounds} 轮）`}</b> · ` +
      `${payload.seconds.toFixed(3)} 秒`;
    const hide = document.createElement('button');
    hide.className = 'small';
    hide.textContent = '收起轨迹';
    hide.addEventListener('click', () => {
      setExpanded(key, false);
      setStatus($('qpResult'), `已收起 ${name} 的轨迹`);
    });
    entry.head.appendChild(info);
    entry.head.appendChild(hide);

    entry.rounds.innerHTML = '';
    (payload.trace || []).forEach((t, i) => renderRound(entry.rounds, i + 1, t.guess, t.feedback));
    if (!(payload.trace || []).length) {
      const empty = document.createElement('div');
      empty.className = 'trace-empty';
      empty.textContent = '这一局没有产生轮次记录（可能第一轮就解出或未开始）。';
      entry.rounds.appendChild(empty);
    }
    entry.summary = `${name}：` +
      (payload.solved ? `${payload.rounds} 轮解出` : `未解出（${payload.rounds} 轮）`);
  }

  // -------------------------------------------------------------- 跑一局
  /** 单张卡片试玩：用该卡片自己的参数 */
  async function runOne(strategyKey, quiet) {
    const strategy = state.strategies.find(s => s.key === strategyKey);
    if (!strategy) return null;
    const body = {
      strategy: strategyKey,
      params: collect(strategy, `c_${strategyKey}`),
      maxRounds: parseInt($('qpRounds').value, 10) || 40,
      secretMode: $('qpSecretMode').value,
      tag: 'strategies-page',
      logRounds: true,
    };
    if (!quiet) setStatus($('qpResult'), `正在运行 ${strategy.name} …`);
    const res = await API.post('/api/quickplay', body);
    renderTrace(strategyKey, res);
    setExpanded(strategyKey, true);
    if (!quiet) {
      setStatus($('qpResult'),
        `${strategy.name}：${res.solved ? '解出' : '未解出'}，${res.rounds} 轮，用时 ${res.seconds.toFixed(3)} 秒` +
        (res.error ? `（异常：${res.error}）` : ''),
        res.solved ? 'ok' : '');
    }
    return res;
  }

  /** 顶部「跑一局」：所有卡片各跑一局并展开轨迹 */
  async function runAll() {
    const btn = $('qpRun');
    btn.disabled = true;
    const done = [];
    try {
      for (const strategy of state.strategies) {
        setStatus($('qpResult'),
          `正在运行 ${strategy.name} …（已完成 ${done.length}/${state.strategies.length}）`);
        try {
          const res = await runOne(strategy.key, true);
          if (res) done.push({ name: strategy.name, rounds: res.rounds, solved: res.solved });
        } catch (e) {
          done.push({ name: strategy.name, rounds: NaN, solved: false, error: e.message });
        }
      }
      const lines = done.map(d => (Number.isNaN(d.rounds)
        ? `${d.name}：失败（${d.error}）`
        : `${d.name}：${d.rounds} 轮${d.solved ? ' ✅' : ' ⚠️'}`));
      setStatus($('qpResult'),
        `已跑完 ${done.length} 个策略（轨迹已展开在各自卡片右侧）｜ ` + lines.join(' · '),
        done.every(d => d.solved) ? 'ok' : '');
    } finally {
      btn.disabled = false;
    }
  }

  // -------------------------------------------------------------- 初始化
  function renderCards() {
    const host = $('cards');
    host.innerHTML = '';
    state.cards.clear();
    state.strategies.forEach(strategy => host.appendChild(buildCard(strategy)));
  }

  document.addEventListener('DOMContentLoaded', async () => {
    try {
      const meta = await API.get('/api/meta');
      const res = await API.get('/api/strategies');
      state.strategies = res.strategies;
      $('qpRounds').value = meta.defaults.maxRounds;
      renderCards();
      $('qpRun').addEventListener('click', () => runAll());
      $('qpHideTraces').addEventListener('click', () => collapseAll());
      setStatus($('qpResult'),
        `共 ${state.strategies.length} 个策略。点「跑一局」让所有卡片用各自参数各跑一局` +
        '（轨迹展开在卡片右侧）；也可以只点某张卡片上的「快速试玩」。');
    } catch (e) { toast(e.message, true); }
  });
})();
