    async function runQuery() {
      if (!state.logText.trim()) { showToast('请输入报错信息'); return; }
      state.querying = true;
      state.result = null;
      render();
      const t0 = performance.now();
      try {
        const r = await fetch('/api/query', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ log: state.logText })
        });
        if (noBackend(r.status)) return demoQuery(t0);
        const data = await r.json();
        if (!r.ok) throw new Error(apiErrorMessage(data, '检索失败'));
        state.result = withElapsed(data, t0);
        state.querying = false;
        render();
      } catch (e) {
        state.querying = false;
        render();
        showToast(String(e && e.message || e));
      }
    }

    // 取检索耗时(ms):优先用后端纯检索耗时;缺失时用前端端到端耗时兜底。
    function withElapsed(data, t0) {
      const ms = Math.round(
        typeof data.elapsed_ms === 'number'
          ? data.elapsed_ms
          : (performance.now() - t0)
      );
      return Object.assign({}, data, { elapsed_ms: ms });
    }

    function demoQuery(t0) {
      const log = state.logText.toLowerCase();
      let base;
      if (log.includes('hikari') || log.includes('connection is not available')) {
        base = { mode: 'exact', hits: [{ title: SAMPLE_CASE_FALLBACK.title, status: 'verified', file: 'wiki/cases/hikari-pool-exhausted.md', matched: ['HikariPool-1 - Connection is not available'], solution: SAMPLE_CASE_FALLBACK.solution }] };
      } else if (log.includes('oom') || log.includes('outofmemory') || log.includes('timeout') || log.includes('timed out')) {
        // 无后端时的占位演示数据;score 模拟后端的 BM25 相关度(真实检索为实时计算)
        base = { mode: 'fuzzy', hits: [{ title: 'JVM Metaspace OOM', score: 0.42, file: 'wiki/cases/jvm-metaspace-oom.md' }, { title: 'Kafka 消费组 Rebalance 风暴', score: 0.31, file: 'wiki/cases/kafka-rebalance.md' }] };
      } else {
        base = { mode: 'none', hits: [] };
      }
      state.result = withElapsed(base, t0);
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
      const elapsed = typeof r.elapsed_ms === 'number' ? r.elapsed_ms : null;
      const elapsedBadge = elapsed != null
        ? `<span class="badge info mono">${elapsed} ms</span>`
        : '';
      if (r.mode === 'exact') {
        return `
          <section class="card">
            <div class="card-head"><div><div class="kicker">RESULT · EXACT MATCH</div><h3>精确命中 ${r.hits.length} 个案例</h3></div><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">${elapsedBadge}<span class="badge ok">${iconCheck()}EXACT</span></div></div>
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
            <div class="card-head"><div><div class="kicker">RESULT · POSSIBLY RELATED</div><h3>未精确命中 · 以下可能相关</h3></div><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">${elapsedBadge}<span class="badge warn">需人工判断</span></div></div>
            <div class="card-pad" style="display:grid;gap:10px">
              <p class="muted" style="margin:0 0 2px;font-size:12px">按 BM25 相关度排序(相对排序分,非置信度),仅供参考,勿直接照搬其方案。</p>
              ${r.hits.map(h => `
                <div class="result-block warn" style="display:flex;align-items:center;justify-content:space-between;gap:12px">
                  <div style="min-width:0"><strong style="font-size:13.5px;display:block;line-height:1.4">${escapeHtml(h.title)}</strong><div class="mono muted" style="font-size:11px;margin-top:5px;word-break:break-all">${escapeHtml(h.file)}</div></div>
                  <span class="badge warn mono" title="FTS5 BM25 相关度,越大越相关;相对排序分,非 0-1 置信度">相关度 ${escapeHtml(typeof h.score === 'number' ? h.score.toFixed(2) : h.score)}</span>
                </div>`).join('')}
            </div>
          </section>`;
      }
      return `
        <section class="card">
          <div class="card-head"><div><div class="kicker">RESULT · NO MATCH</div><h3>知识库中暂无相关案例</h3></div>${elapsedBadge}</div>
          <div class="card-pad">
            <div class="empty">
              ${iconInfo()}
              <div style="font-size:14px;font-weight:650;color:var(--text-dim)">知识库中暂无相关案例</div>
              <div style="font-size:12px;max-width:380px;line-height:1.55">请勿编造解决方案;排查后可在「写入知识」页把本次结论入库,下次即可命中。</div>
            </div>
          </div>
        </section>`;
    }
