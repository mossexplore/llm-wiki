    async function runEval() {
      state.evalRunning = true;
      render();
      try {
        const r = await fetch('/api/eval/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ k: state.evalK })
        });
        if (noBackend(r.status)) { state.evalRunning = false; render(); showToast('后端未连接 · 无法运行评测'); return; }
        const payload = await r.json();
        if (!r.ok) throw new Error(apiErrorMessage(payload, '评测失败'));
        state.evalReport = apiData(payload);
        state.evalRunning = false;
        render();
      } catch (e) {
        state.evalRunning = false;
        render();
        showToast(String(e && e.message || e));
      }
    }

    const EVAL_KIND_LABELS = { exact: '原文粘贴', lexical: '词面重合', semantic: '换种说法' };
    const EVAL_MODE_CLASS = { exact: 'ok', fuzzy: 'warn', none: 'bad' };

    function evalPct(v) {
      return (typeof v === 'number' ? Math.round(v * 100) : 0) + '%';
    }

    function renderEvalToolbar() {
      const rep = state.evalReport;
      const kBtns = [1, 3, 5].map(k =>
        `<button class="${state.evalK === k ? 'on' : ''}" data-eval-k="${k}" type="button" ${state.evalRunning ? 'disabled' : ''}>${k}</button>`
      ).join('');
      const backend = rep ? escapeHtml(rep.backend || 'SQLite') : 'SQLite · FTS5(评测沙箱)';
      return `
        <section class="card">
          <div class="card-head">
            <div><div class="kicker">RETRIEVAL · EVALUATION</div><h3>检索质量评测</h3></div>
            <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
              <span class="muted" style="font-size:12px">top-k</span>
              <div class="seg" aria-label="top-k">${kBtns}</div>
              <span class="badge mono" title="评测在隔离沙箱里进行,不影响生产检索索引">${backend}</span>
              <button class="btn primary" id="runEval" type="button" ${state.evalRunning ? 'disabled' : ''}>${state.evalRunning ? iconSpin() : iconSearch()}运行评测</button>
            </div>
          </div>
          <div class="card-pad">
            <p class="muted" style="margin:0;font-size:12px;line-height:1.6">用固定语料 + 标注查询量化 recall@k / MRR,作为检索优化前后的对比基线。查询分三类:<strong>exact</strong>(原文粘贴 signature)、<strong>lexical</strong>(中文换说法但词面重合)、<strong>semantic</strong>(近义改写、词面不重合,纯词法检索的天然短板)。</p>
          </div>
        </section>`;
    }

    function renderEvalMetrics(rep) {
      const k = rep.k;
      const o = rep.overall;
      const missed = rep.rows.filter(r => !r.hit_at_k).length;
      const cards = [
        { label: 'recall@1', value: evalPct(o['recall@1']) },
        { label: 'recall@' + k, value: evalPct(o['recall@' + k]) },
        { label: 'MRR', value: (typeof o.mrr === 'number' ? o.mrr.toFixed(2) : '—') },
        { label: '未命中 / 总数', value: `${missed} <span class="muted" style="font-size:13px">/ ${o.n}</span>` }
      ];
      const modes = rep.modes || {};
      const modeText = ['exact', 'fuzzy', 'none']
        .filter(m => modes[m] != null)
        .map(m => `${m}=${modes[m]}`).join(' · ');
      return `
        <section class="card">
          <div class="card-head">
            <div><div class="kicker">SUMMARY</div><h3>总体指标</h3></div>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
              <span class="badge mono" title="命中模式分布">${escapeHtml(modeText)}</span>
              ${typeof rep.elapsed_ms === 'number' ? `<span class="badge info mono">${rep.elapsed_ms} ms</span>` : ''}
            </div>
          </div>
          <div class="card-pad">
            <div class="eval-metrics">
              ${cards.map(c => `<div class="eval-metric"><div class="eval-metric-label">${c.label}</div><div class="eval-metric-value">${c.value}</div></div>`).join('')}
            </div>
          </div>
        </section>`;
    }

    function renderEvalByKind(rep) {
      const k = rep.k;
      const rowFor = (key, label, note, highlight) => {
        const a = rep.by_kind[key] || {};
        const cls = highlight ? ' class="eval-krow weak"' : ' class="eval-krow"';
        return `<div${cls}>
          <span>${label}${note ? ` <span class="muted" style="font-size:11px">· ${note}</span>` : ''}</span>
          <span class="mono">${evalPct(a['recall@1'])}</span>
          <span class="mono">${evalPct(a['recall@' + k])}</span>
          <span class="mono">${typeof a.mrr === 'number' ? a.mrr.toFixed(2) : '—'}</span>
        </div>`;
      };
      const o = rep.overall;
      return `
        <section class="card">
          <div class="card-head"><div><div class="kicker">BY QUERY TYPE</div><h3>按查询类型</h3></div></div>
          <div class="card-pad">
            <div class="eval-khead">
              <span>类型</span><span class="mono">recall@1</span><span class="mono">recall@${k}</span><span class="mono">MRR</span>
            </div>
            ${rowFor('exact', 'exact', EVAL_KIND_LABELS.exact, false)}
            ${rowFor('lexical', 'lexical', EVAL_KIND_LABELS.lexical, false)}
            ${rowFor('semantic', 'semantic', EVAL_KIND_LABELS.semantic + ' · 短板', true)}
            <div class="eval-krow eval-krow-total">
              <span>overall</span>
              <span class="mono">${evalPct(o['recall@1'])}</span>
              <span class="mono">${evalPct(o['recall@' + k])}</span>
              <span class="mono">${typeof o.mrr === 'number' ? o.mrr.toFixed(2) : '—'}</span>
            </div>
          </div>
        </section>`;
    }

    function renderEvalRows(rep) {
      const rows = rep.rows.slice().sort((a, b) => (a.hit_at_k - b.hit_at_k));  // 未命中排最前
      return `
        <section class="card">
          <div class="card-head"><div><div class="kicker">PER QUERY</div><h3>逐条结果 · ${rows.length} 条</h3></div></div>
          <div class="card-pad" style="display:grid;gap:0">
            ${rows.map(r => {
              const miss = !r.hit_at_k;
              const rankText = r.rank > 0 ? ('#' + r.rank) : '✗';
              const hint = miss ? `<div class="eval-row-hint mono">→ 期望 ${escapeHtml(r.expected)}</div>` : '';
              return `<div class="eval-row${miss ? ' miss' : ''}">
                <div class="eval-row-main">
                  <div class="eval-row-q">${escapeHtml(r.query)}</div>
                  ${hint}
                </div>
                <div class="eval-row-meta">
                  <span class="badge mono eval-kind-${r.kind}">${r.kind}</span>
                  <span class="badge ${EVAL_MODE_CLASS[r.mode] || ''} mono">${r.mode}</span>
                  <span class="mono eval-rank${miss ? ' miss' : ''}">${rankText}</span>
                </div>
              </div>`;
            }).join('')}
          </div>
        </section>`;
    }

    function renderEvalMain() {
      const rep = state.evalReport;
      let body;
      if (state.evalRunning && !rep) {
        body = `<section class="card"><div class="card-pad"><div class="empty">${iconSpin()}<div style="font-size:13px;color:var(--text-dim)">正在运行评测…</div></div></div></section>`;
      } else if (!rep) {
        body = `<section class="card"><div class="card-pad"><div class="empty">${iconInfo()}<div style="font-size:13px;color:var(--text-dim)">点「运行评测」开始</div></div></div></section>`;
      } else if (!rep.ok) {
        body = `<section class="card"><div class="card-pad"><div class="empty">${iconInfo()}<div style="font-size:13px;color:var(--text-dim)">${escapeHtml(rep.reason || '评测不可用')}</div></div></div></section>`;
      } else {
        body = renderEvalMetrics(rep) + renderEvalByKind(rep) + renderEvalRows(rep);
      }
      return `<div style="display:grid;gap:16px">${renderEvalToolbar()}${body}</div>`;
    }
