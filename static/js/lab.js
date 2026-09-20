/* 模拟实验台：配置 -> 后台任务 -> 指标 / 图表 / 导出 */
(() => {
  const state = {
    strategies: [],
    rows: [],          // 计划行：{rid, key, enabled, _params}
    presets: [],
    jobId: null,
    timer: null,
    report: null,
  };
  const $ = id => document.getElementById(id);
  let ridSeq = 0;

  const strategyByKey = key => state.strategies.find(s => s.key === key);

  function paramInput(spec, rid) {
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
      input.style.width = '90px';
    }
    input.id = `p_${rid}_${spec.name}`;
    input.title = spec.help || '';
    return input;
  }

  function setParamValue(rid, name, value) {
    const el = document.getElementById(`p_${rid}_${name}`);
    if (!el) return;
    if (el.type === 'checkbox') el.checked = !!value;
    else el.value = value;
  }

  function renderPlan() {
    const host = $('planList');
    host.innerHTML = '';
    state.rows.forEach(row => {
      const strategy = strategyByKey(row.key);
      if (!strategy) return;
      const div = document.createElement('div');
      div.className = 'plan-row';

      const check = document.createElement('input');
      check.type = 'checkbox';
      check.checked = row.enabled;
      check.addEventListener('change', () => { row.enabled = check.checked; });

      const name = document.createElement('div');
      name.className = 'name';
      name.innerHTML = `${strategy.name} <span class="key">${strategy.key}</span>`;

      div.appendChild(check);
      div.appendChild(name);
      strategy.params.forEach(spec => {
        const field = document.createElement('div');
        field.className = 'pfield';
        const label = document.createElement('span');
        label.textContent = spec.label;
        field.appendChild(label);
        field.appendChild(paramInput(spec, row.rid));
        div.appendChild(field);
      });

      const actions = document.createElement('div');
      actions.className = 'row';
      const dup = document.createElement('button');
      dup.className = 'small';
      dup.textContent = '+ 参数变体';
      dup.title = '复制一行，用不同参数再跑一遍同一个策略';
      dup.addEventListener('click', () => {
        const snapshot = collectRow(row);
        const newRow = { rid: ++ridSeq, key: row.key, enabled: true };
        state.rows.push(newRow);
        renderPlan();
        Object.entries(snapshot).forEach(([k, v]) => setParamValue(newRow.rid, k, v));
      });
      const del = document.createElement('button');
      del.className = 'small';
      del.textContent = '移除';
      del.addEventListener('click', () => {
        state.rows = state.rows.filter(r => r.rid !== row.rid);
        renderPlan();
      });
      actions.appendChild(dup);
      actions.appendChild(del);
      div.appendChild(actions);
      host.appendChild(div);
    });
  }

  function collectRow(row) {
    const strategy = strategyByKey(row.key);
    const params = {};
    strategy.params.forEach(spec => {
      const el = document.getElementById(`p_${row.rid}_${spec.name}`);
      if (!el) return;
      if (spec.type === 'bool') params[spec.name] = el.checked;
      else if (spec.type === 'int') params[spec.name] = parseInt(el.value, 10);
      else if (spec.type === 'float') params[spec.name] = parseFloat(el.value);
      else params[spec.name] = el.value;
    });
    return params;
  }

  function collectPlan() {
    return state.rows.filter(r => r.enabled).map(r => ({ key: r.key, params: collectRow(r) }));
  }

  function lookLabel(entry) {
    if (!entry) return '?';
    const s = strategyByKey(entry.key);
    const bits = Object.entries(entry.params || {})
      .filter(([k, v]) => {
        const spec = ((s && s.params) || []).find(p => p.name === k);
        return spec && v !== spec.default;
      })
      .map(([k, v]) => `${k}=${v}`);
    const name = s ? s.name : entry.key;
    const suffix = entry.id && entry.id.includes('#') ? ` #${entry.id.split('#')[1]}` : '';
    return name + suffix + (bits.length ? `（${bits.join(', ')}）` : '');
  }

  function empty(el) { el.innerHTML = '<tbody><tr><td class="muted">暂无数据</td></tr></tbody>'; }

  function renderReport(report) {
    state.report = report;
    $('resultPanel').hidden = false;
    $('chartPanel').hidden = false;
    $('pairPanel').hidden = false;
    const planMap = {};
    (report.meta.plan || []).forEach(p => { planMap[p.id] = p; });

    const entries = Object.entries(report.strategies)
      .sort((a, b) => a[1].aggregate.mean - b[1].aggregate.mean);  // 按平均步数升序，最快的排最前
    const bestMean = Math.min(...entries.map(([, e]) => e.aggregate.mean));
    const rows = entries.map(([id, entry]) => {
      const a = entry.aggregate;
      const best = Math.abs(a.mean - bestMean) < 1e-9 ? ' class="best"' : '';
      return [
        lookLabel(planMap[id]),
        `<span${best}>${fmt(a.mean, 3)}</span>`,
        fmt(a.median, 1),
        fmt(a.p90, 1),
        fmt(a.p99, 1),
        a.max,
        `${a.solved}/${a.games}（${fmt(a.solveRate * 100, 1)}%）`,
        fmt(a.std, 3),
        fmt(a.meanMsPerGame, 2),
        fmt(a.totalSec, 2),
        Object.keys(entry.errors || {}).length ? '⚠️' : '',
      ];
    });
    $('aggTable').innerHTML = tableHtml(
      ['策略', '平均步数', '中位数', 'P90', 'P99', '最坏', '解出/总局数', '标准差', 'ms/局', '总耗时(s)', '异常'],
      rows
    );

    const meta = report.meta;
    $('metaLine').textContent =
      `种子 ${meta.seed} · 每策略 ${meta.games} 局 · 轮次上限 ${meta.maxRounds} · 秘密 ${meta.secretMode} · `
      + `总耗时 ${fmt(meta.elapsedSec, 2)}s`;

    const bars = entries.map(([id, entry]) => ({
      label: lookLabel(planMap[id]),
      value: entry.aggregate.mean,
      marks: {
        median: entry.aggregate.median,
        p90: entry.aggregate.p90,
        p99: entry.aggregate.p99,
        max: entry.aggregate.max,
      },
    }));
    $('barChart').innerHTML = svgBarChart(bars);

    const sel = $('histStrategy');
    sel.innerHTML = '';
    entries.forEach(([id]) => {
      const o = document.createElement('option');
      o.value = id;
      o.textContent = lookLabel(planMap[id]);
      sel.appendChild(o);
    });
    sel.onchange = renderHistogram;
    renderHistogram();

    const pairRows = (report.pairwise || []).map(p => [
      `${lookLabel(planMap[p.a])} <span class="muted">vs</span> ${lookLabel(planMap[p.b])}`,
      p.aWins, p.bWins, p.ties,
      fmt(p.meanDiff, 3),
      fmt(p.pValue, 5),
      p.significant ? '✅ 显著' : '—',
    ]);
    $('pairTable').innerHTML = tableHtml(
      ['对比', '左侧更优局数', '右侧更优局数', '平局', '平均差(a−b)', 'p 值', 'α=0.05'],
      pairRows
    );
    $('btnCsv').href = `/api/jobs/${state.jobId}/export?format=csv`;
    $('btnJson').href = `/api/jobs/${state.jobId}/export?format=json`;
  }

  function renderHistogram() {
    if (!state.report) return;
    const key = $('histStrategy').value;
    const entry = state.report.strategies[key];
    if (!entry) return;
    const items = Object.entries(entry.aggregate.histogram);
    $('histChart').innerHTML = svgHistogram(items);
  }

  function setProgress(done, total, message) {
    const pct = total ? Math.round((done / total) * 100) : 0;
    $('progressBar').style.width = `${pct}%`;
    $('jobHint').textContent = message || `${done}/${total}（${pct}%）`;
  }

  async function poll() {
    if (!state.jobId) return;
    try {
      const data = await API.get(`/api/jobs/${state.jobId}`);
      const job = data.job;
      setProgress(job.progress.done, job.progress.total, job.progress.message);
      if (job.status === 'done') {
        clearInterval(state.timer); state.timer = null;
        const full = await API.get(`/api/jobs/${state.jobId}?result=1`);
        renderReport(full.job.result);
        setStatus($('jobHint'), `完成，用时 ${fmt(job.elapsedSec, 2)} 秒`, 'ok');
        $('btnStart').disabled = false;
        $('btnCancel').disabled = true;
        refreshRuns();
      } else if (job.status === 'error' || job.status === 'cancelled') {
        clearInterval(state.timer); state.timer = null;
        setStatus($('jobHint'), job.error || '已取消', 'error');
        $('btnStart').disabled = false;
        $('btnCancel').disabled = true;
      }
    } catch (e) {
      clearInterval(state.timer); state.timer = null;
      toast(e.message, true);
      $('btnStart').disabled = false;
      $('btnCancel').disabled = true;
    }
  }

  async function start() {
    const plan = collectPlan();
    if (!plan.length) { toast('请至少勾选一个策略'); return; }
    const body = {
      plan,
      games: parseInt($('games').value, 10) || 100,
      seed: parseInt($('seed').value, 10) || 0,
      maxRounds: parseInt($('maxRounds').value, 10) || 60,
      secretMode: $('secretMode').value,
    };
    try {
      $('btnStart').disabled = true;
      $('btnCancel').disabled = false;
      setProgress(0, plan.length * body.games, '排队中…');
      const res = await API.post('/api/simulations', body);
      state.jobId = res.job.id;
      state.timer = setInterval(poll, 400);
      poll();
    } catch (e) {
      toast(e.message, true);
      $('btnStart').disabled = false;
      $('btnCancel').disabled = true;
    }
  }

  async function cancel() {
    if (!state.jobId) return;
    await API.post(`/api/jobs/${state.jobId}/cancel`, {});
    poll();
  }

  /** 用一组 (key, params) 重建计划行 */
  function applyPlan(plan, games, maxRounds, secretMode) {
    state.rows = [];
    ridSeq = 0;
    (plan || []).forEach(entry => {
      state.rows.push({ rid: ++ridSeq, key: entry.key, enabled: true, _params: entry.params || {} });
    });
    renderPlan();
    state.rows.forEach(row => {
      Object.entries(row._params || {}).forEach(([k, v]) => setParamValue(row.rid, k, v));
    });
    if (games) $('games').value = games;
    if (maxRounds) $('maxRounds').value = maxRounds;
    if (secretMode) $('secretMode').value = secretMode;
  }

  async function refreshRuns() {
    try {
      const data = await API.get('/api/runs');
      const rows = data.runs.map(r => {
        const summary = (r.summary || []).map(s => `${s.key}: ${fmt(s.mean, 2)}`).join('　');
        const date = new Date(r.savedAt * 1000).toLocaleString();
        return [r.id, date, r.meta.games, r.meta.maxRounds, r.meta.secretMode, summary];
      });
      $('runsTable').innerHTML = tableHtml(
        ['运行 ID', '时间', '局数', '轮次上限', '秘密模式', '各策略平均步数'], rows);
    } catch (e) { /* 忽略 */ }
  }

  document.addEventListener('DOMContentLoaded', async () => {
    try {
      const [stratRes, presetRes] = await Promise.all([
        API.get('/api/strategies'),
        API.get('/api/experiments/presets'),
      ]);
      state.strategies = stratRes.strategies;
      state.presets = presetRes.presets;

      const sel = $('preset');
      state.presets.forEach(p => {
        const o = document.createElement('option');
        o.value = p.id; o.textContent = p.name;
        sel.appendChild(o);
      });

      applyPlan(state.strategies.filter(s => s.default).map(s => ({ key: s.key })), 200, 40, 'distinct');

      $('loadPreset').addEventListener('click', () => {
        const preset = state.presets.find(p => p.id === sel.value);
        if (!preset) { toast('请选择一个预设'); return; }
        applyPlan(preset.plan, preset.games, preset.maxRounds, preset.secretMode);
        toast(`已载入：${preset.name}`);
      });
      $('resetPlan').addEventListener('click', () => {
        applyPlan(state.strategies.filter(s => s.default).map(s => ({ key: s.key })), 200, 40, 'distinct');
      });
      $('btnStart').addEventListener('click', start);
      $('btnCancel').addEventListener('click', cancel);
      $('btnRefreshRuns').addEventListener('click', refreshRuns);
      refreshRuns();
    } catch (e) { toast(e.message, true); }
  });
})();
