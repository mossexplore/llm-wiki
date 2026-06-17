    // ============================ 对话 Agent 页面 ============================
    // 左侧会话列表 + 右侧聊天区(参考 NextChat)。回答先检索 wiki,命中则流式回 wiki
    // 答案并标注来源;否则流式调用大模型。所有会话/消息/反馈都落库(见后端)。

    function iconChat() { return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>'; }
    function iconSend() { return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"></path><path d="M22 2 15 22l-4-9-9-4 20-7z"></path></svg>'; }
    function iconCopy() { return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>'; }
    function iconUp() { return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10v12"></path><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"></path></svg>'; }
    function iconDown() { return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M17 14V2"></path><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"></path></svg>'; }

    // 行内格式:**粗体** / *斜体* / `行内代码`(已在外层做过 HTML 转义)
    function mdInline(s) {
      return s
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
        .replace(/`([^`]+)`/g, '<code>$1</code>');
    }

    // 轻量 Markdown 渲染:支持围栏代码块 ``` / 标题 # ## ### / 引用 > / 有序无序列表 /
    // 行内粗体斜体代码。先整体转义再逐行解析,流式期间未闭合的代码块也会按代码块展示。
    function renderMarkdown(text) {
      const lines = escapeHtml(text || '').split('\n');
      const out = [];
      let listType = null;     // 'ul' | 'ol' | null
      let para = [];           // 连续普通文本行缓冲
      const closeList = () => { if (listType) { out.push('</' + listType + '>'); listType = null; } };
      const flushPara = () => { if (para.length) { out.push('<div class="md-p">' + para.join('<br>') + '</div>'); para = []; } };
      const flushBlocks = () => { flushPara(); closeList(); };

      let i = 0;
      while (i < lines.length) {
        const line = lines[i];

        // 围栏代码块:```lang ... ```(流式未闭合时收集到结尾)
        const fence = line.match(/^\s*```(.*)$/);
        if (fence) {
          flushBlocks();
          const code = [];
          i++;
          while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { code.push(lines[i]); i++; }
          if (i < lines.length) i++;   // 跳过闭合的 ```
          out.push('<pre class="md-code"><code>' + code.join('\n') + '</code></pre>');
          continue;
        }

        let m;
        if ((m = line.match(/^\s*(#{1,6})\s+(.*)$/))) {            // 标题
          flushBlocks();
          out.push('<div class="md-h">' + mdInline(m[2]) + '</div>');
        } else if (/^\s*&gt;\s?/.test(line)) {                     // 引用
          flushBlocks();
          out.push('<div class="md-quote">' + mdInline(line.replace(/^\s*&gt;\s?/, '')) + '</div>');
        } else if ((m = line.match(/^\s*[-*+]\s+(.*)$/))) {        // 无序列表
          flushPara();
          if (listType !== 'ul') { closeList(); out.push('<ul class="md-list">'); listType = 'ul'; }
          out.push('<li>' + mdInline(m[1]) + '</li>');
        } else if ((m = line.match(/^\s*\d+\.\s+(.*)$/))) {        // 有序列表
          flushPara();
          if (listType !== 'ol') { closeList(); out.push('<ol class="md-list">'); listType = 'ol'; }
          out.push('<li>' + mdInline(m[1]) + '</li>');
        } else if (/^\s*$/.test(line)) {                           // 空行 → 段落分隔
          flushBlocks();
        } else {                                                   // 普通文本行
          closeList();
          para.push(mdInline(line));
        }
        i++;
      }
      flushBlocks();
      return out.join('');
    }

    async function loadChatSessions(selectFirst = true) {
      state.chatSessionsLoading = true;
      render();
      try {
        const r = await fetch('/api/chat/sessions');
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const data = await r.json();
        state.chatSessions = data.items || [];
        state.chatSessionsLoading = false;
        if (selectFirst && !state.chatActive && state.chatSessions.length) {
          await selectChatSession(state.chatSessions[0].id);
        } else {
          render();
        }
      } catch (e) {
        state.chatSessionsLoading = false;
        render();
        if (!noBackend(e.status)) showToast('会话列表加载失败');
      }
    }

    async function newChatSession() {
      try {
        const r = await fetch('/api/chat/sessions', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: '新会话' })
        });
        if (noBackend(r.status)) { showToast('后端未连接 · 无法新建会话'); return; }
        const s = await r.json();
        if (!r.ok) throw new Error(s.detail || '新建失败');
        state.chatSessions.unshift(s);
        state.chatActive = s.id;
        state.chatMessages = [];
        state.chatStreamText = '';
        state.chatStreamMeta = null;
        render();
        const ta = document.getElementById('chatInput');
        if (ta) ta.focus();
      } catch (e) { showToast(String(e && e.message || e)); }
    }

    async function selectChatSession(id) {
      if (!id) return;
      state.chatActive = id;
      state.chatMessagesLoading = true;
      state.chatStreamText = '';
      state.chatStreamMeta = null;
      render();
      try {
        const r = await fetch('/api/chat/sessions/' + encodeURIComponent(id) + '/messages');
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || '加载消息失败');
        state.chatMessages = data.items || [];
        state.chatMessagesLoading = false;
        render();
      } catch (e) {
        state.chatMessagesLoading = false;
        render();
        showToast(String(e && e.message || e));
      }
    }

    async function deleteChatSession(id, title) {
      const ok = await confirmModal({
        title: '删除会话',
        message: '确定删除会话「' + (title || '新会话') + '」?该会话的全部对话与反馈记录都会被移除。',
        confirmText: '删除', cancelText: '取消', danger: true,
      });
      if (!ok) return;
      try {
        const r = await fetch('/api/chat/sessions/' + encodeURIComponent(id), { method: 'DELETE' });
        if (noBackend(r.status)) { showToast('后端未连接 · 无法删除'); return; }
        if (!r.ok) { const d = await r.json(); throw new Error(d.detail || '删除失败'); }
        state.chatSessions = state.chatSessions.filter(s => s.id !== id);
        if (state.chatActive === id) {
          state.chatActive = '';
          state.chatMessages = [];
          if (state.chatSessions.length) await selectChatSession(state.chatSessions[0].id);
          else render();
        } else render();
        showToast('已删除会话');
      } catch (e) { showToast(String(e && e.message || e)); }
    }

    async function sendChatMessage() {
      if (state.chatStreaming) return;
      const text = (state.chatInput || '').trim();
      if (!text) { showToast('请输入问题'); return; }
      // 没有会话则先建一个
      if (!state.chatActive) {
        await newChatSession();
        if (!state.chatActive) return;
      }
      const sessionId = state.chatActive;
      state.chatMessages.push({ role: 'user', content: text, id: 'local-' + Date.now() });
      state.chatInput = '';
      state.chatStreaming = true;
      state.chatStreamText = '';
      state.chatStreamMeta = null;
      render();

      try {
        const r = await fetch('/api/chat/sessions/' + encodeURIComponent(sessionId) + '/messages', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: text })
        });
        if (noBackend(r.status)) throw new Error('后端未连接');
        if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || '发送失败'); }
        await consumeChatStream(r);
      } catch (e) {
        state.chatStreaming = false;
        render();
        showToast(String(e && e.message || e));
      }
      // 流结束后刷新会话列表(标题/排序可能变化)
      loadChatSessions(false);
    }

    async function consumeChatStream(resp) {
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let doneMeta = null;
      let errored = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line) continue;
          let ev;
          try { ev = JSON.parse(line); } catch (_) { continue; }
          if (ev.type === 'meta') {
            state.chatStreamMeta = { source: ev.source, mode: ev.mode, refs: ev.refs || [] };
            render();
          } else if (ev.type === 'delta') {
            state.chatStreamText += ev.text || '';
            updateStreamDOM();
          } else if (ev.type === 'done') {
            doneMeta = ev;
          } else if (ev.type === 'error') {
            errored = ev.error || '生成失败';
          }
        }
      }
      state.chatStreaming = false;
      if (errored) {
        showToast('生成出错:' + errored);
        // 仍把已生成内容作为一条回复展示
      }
      if (state.chatStreamText) {
        state.chatMessages.push({
          role: 'assistant',
          content: state.chatStreamText,
          id: doneMeta && doneMeta.message_id || ('local-' + Date.now()),
          answer_source: state.chatStreamMeta && state.chatStreamMeta.source || (doneMeta && doneMeta.source),
          retrieval_mode: state.chatStreamMeta && state.chatStreamMeta.mode || (doneMeta && doneMeta.mode),
          refs: (state.chatStreamMeta && state.chatStreamMeta.refs) || (doneMeta && doneMeta.refs) || [],
        });
      }
      state.chatStreamText = '';
      state.chatStreamMeta = null;
      render();
    }

    // 流式期间只更新生成中气泡的 DOM,避免整页重渲染丢失滚动/输入焦点
    function updateStreamDOM() {
      const el = document.getElementById('chatStreamBody');
      if (el) {
        el.innerHTML = renderMarkdown(state.chatStreamText) + '<span class="caret"></span>';
        scrollChatToBottom();
      }
    }

    async function copyMessage(text) {
      try {
        await navigator.clipboard.writeText(text || '');
        showToast('已复制');
      } catch (e) {
        showToast('复制失败,请手动选择');
      }
    }

    async function chatFeedback(messageId, rating) {
      if (!messageId || String(messageId).startsWith('local-')) {
        showToast('该回复未落库,无法反馈');
        return;
      }
      let reason = null;
      if (rating === 'down') {
        reason = await promptModal({
          title: '反馈:这条回答不太好',
          message: '请告诉我们哪里不对(便于改进知识库与答案质量):',
          placeholder: '例如:解决方案不适用 / 来源不相关 / 有事实错误…',
          confirmText: '提交点踩', required: true,
        });
        if (reason === null) return;     // 用户取消
      }
      try {
        const r = await fetch('/api/chat/messages/' + encodeURIComponent(messageId) + '/feedback', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rating, reason })
        });
        if (noBackend(r.status)) { showToast('后端未连接 · 无法反馈'); return; }
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || '反馈失败');
        const msg = state.chatMessages.find(m => m.id === messageId);
        if (msg) { msg.feedback_rating = rating; msg.feedback_reason = reason; }
        render();
        showToast(rating === 'up' ? '已点赞,谢谢反馈' : '已记录,谢谢反馈');
      } catch (e) { showToast(String(e && e.message || e)); }
    }

    function scrollChatToBottom() {
      const sc = document.getElementById('chatScroll');
      if (sc) sc.scrollTop = sc.scrollHeight;
    }

    // wiki 来源 chip:可点击,点击弹出该知识详情
    function chatRefsHtml(refs) {
      const list = (refs || []).filter(Boolean);
      if (!list.length) return '';
      return '<div class="chat-refs"><span class="chat-refs-label">来源 wiki:</span>' +
        list.map(rf => '<button class="chat-ref mono" type="button" data-wiki-file="' + escapeHtml(rf.file) + '" title="点击查看知识详情:' + escapeHtml(rf.file) + '">' + iconFile() + escapeHtml(rf.title || rf.file) + '</button>').join('') +
        '</div>';
    }

    async function openWikiDetail(file) {
      if (!file) return;
      const mask = wikiModalShell('加载中…', '<div class="empty">' + iconSpin() + '<div>正在加载知识详情</div></div>');
      try {
        const r = await fetch('/api/knowledge/' + file.split('/').map(encodeURIComponent).join('/'));
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || '加载失败');
        wikiModalFill(mask, data);
      } catch (e) {
        wikiModalFill(mask, null, String(e && e.message || e));
      }
    }

    // 构造弹窗外壳(遮罩+可滚动盒子),返回 mask 元素;内容后续填充
    function wikiModalShell(title, bodyHtml) {
      const mask = document.createElement('div');
      mask.className = 'modal-mask';
      mask.innerHTML =
        '<div class="modal-box wiki-modal" role="dialog" aria-modal="true">' +
          '<div class="wiki-modal-head">' +
            '<div class="wiki-modal-title" data-role="title">' + escapeHtml(title) + '</div>' +
            '<button class="wiki-modal-close" type="button" data-act="close" title="关闭">✕</button>' +
          '</div>' +
          '<div class="wiki-modal-body" data-role="body">' + bodyHtml + '</div>' +
        '</div>';
      function close() {
        mask.classList.remove('show');
        document.removeEventListener('keydown', onKey);
        setTimeout(() => mask.remove(), 160);
      }
      function onKey(e) { if (e.key === 'Escape') close(); }
      mask.addEventListener('click', e => { if (e.target === mask) close(); });
      mask.querySelector('[data-act="close"]').onclick = close;
      document.addEventListener('keydown', onKey);
      document.body.appendChild(mask);
      requestAnimationFrame(() => mask.classList.add('show'));
      return mask;
    }

    // 把知识详情数据填进弹窗。读取详情复用 /api/knowledge/{file} 接口,这里只做只读展示。
    function wikiModalFill(mask, d, err) {
      const titleEl = mask.querySelector('[data-role="title"]');
      const bodyEl = mask.querySelector('[data-role="body"]');
      if (err || !d) {
        titleEl.textContent = '加载失败';
        bodyEl.innerHTML = '<div class="result-block warn">' + escapeHtml(err || '无法加载知识详情') + '</div>';
        return;
      }
      titleEl.textContent = d.title || d.file || '知识详情';
      const chips = [];
      if (d.category) chips.push('<span class="wiki-chip">' + escapeHtml(d.category) + '</span>');
      if (d.status) chips.push('<span class="wiki-chip ' + (d.status === 'verified' ? 'ok' : '') + '">' + escapeHtml(d.status) + '</span>');
      if (d.confidence && d.confidence !== 'unknown') chips.push('<span class="wiki-chip">置信度 ' + escapeHtml(d.confidence) + '</span>');
      if (d.updated) chips.push('<span class="wiki-chip mono">' + iconClock() + escapeHtml(fmtTime(d.updated)) + '</span>');

      const sec = (label, content, opts) => {
        const o = opts || {};
        if (!content || (Array.isArray(content) && !content.length)) return '';
        let inner;
        if (o.list) {
          inner = '<div class="wiki-tags">' + content.filter(Boolean).map(v => '<span class="wiki-tag mono">' + escapeHtml(v) + '</span>').join('') + '</div>';
        } else if (o.mono) {
          inner = '<pre class="wiki-pre">' + escapeHtml(content) + '</pre>';
        } else {
          inner = '<div class="wiki-text">' + escapeHtml(content) + '</div>';
        }
        return '<div class="wiki-sec"><div class="wiki-sec-label">' + escapeHtml(label) + '</div>' + inner + '</div>';
      };

      bodyEl.innerHTML =
        (chips.length ? '<div class="wiki-chips">' + chips.join('') + '</div>' : '') +
        (d.description ? '<div class="wiki-desc">' + escapeHtml(d.description) + '</div>' : '') +
        sec('SIGNATURES（检索锚点）', d.signatures, { list: true }) +
        sec('COMPONENTS', d.components, { list: true }) +
        sec('问题背景', d.background) +
        sec('定位过程', d.diagnosis) +
        sec('解决方案', d.solution) +
        sec('文件', d.file, { mono: true });
    }

    function srcBadge(source, mode) {
      if (source === 'wiki') {
        const label = mode === 'exact' ? '来源 wiki · 精确命中' : '来源 wiki · 关联命中';
        return '<span class="src-badge wiki">' + iconBook() + label + '</span>';
      }
      if (source === 'llm') return '<span class="src-badge llm">' + iconSpark() + '大模型回答</span>';
      return '';
    }

    function renderChatMessage(m) {
      if (m.role === 'user') {
        return '<div class="chat-row user"><div class="chat-bubble user">' + escapeHtml(m.content) + '</div></div>';
      }
      const refsHtml = chatRefsHtml(m.refs);
      const fbUp = m.feedback_rating === 'up' ? ' on' : '';
      const fbDown = m.feedback_rating === 'down' ? ' on down' : '';
      const isLocal = String(m.id || '').startsWith('local-');
      return '<div class="chat-row agent">' +
        '<div class="chat-avatar">' + iconChat() + '</div>' +
        '<div class="chat-bubble agent">' +
          '<div class="chat-srcline">' + srcBadge(m.answer_source, m.retrieval_mode) + '</div>' +
          '<div class="chat-md">' + renderMarkdown(m.content) + '</div>' +
          refsHtml +
          (isLocal ? '' :
          '<div class="chat-acts">' +
            '<button class="chat-act" data-copy="' + encodeURIComponent(m.content) + '" title="复制">' + iconCopy() + '</button>' +
            '<button class="chat-act' + fbUp + '" data-fb="up" data-mid="' + escapeHtml(m.id) + '" title="点赞">' + iconUp() + '</button>' +
            '<button class="chat-act' + fbDown + '" data-fb="down" data-mid="' + escapeHtml(m.id) + '" title="点踩">' + iconDown() + '</button>' +
            (m.feedback_rating === 'down' && m.feedback_reason ? '<span class="chat-fb-reason" title="点踩原因">' + escapeHtml(m.feedback_reason) + '</span>' : '') +
          '</div>') +
        '</div>' +
      '</div>';
    }

    function renderChatMain() {
      const sessions = state.chatSessions;
      const sideList = state.chatSessionsLoading && !sessions.length
        ? '<div class="empty">' + iconSpin() + '<div>加载中</div></div>'
        : (!sessions.length
          ? '<div class="empty">' + iconChat() + '<div style="font-size:13px">还没有会话</div><div style="font-size:11.5px">点上方「新建聊天」开始</div></div>'
          : sessions.map(s =>
            '<div class="chat-sess' + (state.chatActive === s.id ? ' on' : '') + '" data-sess="' + escapeHtml(s.id) + '">' +
              '<span class="chat-sess-title">' + escapeHtml(s.title || '新会话') + '</span>' +
              '<span class="chat-sess-meta mono">' + iconClock() + escapeHtml(fmtTime(s.updated_at) || '') + ' · ' + (s.message_count || 0) + '</span>' +
              '<button class="chat-sess-del" data-sess-del="' + escapeHtml(s.id) + '" data-sess-title="' + escapeHtml(s.title || '新会话') + '" title="删除会话">' + iconTrash() + '</button>' +
            '</div>').join(''));

      let convo;
      if (!state.chatActive) {
        convo = '<div class="chat-empty"><div class="chat-empty-icon">' + iconChat() + '</div>' +
          '<div style="font-size:15px;font-weight:650;color:var(--text-dim)">开始和知识库对话</div>' +
          '<div style="font-size:12.5px;max-width:420px;line-height:1.6;margin-top:4px">先检索已沉淀的 wiki 案例;命中就用案例的解决方案回答并标注来源,没命中再由大模型兜底。</div>' +
          '<button class="btn primary" id="chatEmptyNew" type="button" style="margin-top:18px">' + iconPlus() + '新建聊天</button></div>';
      } else {
        const msgs = state.chatMessages.map(renderChatMessage).join('');
        const streaming = state.chatStreaming
          ? '<div class="chat-row agent"><div class="chat-avatar">' + iconChat() + '</div><div class="chat-bubble agent">' +
              '<div class="chat-srcline">' + (state.chatStreamMeta ? srcBadge(state.chatStreamMeta.source, state.chatStreamMeta.mode) : '<span class="src-badge muted">' + iconSpin() + '检索中…</span>') + '</div>' +
              '<div class="chat-md" id="chatStreamBody">' + (state.chatStreamText ? renderMarkdown(state.chatStreamText) + '<span class="caret"></span>' : '<span class="caret"></span>') + '</div>' +
            '</div></div>'
          : '';
        const empty = (!state.chatMessages.length && !state.chatStreaming)
          ? '<div class="empty" style="margin:auto">' + iconChat() + '<div style="font-size:13px">发送第一条消息开始对话</div></div>'
          : '';
        convo =
          '<div class="chat-scroll" id="chatScroll">' +
            (state.chatMessagesLoading ? '<div class="empty">' + iconSpin() + '<div>加载消息中</div></div>' : (empty + msgs + streaming)) +
          '</div>' +
          '<div class="chat-input-bar">' +
            '<textarea id="chatInput" class="field" placeholder="输入你的问题,Enter 发送 / Shift+Enter 换行" rows="1" ' + (state.chatStreaming ? 'disabled' : '') + '>' + escapeHtml(state.chatInput) + '</textarea>' +
            '<button class="btn primary chat-send" id="chatSend" type="button" ' + (state.chatStreaming ? 'disabled' : '') + '>' + (state.chatStreaming ? iconSpin() : iconSend()) + '</button>' +
          '</div>';
      }

      return '<section class="chat-layout">' +
        '<aside class="card chat-side">' +
          '<div class="card-head knowledge-head">' +
            '<div style="min-width:0"><div class="kicker" style="overflow:hidden;text-overflow:ellipsis">CHAT · AGENT</div><h3>会话</h3></div>' +
            '<div class="knowledge-actions">' +
              '<button class="btn sm primary" id="chatNew" type="button" title="新建聊天">' + iconPlus() + '新建</button>' +
            '</div>' +
          '</div>' +
          '<div class="card-pad">' +
            '<div class="chat-sess-list">' + sideList + '</div>' +
          '</div>' +
        '</aside>' +
        '<div class="card chat-main">' + convo + '</div>' +
      '</section>';
    }

    function bindChatEvents() {
      const bindC = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };
      bindC('chatNew', newChatSession);
      bindC('chatEmptyNew', newChatSession);
      bindC('chatSend', sendChatMessage);
      const ta = document.getElementById('chatInput');
      if (ta) {
        ta.oninput = e => { state.chatInput = e.target.value; autoGrow(e.target); };
        ta.onkeydown = e => {
          if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendChatMessage(); }
        };
      }
      root.querySelectorAll('[data-sess]').forEach(el => el.onclick = e => {
        if (e.target.closest('[data-sess-del]')) return;
        selectChatSession(el.dataset.sess);
      });
      root.querySelectorAll('[data-sess-del]').forEach(el => el.onclick = e => {
        e.stopPropagation();
        deleteChatSession(el.dataset.sessDel, el.dataset.sessTitle);
      });
      root.querySelectorAll('[data-copy]').forEach(el => el.onclick = () => copyMessage(decodeURIComponent(el.dataset.copy)));
      root.querySelectorAll('[data-fb]').forEach(el => el.onclick = () => chatFeedback(el.dataset.mid, el.dataset.fb));
      root.querySelectorAll('[data-wiki-file]').forEach(el => el.onclick = () => openWikiDetail(el.dataset.wikiFile));
    }

    function autoGrow(ta) {
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
    }

    function afterChatRender() {
      const ta = document.getElementById('chatInput');
      if (ta) autoGrow(ta);
      scrollChatToBottom();
    }
