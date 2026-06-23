    async function loadSample() {
      if (!state.sample.raw) await refreshMeta();
      state.rawInput = state.sample.raw || SAMPLE_RAW_FALLBACK;
      render();
      showToast('已载入示例记录');
    }

    async function doPreview() {
      const raw = state.rawInput;
      if (!raw.trim()) { showToast('请先粘贴原始排查记录'); return; }
      Object.assign(state, { step: 2, maxStep: Math.max(state.maxStep, 2), previewing: true, streamText: '', previewProgress: calcExtractionProgress(''), parseErr: '', draft: null, committed: null });
      render();
      const requestId = newRequestId();
      let httpStatus = null;
      let normalizedText = '';
      try {
        const resp = await fetch('/api/ingest/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Request-ID': requestId },
          body: JSON.stringify({ raw })
        });
        httpStatus = resp.status;
        const responseRequestId = resp.headers.get('X-Request-ID') || requestId;
        if (!resp.ok) {
          if (noBackend(resp.status)) return demoPreview(raw);
          let detail = '';
          try { detail = apiErrorMessage(await resp.json(), ''); } catch (e) {}
          throw new Error(`[request_id=${responseRequestId}] ${detail || 'HTTP ' + resp.status}`);
        }
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let acc = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          acc += decoder.decode(value, { stream: true });
          state.streamText = acc;
          state.previewProgress = calcExtractionProgress(acc);
          render();
        }
        if (acc.includes('[ERROR]')) throw new Error(acc);
        normalizedText = normalizeJsonText(acc);
        state.draft = toDraft(JSON.parse(normalizedText), raw);
        state.previewProgress = calcExtractionProgress(acc, true);
        goToStep(3);
        state.previewing = false;
        console.info('[log-wiki] ingest preview parsed', {
          requestId: responseRequestId,
          rawLength: raw.length,
          streamLength: acc.length,
          normalizedLength: normalizedText.length
        });
        render();
        showToast('解析完成,请复核后确认入库');
      } catch (e) {
        logPreviewFailure({
          requestId,
          httpStatus,
          rawLength: raw.length,
          streamText: state.streamText,
          normalizedText,
          error: e
        });
        state.parseErr = `request_id=${requestId}\n${String(e && e.message || e)}`;
        state.previewing = false;
        render();
        showToast('解析失败,详见下方');
      }
    }

    async function demoPreview(raw) {
      const obj = state.sample.case || SAMPLE_CASE_FALLBACK;
      const json = JSON.stringify(obj, null, 2);
      state.previewProgress = calcExtractionProgress('');
      for (let k = 1; k <= 6; k++) {
        await new Promise(r => setTimeout(r, 90));
        state.streamText = json.slice(0, Math.round(json.length * k / 6));
        state.previewProgress = calcExtractionProgress(state.streamText);
        render();
      }
      state.streamText = json;
      state.previewProgress = calcExtractionProgress(json, true);
      state.draft = toDraft(obj, raw);
      goToStep(3);
      state.previewing = false;
      render();
      showToast('后端未连接 · 已载入演示数据');
    }

    async function commit() {
      const d = state.draft;
      if (!d) return;
      if (state.mode === 'list') return updateKnowledge();
      const payload = {
        raw: d.raw,
        title: d.title,
        category: d.category,
        signatures: d.signatures.filter(s => s && s.trim()),
        components: d.components.filter(c => c && c.trim()),
        background: d.background,
        diagnosis: d.diagnosis,
        solution: d.solution,
        ident: d.ident || null
      };
      if (!payload.title.trim()) { showToast('标题不能为空'); return; }
      if (!payload.signatures.length) { showToast('至少保留一条 signature'); return; }
      state.committing = true;
      render();
      try {
        const r = await fetch('/api/ingest/commit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (noBackend(r.status)) {
          const stamp = payload.ident || String(Date.now());
          state.committed = { case_file: 'wiki/cases/' + slug(payload.title) + '.md', raw_file: 'raw/sources/' + stamp + '.md', demo: true };
          goToStep(4);
          state.committing = false;
          render();
          showToast('后端未连接 · 已演示入库结果');
          return;
        }
        const data = await r.json();
        if (!r.ok) throw new Error(apiErrorMessage(data, '入库失败'));
        state.committed = data;
        state.knowledgeDirty = true;   // 标记列表需刷新:切到知识列表页会自动重载
        state.graph = null; state.graphSelected = '';   // 新增知识,图谱缓存失效
        goToStep(4);
        state.committing = false;
        render();
        refreshMeta();
        showToast('已真正写入本地知识库');
      } catch (e) {
        state.committing = false;
        render();
        showToast(String(e && e.message || e));
      }
    }

    function resetIngest() {
      Object.assign(state, { step: 1, maxStep: 1, rawInput: '', streamText: '', previewProgress: null, parseErr: '', draft: null, committed: null });
      render();
    }

    const EXTRACTION_FIELDS = [
      { key: 'title', label: '标题', weight: 10, type: 'string' },
      { key: 'category', label: '类别', weight: 8, type: 'string' },
      { key: 'signatures', label: '报错签名', weight: 16, type: 'array' },
      { key: 'components', label: '组件', weight: 10, type: 'array' },
      { key: 'background', label: '问题背景', weight: 18, type: 'string' },
      { key: 'diagnosis', label: '定位过程', weight: 20, type: 'string' },
      { key: 'solution', label: '解决方案', weight: 18, type: 'string' }
    ];

    function calcExtractionProgress(text, complete = false) {
      const raw = String(text || '');
      const fields = EXTRACTION_FIELDS.map(f => {
        const seen = new RegExp('"' + f.key + '"\\s*:', 's').test(raw);
        let filled = false;
        if (f.type === 'array') {
          const m = raw.match(new RegExp('"' + f.key + '"\\s*:\\s*\\[([\\s\\S]*)', 's'));
          if (m) {
            const beforeClose = m[1].split(']')[0] || '';
            filled = /"((?:\\.|[^"\\])+)"/.test(beforeClose);
          }
        } else {
          const m = raw.match(new RegExp('"' + f.key + '"\\s*:\\s*"((?:\\\\.|[^"\\\\])*)', 's'));
          filled = !!(m && m[1] && m[1].trim());
        }
        return Object.assign({}, f, { seen, filled, progress: filled ? 1 : (seen ? 0.25 : 0) });
      });
      let percent = fields.reduce((sum, f) => sum + f.weight * f.progress, raw ? 3 : 0);
      percent = complete ? 100 : Math.max(raw ? 3 : 0, Math.min(94, Math.round(percent)));
      const active = fields.find(f => !f.filled && f.seen) || fields.find(f => !f.filled) || fields[fields.length - 1];
      return { percent, fields, activeKey: active && active.key, complete };
    }

    function renderExtractionProgress(options = {}) {
      const isPreviewing = options.previewing != null ? options.previewing : state.previewing;
      const hasError = options.parseErr != null ? options.parseErr : state.parseErr;
      const streamText = options.streamText != null ? options.streamText : state.streamText;
      const progress = options.progress || state.previewProgress || calcExtractionProgress(streamText || '', !isPreviewing && !hasError);
      const pct = Math.max(0, Math.min(100, Math.round(progress.percent || 0)));
      const filled = progress.fields.filter(f => f.filled).length;
      const current = progress.fields.find(f => f.key === progress.activeKey);
      const done = !!progress.complete || (!isPreviewing && !hasError);
      const status = hasError ? '解析失败' : (done ? '模型输出已结束' : `正在抽取${current ? current.label : '字段'}…`);
      const sub = done ? `${filled}/${progress.fields.length} 个字段已识别 · 已完成解析` : `${filled}/${progress.fields.length} 个字段已识别 · 基于模型流式输出实时计算`;
      const cls = options.compact ? 'extract-progress compact' : 'extract-progress';
      return `
        <div class="${cls}" aria-live="polite">
          <div class="extract-meter-row">
            <div>
              <div class="extract-title">${escapeHtml(status)}</div>
              <div class="extract-sub">${sub}</div>
            </div>
            <div class="extract-percent mono">${pct}%</div>
          </div>
          <div class="extract-track" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100" aria-label="字段抽取进度">
            <div class="extract-fill" style="width:${pct}%"></div>
          </div>
          <div class="extract-fields">
            ${progress.fields.map(f => {
              const cls = f.filled ? 'done' : (f.key === progress.activeKey && isPreviewing ? 'active' : '');
              return `<div class="extract-field ${cls}"><span class="extract-check">${f.filled ? iconCheck() : ''}</span><span>${escapeHtml(f.label)}</span></div>`;
            }).join('')}
          </div>
        </div>`;
    }

    function renderIngestMain() {
      if (state.batchActive) return renderBatchMain();
      if (state.step === 1) {
        return `
          <section class="card">
            <div class="card-head"><div><div class="kicker">STEP 01 · PASTE</div><h3>粘贴原始排查记录</h3></div><span class="badge mono">不落库</span></div>
            <div class="card-pad">
              <p class="muted" style="font-size:12.5px;margin:0 0 11px;line-height:1.55">把工单 / 对话 / 笔记原文粘进来,模型会流式抽取成结构化案例。此步不写入,确认后才入库。</p>
              <textarea id="rawInput" class="field mono" spellcheck="false" placeholder="例:大促高峰 order-service 一批接口疯狂 500,日志一直刷 HikariPool-1 - Connection is not available, request timed out after 30007ms……" style="height:208px;font-size:12.5px;line-height:1.62">${escapeHtml(state.rawInput)}</textarea>
              <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:13px;flex-wrap:wrap">
                <div style="display:flex;align-items:center;gap:8px">
                  <button class="btn sm ghost" id="loadSample" type="button">${iconFile()}载入示例</button>
                  <button class="btn sm ghost" id="batchPick" type="button">${iconUpload()}上传 Markdown(批量)</button>
                  <input id="batchFile" type="file" accept=".md,.markdown,.txt" style="display:none">
                </div>
                <div style="display:flex;align-items:center;gap:13px">
                  <span class="mono muted" style="font-size:11px">${state.rawInput ? state.rawInput.length + ' 字' : ''}</span>
                  <button class="btn primary" id="doPreview" type="button">${iconSpark()}解析抽取</button>
                </div>
              </div>
              <p class="muted" style="font-size:11.5px;margin:11px 0 0;line-height:1.5">批量:上传一个含多条记录的 Markdown,系统会按一级标题 <code class="mono"># 标题</code> 切分内容,模型并行抽取,可逐条或一次性入库。</p>
            </div>
          </section>`;
      }
      if (state.step === 2) {
        return `
          <section class="card">
            <div class="card-head">
              <div><div class="kicker">STEP 02 · MODEL EXTRACTION</div><h3>模型流式抽取</h3></div>
              ${state.previewing ? '<span class="badge info"><span class="dot pulse"></span>生成中</span>' : '<span class="badge ok"><span class="dot"></span>已完成</span>'}
            </div>
            <div class="card-pad">
              ${renderExtractionProgress()}
              ${state.parseErr ? `
                <div class="result-block warn" style="border-left-color:var(--danger);background:var(--danger-soft);margin-top:13px">
                  <div style="font-size:13px;font-weight:650;color:var(--danger)">解析失败</div>
                  <div class="mono" style="font-size:11.5px;color:var(--text-dim);margin-top:6px;white-space:pre-wrap;word-break:break-word">${escapeHtml(state.parseErr)}</div>
                </div>
                <div style="display:flex;gap:10px;margin-top:13px;flex-wrap:wrap">
                  <button class="btn" id="backToPaste" type="button">${iconBack()}返回修改原文</button>
                  <button class="btn primary" id="retryPreview" type="button">重试</button>
                </div>` : ''}
            </div>
          </section>`;
      }
      if (state.step === 3) return renderReview();
      return renderDone();
    }

    function renderReview(options = {}) {
      const d = state.draft || {};
      const listMode = !!options.listMode;
      const title = listMode ? '编辑知识详情,确认后更新入库' : '复核抽取结果,确认无误后入库';
      const kicker = listMode ? 'KNOWLEDGE DETAIL · EDIT' : 'STEP 03 · HUMAN REVIEW';
      const badge = listMode ? '<span class="badge info">编辑中</span>' : '<span class="badge warn">待确认</span>';
      const saveBusy = listMode ? state.knowledgeSaving : state.committing;
      const saveText = listMode ? '确认更新' : '确认入库';
      return `
        <section class="card">
          <div class="card-head"><div><div class="kicker">${kicker}</div><h3>${title}</h3>${listMode ? `<div class="mono muted" style="font-size:11px;margin-top:6px;word-break:break-all">${escapeHtml(d.file || state.knowledgeSelected)}</div>` : ''}</div>${badge}</div>
          <div class="card-pad" style="display:grid;gap:14px">
            <label class="lbl"><span>标题</span><input id="title" class="field" value="${escapeHtml(d.title || '')}"></label>
            <div class="two">
              <label class="lbl"><span>类别</span><input id="category" class="field" value="${escapeHtml(d.category || '')}"></label>
              <label class="lbl"><span>${listMode ? '知识标识' : '工单号'}</span><input id="ident" class="field mono" value="${escapeHtml(d.ident || '')}" placeholder="${listMode ? '由文件名生成,仅作展示' : '可选,留空用时间戳'}" ${listMode ? 'disabled' : ''}></label>
            </div>
            <div class="lbl">
              <span>signatures <span class="mono" style="color:var(--text-faint);font-weight:400;text-transform:none;letter-spacing:0">报错原文 · 勿改写</span></span>
              <div style="display:grid;gap:8px">
                ${(d.signatures || ['']).map((v, i) => `
                  <div style="display:flex;gap:8px">
                    <input class="field mono sig" data-index="${i}" style="font-size:12px" value="${escapeHtml(v)}" placeholder="报错原文 / 异常类全名 / 错误码">
                    <button class="btn icon del-sig" data-index="${i}" type="button" title="删除">${iconTrash()}</button>
                  </div>`).join('')}
              </div>
              <button class="btn sm" id="addSig" type="button" style="justify-self:start;margin-top:2px">${iconPlus()}添加 signature</button>
            </div>
            <div class="lbl">
              <span>组件</span>
              <div class="two" style="gap:8px">
                ${(d.components || ['']).map((v, i) => `
                  <div style="display:flex;gap:6px">
                    <input class="field mono comp" data-index="${i}" style="font-size:12px" value="${escapeHtml(v)}" placeholder="服务 / 组件名">
                    <button class="btn icon del-comp" data-index="${i}" type="button" title="删除">${iconTrash()}</button>
                  </div>`).join('')}
              </div>
              <button class="btn sm" id="addComp" type="button" style="justify-self:start;margin-top:2px">${iconPlus()}添加组件</button>
            </div>
            <div class="two">
              <label class="lbl"><span>问题背景</span><textarea id="background" class="field" style="height:96px">${escapeHtml(d.background || '')}</textarea></label>
              <label class="lbl"><span>定位过程</span><textarea id="diagnosis" class="field" style="height:96px">${escapeHtml(d.diagnosis || '')}</textarea></label>
            </div>
            <label class="lbl"><span>解决方案</span><textarea id="solution" class="field" style="height:88px">${escapeHtml(d.solution || '')}</textarea></label>
            <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;padding-top:14px;border-top:1px solid var(--line-faint);flex-wrap:wrap">
              ${listMode ? `<button class="btn" id="reloadKnowledgeDetail" type="button">${iconBack()}放弃修改</button>` : `<button class="btn" id="backToPaste" type="button">${iconBack()}上一步:原文</button>`}
              <button class="btn primary" id="commit" type="button" ${saveBusy ? 'disabled' : ''}>${saveBusy ? iconSpin() : iconCheck()}${saveText}</button>
            </div>
          </div>
        </section>`;
    }

    function renderDone() {
      const c = state.committed || {};
      return `
        <section class="card">
          <div class="card-pad" style="display:grid;gap:18px;justify-items:center;text-align:center;padding:38px 24px">
            <div style="width:56px;height:56px;border-radius:999px;display:grid;place-items:center;background:var(--success-soft);color:var(--success)">${iconCheck()}</div>
            <div>
              <div style="font-size:18px;font-weight:700">已写入本地知识库</div>
              <div class="muted" style="font-size:12.5px;margin-top:5px">结构化案例与原文存档均已落库,可立即被检索命中。</div>
            </div>
            <div style="display:grid;gap:8px;width:100%;max-width:440px;text-align:left">
              <div style="display:flex;align-items:center;gap:10px;padding:11px 14px;border-radius:var(--r-md);background:var(--surface-inset);border:1px solid var(--line-faint)"><span class="kicker">CASE</span><code class="mono" style="font-size:12px;color:var(--accent-deep);word-break:break-all">${escapeHtml(c.case_file || '')}</code></div>
              <div style="display:flex;align-items:center;gap:10px;padding:11px 14px;border-radius:var(--r-md);background:var(--surface-inset);border:1px solid var(--line-faint)"><span class="kicker">RAW</span><code class="mono" style="font-size:12px;color:var(--text-mute);word-break:break-all">${escapeHtml(c.raw_file || '')}</code></div>
            </div>
            <div style="display:flex;gap:10px;flex-wrap:wrap;justify-content:center">
              <button class="btn sm primary" id="resetIngest" type="button">${iconPlus()}再写入一条</button>
              <button class="btn sm" id="goQuery" type="button">${iconSearch()}去检索</button>
            </div>
          </div>
        </section>`;
    }
