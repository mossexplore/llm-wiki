    async function runQuery() {
      if (!state.logText.trim()) { showToast('请输入报错信息'); return; }
      state.querying = true;
      state.result = null;
      render();
      try {
        const r = await fetch('/api/query', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ log: state.logText })
        });
        if (noBackend(r.status)) return demoQuery();
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || '检索失败');
        state.result = data;
        state.querying = false;
        render();
      } catch (e) {
        state.querying = false;
        render();
        showToast(String(e && e.message || e));
      }
    }

    function demoQuery() {
      const log = state.logText.toLowerCase();
      if (log.includes('hikari') || log.includes('connection is not available')) {
        state.result = { mode: 'exact', hits: [{ title: SAMPLE_CASE_FALLBACK.title, status: 'verified', file: 'wiki/cases/hikari-pool-exhausted.md', matched: ['HikariPool-1 - Connection is not available'], solution: SAMPLE_CASE_FALLBACK.solution }] };
      } else if (log.includes('oom') || log.includes('outofmemory') || log.includes('timeout') || log.includes('timed out')) {
        state.result = { mode: 'fuzzy', hits: [{ title: 'JVM Metaspace OOM', score: 0.42, file: 'wiki/cases/jvm-metaspace-oom.md' }, { title: 'Kafka 消费组 Rebalance 风暴', score: 0.31, file: 'wiki/cases/kafka-rebalance.md' }] };
      } else {
        state.result = { mode: 'none', hits: [] };
      }
      state.querying = false;
      render();
      showToast('后端未连接 · 已演示检索结果');
    }

    function renderQueryMain() {
      const r = state.result;
      return `
        <section class="card">
          <div class="card-head"><div><div class="kicker">QUERY · PASTE LOG</div><h3>粘一段日志报错检索相似案例</h3></div><span class="badge mono">噪声无所谓</span></div>
          <div class="card-pad">
            <textarea id="logText" class="field mono" spellcheck="false" placeholder="例:2026-06-13 15:10:33 ERROR HikariPool-1 - Connection is not available, request timed out after 30007ms" style="height:122px;font-size:12.5px;line-height:1.6">${escapeHtml(state.logText)}</textarea>
            <button class="btn primary" id="runQuery" type="button" style="margin-top:13px" ${state.querying ? 'disabled' : ''}>${state.querying ? iconSpin() : iconSearch()}检索</button>
          </div>
        </section>
        ${r ? renderResults(r) : ''}`;
    }

    function renderResults(r) {
      if (r.mode === 'exact') {
        return `
          <section class="card">
            <div class="card-head"><div><div class="kicker">RESULT · EXACT MATCH</div><h3>精确命中 ${r.hits.length} 个案例</h3></div><span class="badge ok">${iconCheck()}EXACT</span></div>
            <div class="card-pad" style="display:grid;gap:12px">
              ${r.hits.map(h => `
                <div class="result-block">
                  <div style="display:grid;grid-template-columns:1fr auto;gap:10px;align-items:start">
                    <strong style="font-size:14.5px;line-height:1.45">${escapeHtml(h.title)}</strong>
                    <span class="lc ${h.status === 'verified' ? 'published' : 'review'}"><span class="dot"></span>${escapeHtml(h.status)}</span>
                  </div>
                  <div class="mono muted" style="font-size:11.5px;margin-top:8px;line-height:1.55">${escapeHtml(h.file)} · 命中:${escapeHtml((h.matched || []).join('；'))}</div>
                  ${h.note ? `<div class="muted" style="font-size:11.5px;margin-top:5px">${escapeHtml(h.note)}</div>` : ''}
                  <div class="kicker" style="margin-top:13px;margin-bottom:6px">SOLUTION</div>
                  <div style="font-size:13px;color:var(--text-dim);line-height:1.72;white-space:pre-wrap">${escapeHtml(h.solution)}</div>
                </div>`).join('')}
            </div>
          </section>`;
      }
      if (r.mode === 'fuzzy') {
        return `
          <section class="card">
            <div class="card-head"><div><div class="kicker">RESULT · POSSIBLY RELATED</div><h3>未精确命中 · 以下可能相关</h3></div><span class="badge warn">需人工判断</span></div>
            <div class="card-pad" style="display:grid;gap:10px">
              <p class="muted" style="margin:0 0 2px;font-size:12px">按重合度排序,仅供参考,勿直接照搬其方案。</p>
              ${r.hits.map(h => `
                <div class="result-block warn" style="display:flex;align-items:center;justify-content:space-between;gap:12px">
                  <div style="min-width:0"><strong style="font-size:13.5px;display:block;line-height:1.4">${escapeHtml(h.title)}</strong><div class="mono muted" style="font-size:11px;margin-top:5px;word-break:break-all">${escapeHtml(h.file)}</div></div>
                  <span class="badge warn mono">重合 ${escapeHtml(typeof h.score === 'number' ? h.score.toFixed(2) : h.score)}</span>
                </div>`).join('')}
            </div>
          </section>`;
      }
      return `
        <section class="card">
          <div class="card-pad">
            <div class="empty">
              ${iconInfo()}
              <div style="font-size:14px;font-weight:650;color:var(--text-dim)">知识库中暂无相关案例</div>
              <div style="font-size:12px;max-width:380px;line-height:1.55">请勿编造解决方案;排查后可在「写入知识」页把本次结论入库,下次即可命中。</div>
            </div>
          </div>
        </section>`;
    }

