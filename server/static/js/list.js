    async function loadKnowledgeList(keepSelection = true) {
      state.knowledgeLoading = true;
      state.knowledgeError = '';
      render();
      try {
        const r = await fetch('/api/knowledge');
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const data = await r.json();
        state.knowledgeItems = data.items || [];
        if (!keepSelection || !state.knowledgeItems.some(item => item.file === state.knowledgeSelected)) {
          state.knowledgeSelected = state.knowledgeItems[0] && state.knowledgeItems[0].file || '';
        }
        state.knowledgeLoading = false;
        render();
        if (state.knowledgeSelected) await selectKnowledge(state.knowledgeSelected);
      } catch (e) {
        state.knowledgeLoading = false;
        state.knowledgeError = String(e && e.message || e);
        render();
        showToast('知识列表加载失败');
      }
    }

    async function selectKnowledge(file) {
      if (!file) return;
      state.knowledgeSelected = file;
      state.knowledgeError = '';
      render();
      try {
        const r = await fetch('/api/knowledge/' + file.split('/').map(encodeURIComponent).join('/'));
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || '加载知识失败');
        state.draft = toDraft(data, data.raw || '');
        state.draft.file = data.file;
        render();
      } catch (e) {
        state.knowledgeError = String(e && e.message || e);
        render();
        showToast('知识详情加载失败');
      }
    }

    async function deleteKnowledge(file, title) {
      if (!file) return;
      const ok = await confirmModal({
        title: '删除知识',
        message: '确定删除「' + (title || file) + '」?\n原始记录 raw/ 仍保留以备溯源,可恢复。',
        confirmText: '删除', cancelText: '取消', danger: true,
      });
      if (!ok) return;
      try {
        const r = await fetch('/api/knowledge/' + file.split('/').map(encodeURIComponent).join('/'), { method: 'DELETE' });
        if (noBackend(r.status)) { showToast('后端未连接 · 无法删除'); return; }
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || '删除失败');
        if (state.knowledgeSelected === file) { state.knowledgeSelected = ''; state.draft = null; }
        state.graph = null; state.graphSelected = '';   // 知识变更,图谱缓存失效,下次进图谱页重载
        showToast('已删除');
        await loadKnowledgeList(true);
        refreshMeta();
      } catch (e) {
        showToast(String(e && e.message || e));
      }
    }

    async function clearAllKnowledge() {
      const n = state.knowledgeItems.length;
      if (!n) return;
      const ok = await confirmModal({
        title: '清空全部知识',
        message: '确定删除全部 ' + n + ' 条知识?此操作不可撤销。\n原始记录 raw/ 仍保留以备溯源,可重新入库。',
        confirmText: '全部删除', cancelText: '取消', danger: true,
      });
      if (!ok) return;
      try {
        const r = await fetch('/api/knowledge', { method: 'DELETE' });
        if (noBackend(r.status)) { showToast('后端未连接 · 无法清空'); return; }
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || '清空失败');
        state.knowledgeSelected = ''; state.draft = null;
        state.graph = null; state.graphSelected = '';   // 知识全清,图谱缓存失效
        showToast('已清空 ' + (data.deleted || n) + ' 条知识');
        await loadKnowledgeList(false);
        refreshMeta();
      } catch (e) {
        showToast(String(e && e.message || e));
      }
    }

    async function updateKnowledge() {
      const d = state.draft;
      if (!d || !state.knowledgeSelected) return;
      const payload = {
        raw: d.raw || '',
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
      state.knowledgeSaving = true;
      render();
      try {
        const r = await fetch('/api/knowledge/' + state.knowledgeSelected.split('/').map(encodeURIComponent).join('/'), {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || '更新失败');
        state.knowledgeSaving = false;
        state.graph = null; state.graphSelected = '';   // 知识变更,图谱缓存失效
        await loadKnowledgeList(true);
        refreshMeta();
        showToast('知识已更新入库');
      } catch (e) {
        state.knowledgeSaving = false;
        render();
        showToast(String(e && e.message || e));
      }
    }

    function renderKnowledgeMain() {
      return `
        <section class="knowledge-layout">
          <aside class="card">
            <div class="card-head" style="align-items:center">
              <div style="min-width:0"><div class="kicker" style="overflow:hidden;text-overflow:ellipsis">KNOWLEDGE · VERIFIED</div><h3>知识列表</h3></div>
              <div style="display:flex;gap:8px;align-items:center;flex:none;flex-wrap:nowrap">
                ${state.knowledgeItems.length ? `<button class="btn sm danger" id="clearKnowledge" type="button" title="删除全部知识">${iconTrash()}清空</button>` : ''}
                <button class="btn sm" id="reloadKnowledge" type="button">${state.knowledgeLoading ? iconSpin() : iconSearch()}刷新</button>
              </div>
            </div>
            <div class="card-pad">
              ${state.knowledgeError ? `<div class="result-block warn" style="margin-bottom:12px">${escapeHtml(state.knowledgeError)}</div>` : ''}
              <div class="knowledge-list">
                ${state.knowledgeLoading && !state.knowledgeItems.length ? `<div class="empty">${iconSpin()}<div>知识加载中</div></div>` : ''}
                ${!state.knowledgeLoading && !state.knowledgeItems.length ? `<div class="empty">${iconInfo()}<div>暂无已入库知识</div></div>` : ''}
                ${state.knowledgeItems.map(item => `
                  <div class="knowledge-item ${state.knowledgeSelected === item.file ? 'on' : ''}" data-knowledge-file="${escapeHtml(item.file)}">
                    <span class="knowledge-title">${escapeHtml(item.title)}</span>
                    <span class="knowledge-meta">
                      <span class="graph-chip">${escapeHtml(item.category || '未分类')}</span>
                      <span class="mono" title="入库时间">${iconClock()}${escapeHtml(fmtTime(item.timestamp || item.created) || '—')}</span>
                    </span>
                    <button class="knowledge-del" type="button" data-del-file="${escapeHtml(item.file)}" data-del-title="${escapeHtml(item.title)}" title="删除此知识">${iconTrash()}</button>
                  </div>`).join('')}
              </div>
            </div>
          </aside>
          <div>
            ${state.draft && state.knowledgeSelected ? renderReview({ listMode: true }) : `<section class="card"><div class="card-pad empty">${iconInfo()}<div>请选择左侧知识</div></div></section>`}
          </div>
        </section>`;
    }

