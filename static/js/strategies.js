/* 策略库页：展示所有已注册策略与参数，并支持一键快速试玩 */
(() => {
  const state = { strategies: [] };
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

  function renderTrace(payload) {
    const panel = $('tracePanel');
    const host = $('trace');
    host.innerHTML = '';
    panel.hidden = false;
    const header = document.createElement('div');
    header.className = 'row';
    header.innerHTML = `<span class="muted">策略 <b>${payload.strategy}</b> · 秘密 <b>${payload.secret.map(comboLabel).join(' ')}</b>`
      + ` · ${payload.solved ? '解出' : '未解出'} · ${payload.rounds} 轮 · ${payload.seconds.toFixed(3)} 秒</span>`;
    host.appendChild(header);
    (payload.trace || []).forEach((t, i) => renderRound(host, i + 1, t.guess, t.feedback));
  }

  async function quickPlay(strategyKey, prefix, paramsOverride) {
    const strategy = state.strategies.find(s => s.key === strategyKey);
    const params = paramsOverride || collect(strategy, prefix);
    const body = {
      strategy: strategyKey,
      params,
      maxRounds: parseInt($('qpRounds').value, 10) || 40,
      secretMode: $('qpSecretMode').value,
      tag: 'strategies-page',
      logRounds: true,
    };
    setStatus($('qpResult'), `正在运行 ${strategy.name} …`);
    try {
      const res = await API.post('/api/quickplay', body);
      setStatus($('qpResult'),
        `${strategy.name}：${res.solved ? '解出' : '未解出'}，${res.rounds} 轮，用时 ${res.seconds.toFixed(3)} 秒` +
        (res.error ? `（异常：${res.error}）` : ''),
        res.solved ? 'ok' : '');
      renderTrace(res);
    } catch (e) {
      setStatus($('qpResult'), e.message, 'error');
    }
  }

  function renderCards() {
    const host = $('cards');
    host.innerHTML = '';
    state.strategies.forEach(strategy => {
      const card = document.createElement('div');
      card.className = 'card';

      const head = document.createElement('div');
      head.className = 'row';
      head.innerHTML = `<h3>${strategy.name}</h3><span class="key">${strategy.key}</span>`;
      card.appendChild(head);

      const tags = document.createElement('div');
      tags.className = 'tags';
      strategy.tags.forEach(t => {
        const s = document.createElement('span');
        s.className = 'tag';
        s.textContent = t;
        tags.appendChild(s);
      });
      card.appendChild(tags);

      const desc = document.createElement('p');
      desc.textContent = strategy.description;
      card.appendChild(desc);

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
      card.appendChild(params);

      const row = document.createElement('div');
      row.className = 'row';
      const info = document.createElement('span');
      info.className = 'muted';
      info.textContent = `建议轮次上限 ${strategy.maxRoundsHint} · ${strategy.source}`;
      const btn = document.createElement('button');
      btn.className = 'primary small';
      btn.textContent = '快速试玩';
      btn.addEventListener('click', () => quickPlay(strategy.key, `c_${strategy.key}`));
      row.appendChild(info);
      row.appendChild(btn);
      card.appendChild(row);
      host.appendChild(card);
    });
  }

  document.addEventListener('DOMContentLoaded', async () => {
    try {
      const meta = await API.get('/api/meta');
      const res = await API.get('/api/strategies');
      state.strategies = res.strategies;
      $('qpRounds').value = meta.defaults.maxRounds;
      const sel = $('qpStrategy');
      state.strategies.forEach(s => {
        const o = document.createElement('option');
        o.value = s.key; o.textContent = s.name;
        sel.appendChild(o);
      });
      sel.value = 'adaptive_hybrid';
      renderCards();
      $('qpRun').addEventListener('click', () => quickPlay(sel.value, '__none__', {}));
      $('qpRun').title = '使用策略默认参数跑一局';
    } catch (e) { toast(e.message, true); }
  });
})();
