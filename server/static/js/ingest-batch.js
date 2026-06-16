    // ====== 批量入库:上传多条记录的 Markdown → 并行抽取 → 逐条/批量入库 ======

    function normBatchRecord(r) {
      return {
        raw: r.raw || '',
        title: r.title || '',
        category: r.category || '未分类',
        signatures: (r.signatures && r.signatures.length) ? r.signatures.slice() : [''],
        components: (r.components && r.components.length) ? r.components.slice() : [''],
        background: r.background || '',
        diagnosis: r.diagnosis || '',
        solution: r.solution || '',
        ident: '',
        error: r.ok ? '' : (r.error || '抽取失败'),
        status: 'pending',          // pending | committing | committed | failed
        statusMsg: '',
        case_file: '',
        streamText: r.streamText || '',
        expanded: !r.ok             // 抽取失败的默认展开,提醒人工补全
      };
    }

    function initBatchStreamRecord(raw, i) {
      return {
        raw,
        title: `记录 ${i + 1}`,
        category: '未分类',
        signatures: [''],
        components: [''],
        background: '',
        diagnosis: '',
        solution: '',
        ident: '',
        error: '',
        status: 'extracting',       // extracting | pending | committing | committed | failed
        statusMsg: '等待模型输出',
        case_file: '',
        streamText: '',
        expanded: true
      };
    }

    // 与后端 _split_records 一致:按独占一行的 --- 切分,去空白
    function splitBatchRecords(raw) {
      // 先归一化换行(CRLF/CR → LF),否则 Windows 文件的 ---\r 匹配不到,整文件会变成一条
      return raw.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
        .split(/^[ \t]*-{3,}[ \t]*$/m).map(s => s.trim()).filter(Boolean);
    }

    async function onBatchFile(file) {
      if (!file) return;
      let text = '';
      try { text = await file.text(); } catch (e) { showToast('读取文件失败'); return; }
      if (!text.trim()) { showToast('文件内容为空'); return; }
      const fileInput = document.getElementById('batchFile');
      if (fileInput) fileInput.value = '';   // 允许再次选同一文件
      const split = splitBatchRecords(text);
      if (!split.length) { showToast('未解析到任何记录;请用独占一行的 --- 分隔'); return; }
      // 先进入"确认切分"阶段,让用户核对分割结果,无误后再并行抽取
      Object.assign(state, { batchActive: true, batchStage: 'split', batchRaw: text, batchSplit: split, batchRecords: [], batchSummary: null });
      render();
      showToast(`已按 --- 切分为 ${split.length} 条,请确认后抽取`);
    }

    async function runBatchPreview(raw) {
      Object.assign(state, {
        batchActive: true,
        batchStage: 'extracting',
        batchRecords: state.batchSplit.map(initBatchStreamRecord),
        batchSummary: null
      });
      render();
      try {
        const r = await fetch('/api/ingest/preview_batch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ raw })
        });
        if (noBackend(r.status)) { state.batchStage = 'split'; render(); showToast('后端未连接 · 无法批量抽取'); return; }
        if (!r.ok) {
          let detail = '';
          try { detail = (await r.json()).detail; } catch (e) { detail = await r.text(); }
          throw new Error(detail || ('HTTP ' + r.status));
        }
        await readBatchPreviewStream(r);
        state.batchStage = 'review';
        render();
        const failed = state.batchRecords.filter(x => x.error).length;
        showToast(`已抽取 ${state.batchRecords.length} 条` + (failed ? ` · ${failed} 条失败,请补全` : ''));
      } catch (e) {
        state.batchStage = 'split';     // 失败回到切分确认页,可重试
        render();
        showToast('批量抽取失败:' + String(e && e.message || e));
      }
    }

    async function readBatchPreviewStream(resp) {
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();
        lines.forEach(line => handleBatchPreviewLine(line));
      }
      if (buf.trim()) handleBatchPreviewLine(buf);
    }

    function handleBatchPreviewLine(line) {
      const txt = String(line || '').trim();
      if (!txt) return;
      let event = null;
      try { event = JSON.parse(txt); } catch (e) {
        console.error('[log-wiki] bad batch stream line', txt, e);
        return;
      }
      handleBatchPreviewEvent(event);
    }

    function handleBatchPreviewEvent(event) {
      if (!event || typeof event.index !== 'number') {
        if (event && event.type === 'summary') state.batchSummary = { ok: event.ok, total: event.count, failed: event.failed };
        return;
      }
      const rec = state.batchRecords[event.index] || initBatchStreamRecord(event.raw || '', event.index);
      state.batchRecords[event.index] = rec;
      if (event.type === 'start') {
        rec.raw = event.raw || rec.raw;
        rec.status = 'extracting';
        rec.statusMsg = '模型已开始输出';
        rec.expanded = true;
      } else if (event.type === 'delta') {
        rec.streamText += event.text || '';
        rec.status = 'extracting';
        rec.statusMsg = `生成中 · ${rec.streamText.length} 字`;
      } else if (event.type === 'done') {
        const next = normBatchRecord(Object.assign({}, event.record || {}, { streamText: rec.streamText }));
        next.expanded = false;
        state.batchRecords[event.index] = next;
      } else if (event.type === 'error') {
        rec.raw = event.raw || rec.raw;
        rec.error = event.error || '抽取失败';
        rec.status = 'failed';
        rec.statusMsg = rec.error;
        rec.expanded = true;
      }
      scheduleBatchRender();
    }

    function scheduleBatchRender() {
      if (scheduleBatchRender.pending) return;
      scheduleBatchRender.pending = true;
      requestAnimationFrame(() => {
        scheduleBatchRender.pending = false;
        if (state.batchActive && state.batchStage === 'extracting') render();
      });
    }

    function exitBatch() {
      Object.assign(state, { batchActive: false, batchStage: 'split', batchRaw: '', batchSplit: [], batchRecords: [], batchSummary: null, step: 1 });
      render();
    }

    function batchPayload(rec) {
      return {
        raw: rec.raw, title: rec.title, category: rec.category,
        signatures: rec.signatures.filter(s => s && s.trim()),
        components: rec.components.filter(c => c && c.trim()),
        background: rec.background, diagnosis: rec.diagnosis, solution: rec.solution,
        ident: rec.ident || null
      };
    }

    function batchInvalid(rec) {
      if (!rec.title.trim()) return '标题不能为空';
      if (!rec.signatures.filter(s => s && s.trim()).length) return '至少保留一条 signature';
      return '';
    }

    async function commitBatchRecord(i) {
      const rec = state.batchRecords[i];
      if (!rec || rec.status === 'committed' || rec.status === 'committing') return;
      const bad = batchInvalid(rec);
      if (bad) { showToast(bad); rec.expanded = true; render(); return; }
      rec.status = 'committing'; rec.statusMsg = ''; render();
      try {
        const r = await fetch('/api/ingest/commit', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(batchPayload(rec))
        });
        if (noBackend(r.status)) { rec.status = 'pending'; render(); showToast('后端未连接 · 无法入库'); return; }
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || '入库失败');
        rec.status = 'committed'; rec.case_file = data.case_file; rec.expanded = false;
        afterBatchMutated();
        render();
        showToast('已入库:' + (data.case_file || rec.title));
      } catch (e) {
        rec.status = 'failed'; rec.statusMsg = String(e && e.message || e);
        render();
        showToast('入库失败,详见卡片');
      }
    }

    async function commitBatchAll() {
      if (state.batchCommitting) return;
      const pending = [];
      state.batchRecords.forEach((rec, i) => {
        if (rec.status === 'committed' || rec.status === 'committing') return;
        if (batchInvalid(rec)) { rec.status = 'failed'; rec.statusMsg = batchInvalid(rec); return; }
        pending.push(i);
      });
      if (!pending.length) { render(); showToast('没有可入库的有效记录'); return; }
      state.batchCommitting = true;
      pending.forEach(i => { state.batchRecords[i].status = 'committing'; state.batchRecords[i].statusMsg = ''; });
      render();
      try {
        const r = await fetch('/api/ingest/commit_batch', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ records: pending.map(i => batchPayload(state.batchRecords[i])) })
        });
        if (noBackend(r.status)) {
          pending.forEach(i => state.batchRecords[i].status = 'pending');
          state.batchCommitting = false; render(); showToast('后端未连接 · 无法批量入库'); return;
        }
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || '批量入库失败');
        (data.results || []).forEach(res => {
          const gi = pending[res.index];                 // 映射回全局索引
          const rec = state.batchRecords[gi];
          if (!rec) return;
          if (res.ok) { rec.status = 'committed'; rec.case_file = res.case_file; rec.expanded = false; }
          else { rec.status = 'failed'; rec.statusMsg = res.error || '入库失败'; }
        });
        state.batchSummary = { ok: data.ok, total: data.total };
        state.batchCommitting = false;
        afterBatchMutated();
        render();
        showToast(`批量入库完成:成功 ${data.ok} / ${data.total}`);
      } catch (e) {
        pending.forEach(i => { if (state.batchRecords[i].status === 'committing') state.batchRecords[i].status = 'pending'; });
        state.batchCommitting = false;
        render();
        showToast(String(e && e.message || e));
      }
    }

    function afterBatchMutated() {
      state.knowledgeDirty = true;
      state.graph = null; state.graphSelected = '';
      refreshMeta();
    }

    // ---- 渲染 ----
    function batchStatusBadge(rec) {
      if (rec.status === 'committed') return '<span class="badge ok"><span class="dot"></span>已入库</span>';
      if (rec.status === 'extracting') return '<span class="badge info"><span class="dot pulse"></span>抽取中</span>';
      if (rec.status === 'committing') return '<span class="badge info"><span class="dot pulse"></span>入库中</span>';
      if (rec.status === 'failed') return '<span class="badge" style="color:var(--danger);border-color:var(--danger-soft)"><span class="dot" style="background:var(--danger)"></span>失败</span>';
      if (rec.error) return '<span class="badge warn"><span class="dot"></span>需补全</span>';
      return '<span class="badge mono">待入库</span>';
    }

    function renderBatchSplit() {
      const recs = state.batchSplit;
      return `
        <section class="card">
          <div class="card-head">
            <div><div class="kicker">BATCH · CONFIRM SPLIT</div><h3>确认切分结果 · 共 ${recs.length} 条</h3></div>
            <span class="badge mono">未抽取</span>
          </div>
          <div class="card-pad">
            <p class="muted" style="font-size:12.5px;margin:0 0 13px;line-height:1.55">已按独占一行的 <code class="mono">---</code> 把文件切成下列各条原文。确认切分无误后再并行抽取;若不对,返回检查文件中的分隔符。</p>
            <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:14px">
              <button class="btn sm" id="batchExit" type="button">${iconBack()}返回粘贴</button>
              <button class="btn primary" id="batchStartExtract" type="button">${iconSpark()}开始并行抽取(${recs.length} 条)</button>
            </div>
            <div style="display:grid;gap:10px">
              ${recs.map((raw, i) => `
                <div class="batch-card open">
                  <div class="batch-head" style="cursor:default">
                    <div class="batch-idx">${i + 1}</div>
                    <div class="batch-main"><div class="batch-title">记录 ${i + 1}</div><div class="batch-sub mono">${raw.length} 字</div></div>
                  </div>
                  <div class="batch-body"><pre class="codebox" style="max-height:200px;overflow:auto;margin:0">${escapeHtml(raw)}</pre></div>
                </div>`).join('')}
            </div>
          </div>
        </section>`;
    }

    function renderBatchMain() {
      if (state.batchStage === 'split') return renderBatchSplit();
      if (state.batchStage === 'extracting') {
        const done = state.batchRecords.filter(r => r.status === 'pending' || r.status === 'failed').length;
        return `
          <section class="card">
            <div class="card-head">
              <div><div class="kicker">BATCH · MODEL EXTRACTION</div><h3>并行抽取中</h3></div>
              <span class="badge info"><span class="dot pulse"></span>${done}/${state.batchSplit.length}</span>
            </div>
            <div class="card-pad">
              <div style="display:grid;gap:10px">
                ${state.batchRecords.map((rec, i) => renderBatchStreamCard(rec, i)).join('')}
              </div>
            </div>
          </section>`;
      }
      const recs = state.batchRecords;
      const committed = recs.filter(r => r.status === 'committed').length;
      const remain = recs.length - committed;
      return `
        <section class="card">
          <div class="card-head">
            <div><div class="kicker">BATCH · HUMAN REVIEW</div><h3>批量复核入库 · 共 ${recs.length} 条</h3></div>
            <span class="badge ${committed === recs.length ? 'ok' : 'warn'}">已入库 ${committed}/${recs.length}</span>
          </div>
          <div class="card-pad">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:14px">
              <button class="btn sm" id="batchExit" type="button">${iconBack()}返回粘贴</button>
              <div style="display:flex;align-items:center;gap:12px">
                <span class="mono muted" style="font-size:11px">${remain} 条待入库</span>
                <button class="btn primary" id="batchCommitAll" type="button" ${state.batchCommitting || !remain ? 'disabled' : ''}>${state.batchCommitting ? iconSpin() : iconCheck()}全部入库</button>
              </div>
            </div>
            <div style="display:grid;gap:10px">
              ${recs.map((rec, i) => renderBatchCard(rec, i)).join('')}
            </div>
          </div>
        </section>`;
    }

    function renderBatchStreamCard(rec, i) {
      const stream = rec.streamText || '';
      const msg = rec.status === 'extracting' ? (rec.statusMsg || '生成中') : (rec.error ? '抽取失败 · 可手动补全' : '抽取完成');
      return `
        <div class="batch-card open st-${rec.status}">
          <div class="batch-head" style="cursor:default">
            <div class="batch-idx">${i + 1}</div>
            <div class="batch-main">
              <div class="batch-title">${escapeHtml(rec.title || `记录 ${i + 1}`)}</div>
              <div class="batch-sub mono">${escapeHtml(msg)}</div>
            </div>
            ${batchStatusBadge(rec)}
          </div>
          <div class="batch-body" style="display:grid;gap:10px">
            ${rec.error ? `<div class="result-block warn" style="border-left-color:var(--danger);background:var(--danger-soft)"><div class="mono" style="font-size:11.5px;color:var(--danger);white-space:pre-wrap;word-break:break-word">${escapeHtml(rec.error)}</div></div>` : ''}
            <pre class="codebox batch-stream">${escapeHtml(stream || '等待模型输出…')}${rec.status === 'extracting' ? '<span class="caret"></span>' : ''}</pre>
            <details class="batch-raw"><summary class="mono muted" style="font-size:11px;cursor:pointer">查看原始记录</summary><pre class="codebox" style="margin-top:8px;max-height:120px;overflow:auto">${escapeHtml(rec.raw)}</pre></details>
          </div>
        </div>`;
    }

    function renderBatchCard(rec, i) {
      const sigCount = rec.signatures.filter(s => s && s.trim()).length;
      const sub = rec.error ? '抽取失败 · 可手动补全' : `${sigCount} signatures · ${escapeHtml(rec.category || '未分类')}`;
      const canCommit = rec.status !== 'committed' && rec.status !== 'committing';
      return `
        <div class="batch-card ${rec.expanded ? 'open' : ''} st-${rec.status}">
          <div class="batch-head" data-batch-toggle="${i}">
            <div class="batch-idx">${i + 1}</div>
            <div class="batch-main">
              <div class="batch-title">${escapeHtml(rec.title || '(未命名,点开补全)')}</div>
              <div class="batch-sub mono">${sub}</div>
            </div>
            ${batchStatusBadge(rec)}
            ${canCommit ? `<button class="btn sm primary batch-commit" data-batch-commit="${i}" type="button" ${rec.status === 'committing' ? 'disabled' : ''}>入库</button>` : ''}
            <span class="batch-chev">${iconChevron()}</span>
          </div>
          ${rec.expanded ? renderBatchForm(rec, i) : ''}
        </div>`;
    }

    function renderBatchForm(rec, i) {
      const errBanner = rec.error ? `<div class="result-block warn" style="border-left-color:var(--danger);background:var(--danger-soft)"><div class="mono" style="font-size:11.5px;color:var(--text-dim);white-space:pre-wrap;word-break:break-word">抽取失败:${escapeHtml(rec.error)}</div></div>` : '';
      const failBanner = rec.statusMsg ? `<div class="result-block warn" style="border-left-color:var(--danger);background:var(--danger-soft)"><div class="mono" style="font-size:11.5px;color:var(--danger);white-space:pre-wrap;word-break:break-word">${escapeHtml(rec.statusMsg)}</div></div>` : '';
      return `
        <div class="batch-body" style="display:grid;gap:12px">
          ${errBanner}${failBanner}
          <label class="lbl"><span>标题</span><input class="field brec" data-rec="${i}" data-field="title" value="${escapeHtml(rec.title)}"></label>
          <label class="lbl"><span>类别</span><input class="field brec" data-rec="${i}" data-field="category" value="${escapeHtml(rec.category)}" style="max-width:240px"></label>
          <div class="lbl">
            <span>signatures <span class="mono" style="color:var(--text-faint);font-weight:400;text-transform:none;letter-spacing:0">报错原文 · 勿改写</span></span>
            <div style="display:grid;gap:8px">
              ${rec.signatures.map((v, j) => `
                <div style="display:flex;gap:8px">
                  <input class="field mono bsig" data-rec="${i}" data-index="${j}" style="font-size:12px" value="${escapeHtml(v)}" placeholder="报错原文 / 异常类全名 / 错误码">
                  <button class="btn icon bsig-del" data-rec="${i}" data-index="${j}" type="button" title="删除">${iconTrash()}</button>
                </div>`).join('')}
            </div>
            <button class="btn sm bsig-add" data-rec="${i}" type="button" style="justify-self:start;margin-top:2px">${iconPlus()}添加 signature</button>
          </div>
          <div class="lbl">
            <span>组件</span>
            <div class="two" style="gap:8px">
              ${rec.components.map((v, j) => `
                <div style="display:flex;gap:6px">
                  <input class="field mono bcomp" data-rec="${i}" data-index="${j}" style="font-size:12px" value="${escapeHtml(v)}" placeholder="服务 / 组件名">
                  <button class="btn icon bcomp-del" data-rec="${i}" data-index="${j}" type="button" title="删除">${iconTrash()}</button>
                </div>`).join('')}
            </div>
            <button class="btn sm bcomp-add" data-rec="${i}" type="button" style="justify-self:start;margin-top:2px">${iconPlus()}添加组件</button>
          </div>
          <div class="two">
            <label class="lbl"><span>问题背景</span><textarea class="field brec" data-rec="${i}" data-field="background" style="height:84px">${escapeHtml(rec.background)}</textarea></label>
            <label class="lbl"><span>定位过程</span><textarea class="field brec" data-rec="${i}" data-field="diagnosis" style="height:84px">${escapeHtml(rec.diagnosis)}</textarea></label>
          </div>
          <label class="lbl"><span>解决方案</span><textarea class="field brec" data-rec="${i}" data-field="solution" style="height:80px">${escapeHtml(rec.solution)}</textarea></label>
          <details class="batch-raw"><summary class="mono muted" style="font-size:11px;cursor:pointer">查看原始记录</summary><pre class="codebox" style="margin-top:8px;max-height:160px;overflow:auto">${escapeHtml(rec.raw)}</pre></details>
        </div>`;
    }

    function bindBatchEvents() {
      const bindId = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
      bindId('batchExit', exitBatch);
      bindId('batchStartExtract', () => runBatchPreview(state.batchRaw));
      bindId('batchCommitAll', commitBatchAll);
      root.querySelectorAll('[data-batch-toggle]').forEach(el => el.onclick = e => {
        if (e.target.closest('[data-batch-commit]')) return;   // 点"入库"不展开
        const i = Number(el.dataset.batchToggle);
        state.batchRecords[i].expanded = !state.batchRecords[i].expanded;
        render();
      });
      root.querySelectorAll('[data-batch-commit]').forEach(el => el.onclick = e => {
        e.stopPropagation();
        commitBatchRecord(Number(el.dataset.batchCommit));
      });
      root.querySelectorAll('.brec').forEach(el => el.oninput = e => {
        state.batchRecords[Number(el.dataset.rec)][el.dataset.field] = e.target.value;
      });
      root.querySelectorAll('.bsig').forEach(el => el.oninput = e => {
        state.batchRecords[Number(el.dataset.rec)].signatures[Number(el.dataset.index)] = e.target.value;
      });
      root.querySelectorAll('.bcomp').forEach(el => el.oninput = e => {
        state.batchRecords[Number(el.dataset.rec)].components[Number(el.dataset.index)] = e.target.value;
      });
      root.querySelectorAll('.bsig-del').forEach(el => el.onclick = e => batchRemoveList(Number(el.dataset.rec), 'signatures', Number(el.dataset.index)));
      root.querySelectorAll('.bcomp-del').forEach(el => el.onclick = e => batchRemoveList(Number(el.dataset.rec), 'components', Number(el.dataset.index)));
      root.querySelectorAll('.bsig-add').forEach(el => el.onclick = e => batchAddList(Number(el.dataset.rec), 'signatures'));
      root.querySelectorAll('.bcomp-add').forEach(el => el.onclick = e => batchAddList(Number(el.dataset.rec), 'components'));
    }

    function batchAddList(i, key) {
      state.batchRecords[i][key].push('');
      render();
    }

    function batchRemoveList(i, key, index) {
      state.batchRecords[i][key].splice(index, 1);
      if (!state.batchRecords[i][key].length) state.batchRecords[i][key].push('');
      render();
    }
