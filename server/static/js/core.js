    const SAMPLE_RAW_FALLBACK = '大促高峰 order-service 一批接口疯狂 500,日志一直刷 HikariPool-1 - Connection is not available, request timed out after 30007ms。DB CPU 不高,但活跃连接数顶满 maximumPoolSize 设为 20。排查发现慢查询 getOrderDetail 平均 4.2s 长时间占用连接,池一被占满后续请求等待 30s 超时即报错。最终给 getOrderDetail 涉及字段加复合索引,查询降到 60ms,并把 maximumPoolSize 调到 40、加上 leakDetectionThreshold 连接泄漏检测,大促期间未再复现。';
    const SAMPLE_CASE_FALLBACK = {
      title: 'HikariPool 连接池耗尽致接口批量 500',
      category: '数据库 / 连接池',
      signatures: ['HikariPool-1 - Connection is not available, request timed out'],
      components: ['order-service', 'HikariCP', 'MySQL'],
      background: '大促高峰期 order-service 接口批量返回 500,DB CPU 不高但活跃连接顶满 maximumPoolSize 设为 20。',
      diagnosis: '慢查询 getOrderDetail 平均 4.2s 长时间占用连接,连接池耗尽后续请求等待 30s 超时,HikariCP 抛 Connection is not available。',
      solution: '为 getOrderDetail 涉及字段加复合索引,查询从 4.2s 降至 60ms;maximumPoolSize 由 20 调至 40,并启用 HikariCP leakDetectionThreshold 连接泄漏检测,大促期间未再出现连接池耗尽。'
    };

    const state = {
      mode: 'ingest',
      step: 1,
      maxStep: 1,
      rawInput: '',
      streamText: '',
      previewing: false,
      parseErr: '',
      draft: null,
      committing: false,
      committed: null,
      batchActive: false,
      batchStage: 'split',          // split(确认切分) | extracting(并行抽取中) | review(复核入库)
      batchRaw: '',
      batchSplit: [],               // 切分后的原文数组,供用户确认
      batchRecords: [],
      batchCommitting: false,
      batchSummary: null,
      logText: '',
      querying: false,
      result: null,
      knowledgeItems: [],
      knowledgeLoading: false,
      knowledgeSelected: '',
      knowledgeSaving: false,
      knowledgeError: '',
      knowledgeDirty: false,
      graph: null,
      graphLoading: false,
      graphFilter: 'all',
      graphSearch: '',
      graphSelected: '',
      graphPositions: {},
      graphDrag: null,
      graphRenderQueued: false,
      graphSuppressClick: false,
      stats: null,
      sample: { raw: SAMPLE_RAW_FALLBACK, case: SAMPLE_CASE_FALLBACK },
      chatSessions: [],
      chatSessionsLoading: false,
      chatActive: '',               // 当前选中的会话 id
      chatMessages: [],
      chatMessagesLoading: false,
      chatInput: '',
      chatStreaming: false,
      chatStreamText: '',           // 流式生成中的临时文本
      chatStreamMeta: null          // 流式生成中的来源信息 {source,mode,refs}
    };

    const root = document.getElementById('root');
    const toastEl = document.getElementById('toast');
    const tabIngest = document.getElementById('tab-ingest');
    const tabList = document.getElementById('tab-list');
    const tabQuery = document.getElementById('tab-query');
    const tabGraph = document.getElementById('tab-graph');
    const tabChat = document.getElementById('tab-chat');

    function iconBook() { return '<svg width="23" height="23" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"></path></svg>'; }
    function iconCheck() { return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"></path></svg>'; }
    function iconInfo() { return '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z"></path><path d="M12 16v-4"></path><path d="M12 8h.01"></path></svg>'; }
    function iconSearch() { return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"></path><path d="M21 21l-4.35-4.35"></path></svg>'; }
    function iconPlus() { return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"></path><path d="M5 12h14"></path></svg>'; }
    function iconBack() { return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"></path></svg>'; }
    function iconTrash() { return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"></path><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"></path></svg>'; }
    function iconClock() { return '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" style="margin-right:3px;vertical-align:-1px"><circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 2"></path></svg>'; }
    function fmtTime(s) {
      if (!s) return '';
      if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;           // 纯日期(created),直接显示
      const d = new Date(s);
      if (isNaN(d.getTime())) return s;
      const p = n => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
    }
    function iconFile() { return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><path d="M14 2v6h6"></path></svg>'; }
    function iconUpload() { return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><path d="M17 8l-5-5-5 5"></path><path d="M12 3v12"></path></svg>'; }
    function iconChevron() { return '<svg class="chev" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"></path></svg>'; }
    function iconSpark() { return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v6m0 8v6"></path><path d="M2 12h6m8 0h6"></path><path d="M5 5l4 4m6 6 4 4"></path><path d="M19 5l-4 4m-6 6-4 4"></path></svg>'; }
    function iconSpin() { return '<svg class="spin" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.2-8.5"></path></svg>'; }

    function escapeHtml(v) {
      return String(v == null ? '' : v)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function showToast(msg) {
      clearTimeout(showToast.timer);
      toastEl.querySelector('span').textContent = msg;
      toastEl.classList.add('show');
      showToast.timer = setTimeout(() => toastEl.classList.remove('show'), 2800);
    }

    function noBackend(status) {
      return [404, 405, 501, 502, 503, 504].includes(status);
    }

    // 样式化确认弹窗,替代原生 confirm;返回 Promise<boolean>
    function confirmModal(opts) {
      const o = opts || {};
      const title = o.title || '确认';
      const message = o.message || '';
      const confirmText = o.confirmText || '确定';
      const cancelText = o.cancelText || '取消';
      const danger = !!o.danger;
      return new Promise(resolve => {
        const mask = document.createElement('div');
        mask.className = 'modal-mask';
        mask.innerHTML =
          '<div class="modal-box" role="dialog" aria-modal="true">' +
            '<div class="modal-head">' +
              (danger ? '<div class="modal-icon">' + iconTrash() + '</div>' : '') +
              '<div class="modal-title">' + escapeHtml(title) + '</div>' +
            '</div>' +
            '<div class="modal-msg">' + escapeHtml(message) + '</div>' +
            '<div class="modal-actions">' +
              '<button class="btn" data-act="cancel">' + escapeHtml(cancelText) + '</button>' +
              '<button class="btn ' + (danger ? 'danger' : 'primary') + '" data-act="ok">' + escapeHtml(confirmText) + '</button>' +
            '</div>' +
          '</div>';
        function close(v) {
          mask.classList.remove('show');
          document.removeEventListener('keydown', onKey);
          setTimeout(() => mask.remove(), 160);
          resolve(v);
        }
        function onKey(e) {
          if (e.key === 'Escape') close(false);
          else if (e.key === 'Enter') close(true);
        }
        mask.addEventListener('click', e => { if (e.target === mask) close(false); });
        mask.querySelector('[data-act="cancel"]').onclick = () => close(false);
        mask.querySelector('[data-act="ok"]').onclick = () => close(true);
        document.addEventListener('keydown', onKey);
        document.body.appendChild(mask);
        requestAnimationFrame(() => mask.classList.add('show'));
        mask.querySelector('[data-act="ok"]').focus();
      });
    }

    // \u5e26\u6587\u672c\u8f93\u5165\u7684\u5f39\u7a97(\u70b9\u8e29\u539f\u56e0\u7528);\u8fd4\u56de Promise<string|null>,\u53d6\u6d88\u4e3a null
    function promptModal(opts) {
      const o = opts || {};
      const title = o.title || '\u8bf7\u8f93\u5165';
      const message = o.message || '';
      const placeholder = o.placeholder || '';
      const confirmText = o.confirmText || '\u63d0\u4ea4';
      const cancelText = o.cancelText || '\u53d6\u6d88';
      const required = !!o.required;
      return new Promise(resolve => {
        const mask = document.createElement('div');
        mask.className = 'modal-mask';
        mask.innerHTML =
          '<div class="modal-box" role="dialog" aria-modal="true">' +
            '<div class="modal-head"><div class="modal-title">' + escapeHtml(title) + '</div></div>' +
            (message ? '<div class="modal-msg">' + escapeHtml(message) + '</div>' : '') +
            '<textarea class="field mono" data-act="input" placeholder="' + escapeHtml(placeholder) + '" style="height:88px;margin-top:4px"></textarea>' +
            '<div class="modal-actions">' +
              '<button class="btn" data-act="cancel">' + escapeHtml(cancelText) + '</button>' +
              '<button class="btn primary" data-act="ok">' + escapeHtml(confirmText) + '</button>' +
            '</div>' +
          '</div>';
        const input = mask.querySelector('[data-act="input"]');
        function close(v) {
          mask.classList.remove('show');
          document.removeEventListener('keydown', onKey);
          setTimeout(() => mask.remove(), 160);
          resolve(v);
        }
        function submit() {
          const v = input.value.trim();
          if (required && !v) { input.focus(); showToast('\u8bf7\u586b\u5199\u539f\u56e0'); return; }
          close(v);
        }
        function onKey(e) {
          if (e.key === 'Escape') close(null);
          else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit();
        }
        mask.addEventListener('click', e => { if (e.target === mask) close(null); });
        mask.querySelector('[data-act="cancel"]').onclick = () => close(null);
        mask.querySelector('[data-act="ok"]').onclick = submit;
        document.addEventListener('keydown', onKey);
        document.body.appendChild(mask);
        requestAnimationFrame(() => mask.classList.add('show'));
        input.focus();
      });
    }

    function slug(s) {
      return (s || 'case').toLowerCase().replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'case';
    }

    function newRequestId() {
      if (window.crypto && crypto.randomUUID) return crypto.randomUUID().slice(0, 12);
      return Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    }

    function clipText(text, size = 500) {
      const s = String(text || '');
      if (s.length <= size * 2) return s;
      return s.slice(0, size) + '\\n...<snip>...\\n' + s.slice(-size);
    }

    function normalizeJsonText(text) {
      let txt = String(text || '').trim();
      txt = txt.replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/i, '').trim();
      if (!txt.startsWith('{')) {
        const first = txt.indexOf('{');
        const last = txt.lastIndexOf('}');
        if (first !== -1 && last > first) txt = txt.slice(first, last + 1).trim();
      }
      return txt;
    }

    function logPreviewFailure(info) {
      console.error('[log-wiki] ingest preview parse failed', {
        requestId: info.requestId,
        httpStatus: info.httpStatus,
        rawLength: info.rawLength,
        streamLength: info.streamText.length,
        normalizedLength: info.normalizedText.length,
        message: info.error && info.error.message || String(info.error),
        streamSample: clipText(info.streamText),
        normalizedSample: clipText(info.normalizedText)
      });
    }

    function toDraft(o, raw) {
      // 模型偶尔把案例包成 [{...}] 或 {key:[{...}]};归一化为单个对象
      if (o && !Array.isArray(o) && Object.keys(o).length === 1 && Array.isArray(o[Object.keys(o)[0]])) o = o[Object.keys(o)[0]];
      if (Array.isArray(o)) o = o.find(x => x && typeof x === 'object' && !Array.isArray(x)) || {};
      o = o || {};
      return {
        file: o.file || '',
        title: o.title || '',
        category: o.category || '未分类',
        signatures: o.signatures && o.signatures.length ? o.signatures.slice() : [''],
        components: o.components && o.components.length ? o.components.slice() : [''],
        background: o.background || '',
        diagnosis: o.diagnosis || '',
        solution: o.solution || '',
        ident: '',
        raw
      };
    }

    function setMode(mode) {
      state.mode = mode;
      tabIngest.classList.toggle('on', mode === 'ingest');
      tabList.classList.toggle('on', mode === 'list');
      tabQuery.classList.toggle('on', mode === 'query');
      tabGraph.classList.toggle('on', mode === 'graph');
      tabChat.classList.toggle('on', mode === 'chat');
      if (mode === 'graph' && !state.graph && !state.graphLoading) loadGraph();
      if (mode === 'chat' && !state.chatSessions.length && !state.chatSessionsLoading) loadChatSessions();
      // 进入列表页:首次为空、或刚入库过(dirty)都自动刷新,无需手点「刷新」
      if (mode === 'list' && !state.knowledgeLoading &&
          (!state.knowledgeItems.length || state.knowledgeDirty)) {
        state.knowledgeDirty = false;
        loadKnowledgeList(true);
      }
      render();
    }

    function setStep(step) {
      if (step <= state.maxStep) {
        state.step = step;
        render();
      }
    }

    function goToStep(step) {
      state.step = step;
      state.maxStep = Math.max(state.maxStep, step);
    }

    async function refreshMeta() {
      try {
        const [statsResp, sampleResp] = await Promise.all([
          fetch('/api/kb/stats'),
          fetch('/api/examples/ingest')
        ]);
        if (statsResp.ok) state.stats = await statsResp.json();
        if (sampleResp.ok) state.sample = await sampleResp.json();
      } catch (e) {}
      render();
    }

    function updateDraft(key, value) {
      state.draft[key] = value;
    }

    function updateList(key, index, value) {
      state.draft[key][index] = value;
    }

    function addList(key) {
      state.draft[key].push('');
      render();
    }

    function removeList(key, index) {
      state.draft[key].splice(index, 1);
      if (!state.draft[key].length) state.draft[key].push('');
      render();
    }

    function stepRows() {
      const defs = [
        { n: 1, label: '粘贴原文', meta: state.rawInput ? state.rawInput.length + ' 字 · 已读取' : '等待粘贴' },
        { n: 2, label: '模型抽取', meta: state.previewing ? '流式生成中…' : (state.parseErr ? '解析失败' : (state.streamText ? 'JSON · 已完成' : '等待解析')) },
        { n: 3, label: '人工复核', meta: state.draft ? '可编辑核对' : '等待抽取' },
        { n: 4, label: '确认入库', meta: state.committed ? '已写入' : '待入库' }
      ];
      return defs.map((d, idx) => {
        const cls = ['step'];
        if (d.n < state.step) cls.push('done', 'click');
        if (d.n === state.step) cls.push('active', 'click');
        if (d.n > state.step && d.n <= state.maxStep) cls.push('click');
        return `
          <div class="${cls.join(' ')}" data-step="${d.n}">
            <div class="step-mark">
              <div class="step-dot">${d.n < state.step ? iconCheck() : d.n}</div>
              ${idx < defs.length - 1 ? '<div class="step-line"></div>' : ''}
            </div>
            <div class="step-copy">
              <div class="step-label">${escapeHtml(d.label)}</div>
              <div class="mono muted" style="font-size:10.5px;margin-top:2px">${escapeHtml(d.meta)}</div>
            </div>
          </div>`;
      }).join('');
    }

    function statsHtml() {
      const s = state.stats;
      if (!s) return '<div class="mono muted" style="font-size:11px;margin-top:12px">统计加载中...</div>';
      return `
        <div style="margin-top:14px;padding-top:14px;border-top:1px solid var(--line-faint);display:grid;grid-template-columns:1fr 1fr;gap:8px">
          <div><div class="kicker">CASES</div><strong>${s.cases}</strong></div>
          <div><div class="kicker">SIGN</div><strong>${s.signatures}</strong></div>
          <div><div class="kicker">VERIFIED</div><strong>${s.verified}</strong></div>
          <div><div class="kicker">DRAFT</div><strong>${s.drafts}</strong></div>
        </div>`;
    }

    function renderRail() {
      if (state.mode === 'list') {
        return `
          <aside class="card rail">
            <div class="kicker" style="margin-bottom:14px">KNOWLEDGE LIST</div>
            <div style="display:grid;gap:12px">
              ${modeNote('var(--accent)', '左侧列表', '点击任一知识后,右侧展示可编辑详情。')}
              ${modeNote('var(--success)', '更新入库', '保存会覆盖对应案例 Markdown,并刷新索引。')}
              ${modeNote('var(--warning)', '检索锚点', 'signatures 仍需保留报错原文。')}
            </div>
            ${statsHtml()}
          </aside>`;
      }
      if (state.mode === 'graph') {
        const graph = state.graph || { nodes: [], edges: [] };
        return `
          <aside class="card rail">
            <div class="kicker" style="margin-bottom:14px">GRAPH FILTERS</div>
            <label class="lbl" style="margin-bottom:12px"><span>搜索节点</span><input id="graphSearch" class="field" value="${escapeHtml(state.graphSearch)}" placeholder="title / tag / component"></label>
            <div class="seg" style="display:grid;grid-template-columns:1fr 1fr;margin-bottom:14px">
              ${['all','case','concept','neighbors'].map(v => `<button class="${state.graphFilter === v ? 'on' : ''}" data-graph-filter="${v}" type="button">${{all:'全部',case:'案例',concept:'概念',neighbors:'一跳'}[v]}</button>`).join('')}
            </div>
            <div class="kicker" style="margin:14px 0 10px">LEGEND</div>
            <div style="display:grid;gap:9px">
              ${[
                ['case','Case'],
                ['concept','Concept'],
                ['raw','Raw Source'],
                ['component','Component'],
                ['tag','Tag']
              ].map(([type,label]) => `<div style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-dim)"><span style="width:9px;height:9px;border-radius:999px;background:${graphColor(type)}"></span>${label}</div>`).join('')}
            </div>
            <div style="margin-top:14px;padding-top:14px;border-top:1px solid var(--line-faint);display:grid;grid-template-columns:1fr 1fr;gap:8px">
              <div><div class="kicker">NODES</div><strong>${graph.nodes.length}</strong></div>
              <div><div class="kicker">EDGES</div><strong>${graph.edges.length}</strong></div>
            </div>
          </aside>`;
      }
      if (state.mode === 'query') {
        return `
          <aside class="card rail">
            <div class="kicker" style="margin-bottom:14px">MATCH MODES</div>
            <div style="display:grid;gap:14px">
              ${modeNote('var(--success)', '精确命中', 'signature 原文匹配,可直接照方案处理。')}
              ${modeNote('var(--warning)', '可能相关', 'BM25 全文检索召回,按相关度排序,需人工判断,勿照搬。')}
              ${modeNote('var(--text-faint)', '暂无案例', '勿编造方案;排查后到写入页入库。')}
            </div>
            ${statsHtml()}
          </aside>`;
      }
      if (state.batchActive) {
        const recs = state.batchRecords;
        const total = state.batchStage === 'review' ? recs.length : state.batchSplit.length;
        const done = recs.filter(r => r.status === 'committed').length;
        const failed = recs.filter(r => r.status === 'failed').length;
        const stageLabel = { split: '① 确认切分', extracting: '② 并行抽取中', review: '③ 复核入库' }[state.batchStage] || '';
        return `
          <aside class="card rail">
            <div class="kicker" style="margin-bottom:14px">BATCH INGEST</div>
            <div class="badge mono" style="margin-bottom:14px">${stageLabel}</div>
            <div style="display:grid;gap:12px">
              ${modeNote('var(--accent)', '确认切分', '先按 Markdown 一级标题 # 切分并展示各条原文,确认无误后再抽取。')}
              ${modeNote('var(--success)', '逐条/批量', '抽取后可对每条单独入库,也可一次性全部入库。')}
              ${modeNote('var(--warning)', '需补全', '抽取失败的记录会标红展开,补全标题与 signatures 后即可入库。')}
            </div>
            <div style="margin-top:14px;padding-top:14px;border-top:1px solid var(--line-faint);display:grid;grid-template-columns:1fr 1fr;gap:8px">
              <div><div class="kicker">TOTAL</div><strong>${total}</strong></div>
              <div><div class="kicker">DONE</div><strong>${done}</strong></div>
              <div><div class="kicker">FAILED</div><strong>${failed}</strong></div>
              <div><div class="kicker">LEFT</div><strong>${total - done}</strong></div>
            </div>
          </aside>`;
      }
      return `
        <aside class="card rail">
          <div class="kicker" style="margin-bottom:16px">INGEST PIPELINE</div>
          <div>${stepRows()}</div>
          <div style="margin-top:4px;padding-top:16px;border-top:1px solid var(--line-faint);display:flex;gap:7px;align-items:flex-start">
            <span style="width:8px;height:8px;border-radius:999px;background:var(--warning);margin-top:5px;flex:none"></span>
            <span style="font-size:11.5px;color:var(--text-mute);line-height:1.5">signatures 请保持报错原文,勿改写或脱敏。</span>
          </div>
          ${statsHtml()}
        </aside>`;
    }

    function modeNote(color, title, text) {
      return `<div style="display:flex;gap:10px"><span style="width:8px;height:8px;border-radius:999px;background:${color};flex:none;margin-top:5px"></span><div><div style="font-size:13px;font-weight:650;color:var(--text)">${title}</div><div class="muted" style="font-size:11.5px;line-height:1.5;margin-top:2px">${text}</div></div></div>`;
    }

    function bindEvents() {
      tabIngest.onclick = () => setMode('ingest');
      tabList.onclick = () => setMode('list');
      tabQuery.onclick = () => setMode('query');
      tabGraph.onclick = () => setMode('graph');
      tabChat.onclick = () => setMode('chat');
      if (state.mode === 'chat' && typeof bindChatEvents === 'function') bindChatEvents();
      root.querySelectorAll('.step.click').forEach(el => el.onclick = () => setStep(Number(el.dataset.step)));

      const rawInput = document.getElementById('rawInput');
      if (rawInput) rawInput.oninput = e => { state.rawInput = e.target.value; };
      const logText = document.getElementById('logText');
      if (logText) logText.oninput = e => { state.logText = e.target.value; };

      const bind = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
      bind('loadSample', loadSample);
      bind('doPreview', doPreview);
      bind('retryPreview', doPreview);
      bind('batchPick', () => { const f = document.getElementById('batchFile'); if (f) f.click(); });
      const batchFile = document.getElementById('batchFile');
      if (batchFile) batchFile.onchange = e => onBatchFile(e.target.files && e.target.files[0]);
      if (state.batchActive && typeof bindBatchEvents === 'function') bindBatchEvents();
      bind('backToPaste', () => { state.step = 1; state.parseErr = ''; render(); });
      bind('addSig', () => addList('signatures'));
      bind('addComp', () => addList('components'));
      bind('commit', commit);
      bind('resetIngest', resetIngest);
      bind('goQuery', () => setMode('query'));
      bind('runQuery', runQuery);
      bind('reloadKnowledge', () => loadKnowledgeList(false));
      bind('clearKnowledge', clearAllKnowledge);
      bind('reloadKnowledgeDetail', () => selectKnowledge(state.knowledgeSelected));
      bind('reloadGraph', loadGraph);
      bind('resetGraphLayout', resetGraphLayout);
      root.querySelectorAll('[data-knowledge-file]').forEach(el => el.onclick = e => selectKnowledge(e.currentTarget.dataset.knowledgeFile));
      root.querySelectorAll('[data-del-file]').forEach(el => el.onclick = e => {
        e.stopPropagation();   // 阻止冒泡到外层 item 触发选中
        deleteKnowledge(e.currentTarget.dataset.delFile, e.currentTarget.dataset.delTitle);
      });

      const graphSearch = document.getElementById('graphSearch');
      if (graphSearch) graphSearch.oninput = e => { state.graphSearch = e.target.value; render(); };
      root.querySelectorAll('[data-graph-filter]').forEach(el => el.onclick = e => { state.graphFilter = e.currentTarget.dataset.graphFilter; render(); });
      root.querySelectorAll('.graph-node').forEach(el => {
        el.onpointerdown = startGraphDrag;
        el.onmousedown = startGraphDrag;
        el.onclick = e => {
          if (state.graphSuppressClick) {
            state.graphSuppressClick = false;
            return;
          }
          state.graphSelected = e.currentTarget.dataset.nodeId;
          render();
        };
      });
      root.querySelectorAll('.graph-list [data-node-id]').forEach(el => el.onclick = e => { state.graphSelected = e.currentTarget.dataset.nodeId; render(); });

      if (state.draft) {
        ['title', 'category', 'ident', 'background', 'diagnosis', 'solution'].forEach(key => {
          const el = document.getElementById(key);
          if (el) el.oninput = e => updateDraft(key, e.target.value);
        });
        root.querySelectorAll('.sig').forEach(el => el.oninput = e => updateList('signatures', Number(e.target.dataset.index), e.target.value));
        root.querySelectorAll('.comp').forEach(el => el.oninput = e => updateList('components', Number(e.target.dataset.index), e.target.value));
        root.querySelectorAll('.del-sig').forEach(el => el.onclick = e => removeList('signatures', Number(e.currentTarget.dataset.index)));
        root.querySelectorAll('.del-comp').forEach(el => el.onclick = e => removeList('components', Number(e.currentTarget.dataset.index)));
      }
    }

    function render() {
      let main;
      if (state.mode === 'ingest') main = renderIngestMain();
      else if (state.mode === 'list') main = renderKnowledgeMain();
      else if (state.mode === 'query') main = renderQueryMain();
      else if (state.mode === 'chat') main = renderChatMain();
      else main = renderGraphMain();
      // list / chat 是全宽双栏布局,不带左侧 rail
      root.innerHTML = (state.mode === 'list' || state.mode === 'chat')
        ? `<div class="main">${main}</div>`
        : `
          <div class="grid">
            ${renderRail()}
            <div class="main">${main}</div>
          </div>`;
      bindEvents();
      if (state.mode === 'chat' && typeof afterChatRender === 'function') afterChatRender();
    }
