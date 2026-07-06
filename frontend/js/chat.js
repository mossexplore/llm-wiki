    // ============================ 对话 Agent 页面 ============================
    // 左侧会话列表 + 右侧聊天区(参考 NextChat)。回答先检索 wiki,命中则流式回 wiki
    // 答案并标注来源;否则流式调用大模型。所有会话/消息/反馈都落库(见后端)。

    let chatLatencyTimer = null;
    let chatStreamAbortControllers = {};
    let chatStreamSeq = 0;
    const STOPPED_HYDRATE_DELAYS = [350, 900, 1600, 2600, 4000, 6500];

    function iconChat() { return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>'; }
    function iconSend() { return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"></path><path d="M22 2 15 22l-4-9-9-4 20-7z"></path></svg>'; }
    function iconStop() { return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="2"></rect></svg>'; }
    function iconCopy() { return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>'; }
    function iconUp() { return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10v12"></path><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"></path></svg>'; }
    function iconDown() { return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M17 14V2"></path><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"></path></svg>'; }

    // 行内格式:[链接](url) / **粗体** / *斜体* / `行内代码`(已在外层做过 HTML 转义)
    function mdInline(s) {
      return s
        .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|\/[^\s)]+)\)/g,
          '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
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
        if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {           // 分割线 --- *** ___
          flushBlocks();
          out.push('<hr class="md-hr">');
        } else if ((m = line.match(/^\s*(#{1,6})\s+(.*)$/))) {     // 标题
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
        const r = await fetch('/api/chat/sessions/list', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const data = apiData(await r.json());
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
        const payload = await r.json();
        if (!r.ok) throw new Error(apiErrorMessage(payload, '新建失败'));
        const s = apiData(payload);
        state.chatSessions.unshift(s);
        state.chatActive = s.id;
        state.chatMessages = [];
        syncActiveChatStreamState();
        render();
        const ta = document.getElementById('chatInput');
        if (ta) ta.focus();
      } catch (e) { showToast(String(e && e.message || e)); }
    }

    async function selectChatSession(id) {
      if (!id) return;
      state.chatActive = id;
      state.chatMessagesLoading = true;
      syncActiveChatStreamState();
      render();
      try {
        const r = await fetch('/api/chat/sessions/' + encodeURIComponent(id) + '/messages/list', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        const payload = await r.json();
        if (!r.ok) throw new Error(apiErrorMessage(payload, '加载消息失败'));
        if (state.chatActive !== id) return;
        state.chatMessages = apiData(payload).items || [];
        state.chatMessagesLoading = false;
        syncActiveChatStreamState();
        render();
      } catch (e) {
        if (state.chatActive !== id) return;
        state.chatMessagesLoading = false;
        syncActiveChatStreamState();
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
        const r = await fetch('/api/chat/sessions/' + encodeURIComponent(id) + '/delete', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        if (noBackend(r.status)) { showToast('后端未连接 · 无法删除'); return; }
        if (!r.ok) { const d = await r.json(); throw new Error(apiErrorMessage(d, '删除失败')); }
        abortChatStream(id);
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

    async function clearChatSessions() {
      if (hasChatStreaming()) return;
      const n = state.chatSessions.length;
      if (!n) return;
      const ok = await confirmModal({
        title: '清空全部会话',
        message: '确定清空全部历史会话吗?所有会话关联的问答记录和反馈都会被移除。',
        confirmText: '清空', cancelText: '取消', danger: true,
      });
      if (!ok) return;
      try {
        abortAllChatStreams();
        const r = await fetch('/api/chat/sessions/clear', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        if (noBackend(r.status)) { showToast('后端未连接 · 无法清空'); return; }
        const payload = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(apiErrorMessage(payload, '清空失败'));
        const data = apiData(payload);
        state.chatSessions = [];
        state.chatActive = '';
        state.chatMessages = [];
        state.chatMessagesLoading = false;
        syncActiveChatStreamState();
        state.chatInput = '';
        render();
        const deleted = data.deleted || {};
        showToast('已清空 ' + (deleted.sessions || n) + ' 个会话');
      } catch (e) {
        render();
        showToast(String(e && e.message || e));
      }
    }

    async function sendChatMessage() {
      if (isSessionStreaming(state.chatActive)) return;
      const text = (state.chatInput || '').trim();
      if (!text) { showToast('请输入问题'); return; }
      // 没有会话则先建一个
      if (!state.chatActive) {
        await newChatSession();
        if (!state.chatActive) return;
      }
      const sessionId = state.chatActive;
      const streamToken = String(++chatStreamSeq) + ':' + sessionId;
      abortChatStream(sessionId);
      chatStreamAbortControllers[sessionId] = new AbortController();
      stopChatLatencyTimer();
      state.chatMessages.push({ role: 'user', content: text, id: 'local-' + Date.now() });
      state.chatInput = '';
      state.chatStreams[sessionId] = {
        token: streamToken,
        text: '',
        meta: null,
        status: { stage: 'retrieving', elapsed_ms: 0 },
        streaming: true
      };
      syncActiveChatStreamState();
      render();

      let completed = false;
      try {
        const r = await fetch('/api/chat/sessions/' + encodeURIComponent(sessionId) + '/messages', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: text }),
          signal: chatStreamAbortControllers[sessionId].signal
        });
        if (noBackend(r.status)) throw new Error('后端未连接');
        if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(apiErrorMessage(d, '发送失败')); }
        completed = await consumeChatStream(r, sessionId, streamToken);
      } catch (e) {
        if (e && e.name === 'AbortError') return;
        if (!isCurrentChatStream(sessionId, streamToken)) return;
        clearChatStream(sessionId);
        if (state.chatActive === sessionId) {
          syncActiveChatStreamState();
          render();
        }
        showToast(String(e && e.message || e));
      }
      // 流结束后刷新会话列表(标题/排序可能变化)
      if (completed) loadChatSessions(false);
    }

    async function consumeChatStream(resp, sessionId, streamToken) {
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let doneMeta = null;
      let errored = '';
      const handleEvent = async ev => {
        if (!isCurrentChatStream(sessionId, streamToken)) {
          await cancelChatReader(reader);
          return false;
        }
        if (ev.session_id && ev.session_id !== sessionId) return true;
        const streamState = getChatStream(sessionId);
        if (!streamState) return true;
        if (ev.type === 'meta') {
          streamState.meta = { source: ev.source, mode: ev.mode, refs: ev.refs || [], retrieval_ms: ev.retrieval_ms };
          streamState.status = Object.assign({}, streamState.status || {}, {
            stage: 'retrieved',
            retrieval_ms: ev.retrieval_ms,
            elapsed_ms: ev.retrieval_ms
          });
          renderActiveChatStream(sessionId);
        } else if (ev.type === 'status') {
          streamState.status = Object.assign({}, streamState.status || {}, ev);
          if (ev.stage === 'generating') startChatLatencyTimer(sessionId, streamToken);
          if (ev.stage === 'first_delta' && state.chatActive === sessionId) stopChatLatencyTimer();
          renderActiveChatStream(sessionId);
        } else if (ev.type === 'delta') {
          streamState.text += ev.text || '';
          updateStreamDOM(sessionId);
        } else if (ev.type === 'done') {
          doneMeta = ev;
          streamState.status = Object.assign({}, streamState.status || {}, {
            stage: 'done',
            retrieval_ms: ev.retrieval_ms,
            model_start_ms: ev.model_start_ms,
            model_wait_ms: ev.model_wait_ms,
            first_delta_ms: ev.first_delta_ms,
            total_ms: ev.total_ms,
            message_count: ev.message_count,
            prompt_chars: ev.prompt_chars
          });
        } else if (ev.type === 'error') {
          errored = ev.error || '生成失败';
        }
        return true;
      };
      while (true) {
        if (!isCurrentChatStream(sessionId, streamToken)) {
          await cancelChatReader(reader);
          return false;
        }
        const { value, done } = await reader.read();
        if (!isCurrentChatStream(sessionId, streamToken)) {
          await cancelChatReader(reader);
          return false;
        }
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let boundary;
        while ((boundary = buf.indexOf('\n\n')) >= 0) {
          const block = buf.slice(0, boundary);
          buf = buf.slice(boundary + 2);
          const data = block.split('\n')
            .map(line => line.trimEnd())
            .filter(line => line.startsWith('data:'))
            .map(line => line.slice(5).trimStart())
            .join('\n');
          if (!data || data === '[DONE]') continue;
          let ev;
          try { ev = JSON.parse(data); } catch (_) { continue; }
          if (!await handleEvent(ev)) return false;
        }
        let nl;
        while (buf.indexOf('\n\n') < 0 && (nl = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, nl).trim();
          if (!line || line.startsWith('data:')) break;
          buf = buf.slice(nl + 1);
          let ev;
          try { ev = JSON.parse(line); } catch (_) { continue; }
          if (!await handleEvent(ev)) return false;
        }
      }
      if (!isCurrentChatStream(sessionId, streamToken)) return false;
      const streamState = getChatStream(sessionId);
      if (!streamState) return false;
      streamState.streaming = false;
      if (state.chatActive === sessionId) stopChatLatencyTimer();
      if (errored) {
        if (state.chatActive === sessionId) showToast('生成出错:' + errored);
        // 仍把已生成内容作为一条回复展示
      }
      if (streamState.text && state.chatActive === sessionId && !state.chatMessagesLoading) {
        state.chatMessages.push({
          role: 'assistant',
          content: streamState.text,
          id: doneMeta && doneMeta.message_id || ('local-' + Date.now()),
          answer_source: streamState.meta && streamState.meta.source || (doneMeta && doneMeta.source),
          retrieval_mode: streamState.meta && streamState.meta.mode || (doneMeta && doneMeta.mode),
          refs: (streamState.meta && streamState.meta.refs) || (doneMeta && doneMeta.refs) || [],
          retrieval_ms: doneMeta && doneMeta.retrieval_ms,
          model_wait_ms: doneMeta && doneMeta.model_wait_ms,
          first_delta_ms: doneMeta && doneMeta.first_delta_ms,
          total_ms: doneMeta && doneMeta.total_ms,
	          elapsed_ms: doneMeta && doneMeta.total_ms,
	          message_count: doneMeta && doneMeta.message_count,
	          prompt_chars: doneMeta && doneMeta.prompt_chars,
	        });
      }
      clearChatStream(sessionId);
      if (state.chatActive === sessionId) {
        syncActiveChatStreamState();
        render();
      }
      return true;
    }

    function isCurrentChatStream(sessionId, streamToken) {
      const streamState = getChatStream(sessionId);
      return !!(streamState && streamState.streaming && streamState.token === streamToken);
    }

    function getChatStream(sessionId) {
      return sessionId ? (state.chatStreams && state.chatStreams[sessionId]) : null;
    }

    function isSessionStreaming(sessionId) {
      const streamState = getChatStream(sessionId);
      return !!(streamState && streamState.streaming);
    }

    function hasChatStreaming() {
      return Object.values(state.chatStreams || {}).some(s => s && s.streaming);
    }

    function syncActiveChatStreamState() {
      const streamState = getChatStream(state.chatActive);
      state.chatStreaming = !!(streamState && streamState.streaming);
      state.chatStreamText = streamState ? streamState.text || '' : '';
      state.chatStreamMeta = streamState ? streamState.meta || null : null;
      state.chatStreamStatus = streamState ? streamState.status || null : null;
      state.chatStreamToken = streamState ? streamState.token || '' : '';
    }

    function clearChatStream(sessionId) {
      delete chatStreamAbortControllers[sessionId];
      if (state.chatStreams) delete state.chatStreams[sessionId];
      if (state.chatActive === sessionId) {
        stopChatLatencyTimer();
        syncActiveChatStreamState();
      }
    }

    function resetChatStreamState() {
      state.chatStreaming = false;
      state.chatStreamText = '';
      state.chatStreamMeta = null;
      state.chatStreamStatus = null;
      state.chatStreamToken = '';
      stopChatLatencyTimer();
    }

    function abortChatStream(sessionId) {
      const controller = chatStreamAbortControllers[sessionId];
      delete chatStreamAbortControllers[sessionId];
      if (controller) controller.abort();
      clearChatStream(sessionId);
      if (state.chatActive === sessionId) {
        resetChatStreamState();
      }
    }

    async function stopChatStream(sessionId) {
      const streamState = getChatStream(sessionId);
      if (!streamState || !streamState.streaming) return;
      const partial = streamState.text || '';
      const meta = streamState.meta || {};
      const status = Object.assign({}, streamState.status || {}, { stage: 'stopped' });
      const controller = chatStreamAbortControllers[sessionId];
      delete chatStreamAbortControllers[sessionId];
      if (controller) controller.abort();
      stopChatLatencyTimer();
      streamState.streaming = false;

      if (partial.trim() && state.chatActive === sessionId && !state.chatMessagesLoading) {
        const localId = 'local-stopped-' + Date.now();
        const persisted = await persistStoppedChatMessage(sessionId, partial, meta, status);
        const stoppedMessage = persisted ? Object.assign({}, persisted, { stopped: true }) : {
          role: 'assistant',
          content: partial,
          id: localId,
          answer_source: meta.source,
          retrieval_mode: meta.mode,
          refs: meta.refs || [],
          retrieval_ms: status.retrieval_ms,
          model_wait_ms: status.model_wait_ms,
          first_delta_ms: status.first_delta_ms,
          total_ms: status.total_ms,
          elapsed_ms: status.elapsed_ms,
          message_count: status.message_count,
          prompt_chars: status.prompt_chars,
          stopped: true
        };
        state.chatMessages.push(stoppedMessage);
        if (!persisted) scheduleStoppedMessageHydration(sessionId, localId, partial, 0);
      }

      clearChatStream(sessionId);
      if (state.chatActive === sessionId) {
        syncActiveChatStreamState();
        render();
        const ta = document.getElementById('chatInput');
        if (ta) ta.focus();
      }
      loadChatSessions(false);
      showToast(partial.trim() ? '已停止生成' : '已停止请求');
    }

    async function persistStoppedChatMessage(sessionId, partial, meta, status) {
      try {
        const r = await fetch('/api/chat/sessions/' + encodeURIComponent(sessionId) + '/messages/stop', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            content: partial,
            answer_source: meta && meta.source,
            retrieval_mode: meta && meta.mode,
            refs: meta && meta.refs || [],
            elapsed_ms: status && status.elapsed_ms,
            retrieval_ms: status && status.retrieval_ms,
            model_wait_ms: status && status.model_wait_ms,
            first_delta_ms: status && status.first_delta_ms,
            total_ms: status && status.total_ms,
            message_count: status && status.message_count,
            prompt_chars: status && status.prompt_chars
          })
        });
        const payload = await r.json().catch(() => ({}));
        if (!r.ok) return null;
        const data = apiData(payload);
        return data && data.message || null;
      } catch (_) {
        return null;
      }
    }

    function scheduleStoppedMessageHydration(sessionId, localId, partial, attempt) {
      const delay = STOPPED_HYDRATE_DELAYS[Math.min(attempt, STOPPED_HYDRATE_DELAYS.length - 1)];
      setTimeout(() => hydrateStoppedChatMessage(sessionId, localId, partial, attempt), delay);
    }

    function shouldRetryStoppedHydration(sessionId, localId, attempt) {
      if (attempt >= STOPPED_HYDRATE_DELAYS.length - 1) return false;
      if (state.chatActive !== sessionId) return false;
      return state.chatMessages.some(m => m.id === localId && m.stopped);
    }

    async function hydrateStoppedChatMessage(sessionId, localId, partial, attempt) {
      if (state.chatActive !== sessionId) return;
      try {
        const r = await fetch('/api/chat/sessions/' + encodeURIComponent(sessionId) + '/messages/list', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        const payload = await r.json();
        if (!r.ok) return;
        const items = apiData(payload).items || [];
        let persistedIndex = -1;
        items.forEach((m, idx) => {
          const content = m.content || '';
          if (m.role === 'assistant' && (content === partial || content.indexOf(partial) === 0)) persistedIndex = idx;
        });
        if (persistedIndex < 0 || state.chatActive !== sessionId) {
          if (shouldRetryStoppedHydration(sessionId, localId, attempt)) {
            scheduleStoppedMessageHydration(sessionId, localId, partial, attempt + 1);
          }
          return;
        }
        state.chatMessages = items.map((m, idx) => idx === persistedIndex ? Object.assign({}, m, { stopped: true }) : m);
        syncActiveChatStreamState();
        render();
      } catch (_) {
        const msg = state.chatMessages.find(m => m.id === localId);
        if (msg) msg.stopped = true;
        if (shouldRetryStoppedHydration(sessionId, localId, attempt)) {
          scheduleStoppedMessageHydration(sessionId, localId, partial, attempt + 1);
        }
      }
    }

    function abortAllChatStreams() {
      Object.keys(chatStreamAbortControllers).forEach(sessionId => {
        const controller = chatStreamAbortControllers[sessionId];
        if (controller) controller.abort();
      });
      chatStreamAbortControllers = {};
      state.chatStreams = {};
      resetChatStreamState();
    }

    async function cancelChatReader(reader) {
      try { await reader.cancel(); } catch (_) {}
    }

    function renderActiveChatStream(sessionId) {
      if (state.chatActive !== sessionId) return;
      syncActiveChatStreamState();
      render();
    }

    function startChatLatencyTimer(sessionId, streamToken) {
      const streamState = getChatStream(sessionId);
      if (!streamState || streamState.token !== streamToken) return;
      const startedAt = Date.now();
      streamState.status = Object.assign({}, streamState.status || {}, { wait_started_at: startedAt });
      if (state.chatActive === sessionId) syncActiveChatStreamState();
      if (state.chatActive !== sessionId) return;
      stopChatLatencyTimer();
      chatLatencyTimer = setInterval(() => {
        const current = getChatStream(sessionId);
        const st = current && current.status || {};
        if (!current || !current.streaming || current.token !== streamToken ||
            state.chatActive !== sessionId || st.stage !== 'generating' || st.first_delta_ms) {
          stopChatLatencyTimer();
          return;
        }
        current.status = Object.assign({}, st, {
          wait_ms: Date.now() - (st.wait_started_at || startedAt)
        });
        syncActiveChatStreamState();
        updateLatencyDOM();
      }, 1000);
    }

    function stopChatLatencyTimer() {
      if (chatLatencyTimer) {
        clearInterval(chatLatencyTimer);
        chatLatencyTimer = null;
      }
    }

    function updateLatencyDOM() {
      const el = document.getElementById('chatLatency');
      if (el) el.outerHTML = chatLatencyHtml();
    }

    // 流式期间只更新生成中气泡的 DOM,避免整页重渲染丢失滚动/输入焦点
    function updateStreamDOM(sessionId) {
      if (state.chatActive !== sessionId) return;
      syncActiveChatStreamState();
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

    const FEEDBACK_INFO_TYPES = [
      { value: 'not_helpful', label: '回答没有用' },
      { value: 'misunderstood_intent', label: '没有理解我的意图' },
      { value: 'incorrect_information', label: '信息/数据有误' },
    ];

    function feedbackReasonSummary(reason) {
      const raw = String(reason || '').trim();
      if (!raw) return '';
      try {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          const labels = (parsed.feedback_info_types || [])
            .map(v => (FEEDBACK_INFO_TYPES.find(t => t.value === v) || {}).label || v)
            .filter(Boolean);
          const info = String(parsed.feedback_info || '').trim();
          return labels.concat(info ? [info] : []).join(' / ');
        }
      } catch (_) {}
      return raw;
    }

    function feedbackReasonModal() {
      return new Promise(resolve => {
        const mask = document.createElement('div');
        mask.className = 'modal-mask';
        mask.innerHTML =
          '<div class="modal-box feedback-modal" role="dialog" aria-modal="true">' +
            '<div class="modal-head"><div class="modal-title">反馈:这条回答不太好</div></div>' +
            '<div class="modal-msg">请选择原因,也可以补充具体说明:</div>' +
            '<div class="feedback-type-row">' +
              FEEDBACK_INFO_TYPES.map(t =>
                '<button class="feedback-type-btn" type="button" data-type="' + escapeHtml(t.value) + '" aria-pressed="false">' +
                  escapeHtml(t.label) +
                '</button>'
              ).join('') +
            '</div>' +
            '<textarea class="field mono" data-act="input" placeholder="例如:解决方案不适用 / 来源不相关 / 有事实错误…" style="height:88px;margin-top:12px"></textarea>' +
            '<div class="modal-actions">' +
              '<button class="btn" data-act="cancel">取消</button>' +
              '<button class="btn primary" data-act="ok">提交点踩</button>' +
            '</div>' +
          '</div>';
        const input = mask.querySelector('[data-act="input"]');
        const typeBtns = Array.from(mask.querySelectorAll('[data-type]'));
        function selectedTypes() {
          return typeBtns.filter(btn => btn.classList.contains('on')).map(btn => btn.dataset.type);
        }
        function close(v) {
          mask.classList.remove('show');
          document.removeEventListener('keydown', onKey);
          setTimeout(() => mask.remove(), 160);
          resolve(v);
        }
        function submit() {
          const info = input.value.trim();
          const types = selectedTypes();
          if (!info && !types.length) {
            input.focus();
            showToast('请选择或填写点踩原因');
            return;
          }
          close({ feedback_info: info, feedback_info_types: types });
        }
        function onKey(e) {
          if (e.key === 'Escape') close(null);
          else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit();
        }
        mask.addEventListener('click', e => { if (e.target === mask) close(null); });
        typeBtns.forEach(btn => {
          btn.onclick = () => {
            const on = !btn.classList.contains('on');
            btn.classList.toggle('on', on);
            btn.setAttribute('aria-pressed', on ? 'true' : 'false');
          };
        });
        mask.querySelector('[data-act="cancel"]').onclick = () => close(null);
        mask.querySelector('[data-act="ok"]').onclick = submit;
        document.addEventListener('keydown', onKey);
        document.body.appendChild(mask);
        requestAnimationFrame(() => mask.classList.add('show'));
        typeBtns[0].focus();
      });
    }

    async function chatFeedback(messageId, feedback) {
      if (!messageId || String(messageId).startsWith('local-')) {
        showToast('该回复未落库,无法反馈');
        return;
      }
      const msg = state.chatMessages.find(m => m.id === messageId);
      const clearing = msg && msg.feedback === feedback;
      let reason = null;
      if (feedback === 'unlike' && !clearing) {
        reason = await feedbackReasonModal();
        if (reason === null) return;     // 用户取消
      }
      try {
        const payload = clearing
          ? { feedback: 'NONE' }
          : feedback === 'like'
          ? { feedback: 'like' }
          : { feedback: 'unlike', reason };
        const r = await fetch('/api/chat/messages/' + encodeURIComponent(messageId) + '/feedback', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (noBackend(r.status)) { showToast('后端未连接 · 无法反馈'); return; }
        const data = await r.json();
        if (!r.ok) throw new Error(apiErrorMessage(data, '反馈失败'));
        if (msg) {
          msg.feedback = clearing ? null : feedback;
          msg.feedback_reason = clearing ? null : (reason ? JSON.stringify(reason) : null);
        }
        render();
        showToast(clearing ? '已取消反馈' : (feedback === 'like' ? '已点赞,谢谢反馈' : '已记录,谢谢反馈'));
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

    function fmtMs(ms) {
      return typeof ms === 'number' && isFinite(ms) ? (ms / 1000).toFixed(ms >= 10000 ? 1 : 2) + 's' : '—';
    }

    function _num(v) {
      return typeof v === 'number' && isFinite(v) ? v : null;
    }

    function chatMessageLatencyStatus(m) {
      const retrieval = _num(m.retrieval_ms);
      const legacyElapsed = _num(m.elapsed_ms);
      const total = _num(m.total_ms);
      const st = {
        stage: m.stopped ? 'stopped' : 'done',
        retrieval_ms: retrieval !== null ? retrieval : (total === null ? legacyElapsed : null),
        model_wait_ms: _num(m.model_wait_ms),
        first_delta_ms: _num(m.first_delta_ms),
	        total_ms: total,
	        message_count: _num(m.message_count),
	        prompt_chars: _num(m.prompt_chars)
	      };
      return Object.values(st).some(v => typeof v === 'number') ? st : null;
    }

    function chatLatencyHtml(status, withId) {
      const st = status || state.chatStreamStatus || {};
      const labels = {
        retrieving: '检索中',
        retrieved: '检索完成',
        generating: '请求模型',
        first_delta: '生成中',
        stopped: '已停止',
        done: '已完成'
      };
      const bits = ['<span>' + escapeHtml(labels[st.stage] || '处理中') + '</span>'];
	      if (typeof st.retrieval_ms === 'number') bits.push('<span>检索 ' + fmtMs(st.retrieval_ms) + '</span>');
	      if (typeof st.message_count === 'number' || typeof st.prompt_chars === 'number') {
	        bits.push('<span>提示 ' + (st.message_count || '—') + '条/' + (st.prompt_chars || 0) + '字</span>');
	      }
      if (typeof st.model_wait_ms === 'number') bits.push('<span>模型等待 ' + fmtMs(st.model_wait_ms) + '</span>');
      if (typeof st.first_delta_ms === 'number') bits.push('<span>首字 ' + fmtMs(st.first_delta_ms) + '</span>');
      else if (st.stage === 'generating') {
        const wait = typeof st.wait_ms === 'number' ? st.wait_ms : st.elapsed_ms;
        bits.push('<span>已等 ' + fmtMs(wait) + '</span>');
      }
      if (typeof st.total_ms === 'number') bits.push('<span>总耗时 ' + fmtMs(st.total_ms) + '</span>');
      const idAttr = withId === false ? '' : ' id="chatLatency"';
      return '<span class="chat-latency mono"' + idAttr + '>' + bits.join('<span class="lat-dot">·</span>') + '</span>';
    }

    async function openWikiDetail(file) {
      if (!file) return;
      const mask = wikiModalShell('加载中…', '<div class="empty">' + iconSpin() + '<div>正在加载知识详情</div></div>');
      try {
        const r = await fetch('/api/knowledge/' + file.split('/').map(encodeURIComponent).join('/'));
        const payload = await r.json();
        if (!r.ok) throw new Error(apiErrorMessage(payload, '加载失败'));
        wikiModalFill(mask, apiData(payload));
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
      const latency = chatMessageLatencyStatus(m);
      const fbUp = m.feedback === 'like' ? ' on' : '';
      const fbDown = m.feedback === 'unlike' ? ' on dislike' : '';
      const isLocal = String(m.id || '').startsWith('local-');
      const fbReason = feedbackReasonSummary(m.feedback_reason);
      return '<div class="chat-row agent">' +
        '<div class="chat-avatar">' + iconChat() + '</div>' +
        '<div class="chat-bubble agent">' +
          '<div class="chat-srcline">' + srcBadge(m.answer_source, m.retrieval_mode) + (latency ? chatLatencyHtml(latency, false) : '') + '</div>' +
          (m.stopped ? '<div class="chat-stopped mono">已停止生成</div>' : '') +
          '<div class="chat-md">' + renderMarkdown(m.content) + '</div>' +
          refsHtml +
          (isLocal && !m.stopped ? '' :
          '<div class="chat-acts">' +
            '<button class="chat-act" data-copy="' + encodeURIComponent(m.content) + '" title="复制">' + iconCopy() + '</button>' +
            (isLocal
              ? '<span class="chat-fb-reason">反馈同步中…</span>'
              : '<button class="chat-act' + fbUp + '" data-fb="like" data-mid="' + escapeHtml(m.id) + '" title="点赞">' + iconUp() + '</button>' +
                '<button class="chat-act' + fbDown + '" data-fb="unlike" data-mid="' + escapeHtml(m.id) + '" title="点踩">' + iconDown() + '</button>' +
                (m.feedback === 'unlike' && fbReason ? '<span class="chat-fb-reason" title="点踩原因">' + escapeHtml(fbReason) + '</span>' : '')) +
          '</div>') +
        '</div>' +
      '</div>';
    }

    function renderChatMain() {
      syncActiveChatStreamState();
      const sessions = state.chatSessions;
      const activeStreaming = isSessionStreaming(state.chatActive);
      const anyStreaming = hasChatStreaming();
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
        const streaming = activeStreaming
          ? '<div class="chat-row agent"><div class="chat-avatar">' + iconChat() + '</div><div class="chat-bubble agent">' +
              '<div class="chat-srcline">' +
                (state.chatStreamMeta ? srcBadge(state.chatStreamMeta.source, state.chatStreamMeta.mode) : '<span class="src-badge muted">' + iconSpin() + '检索中…</span>') +
                chatLatencyHtml(null, true) +
              '</div>' +
              '<div class="chat-md" id="chatStreamBody">' + (state.chatStreamText ? renderMarkdown(state.chatStreamText) + '<span class="caret"></span>' : '<span class="caret"></span>') + '</div>' +
            '</div></div>'
          : '';
        const empty = (!state.chatMessages.length && !activeStreaming)
          ? '<div class="empty" style="margin:auto">' + iconChat() + '<div style="font-size:13px">发送第一条消息开始对话</div></div>'
          : '';
        convo =
          '<div class="chat-scroll" id="chatScroll">' +
            (state.chatMessagesLoading ? '<div class="empty">' + iconSpin() + '<div>加载消息中</div></div>' : (empty + msgs + streaming)) +
          '</div>' +
          '<div class="chat-input-bar">' +
            '<textarea id="chatInput" class="field" placeholder="输入你的问题,Enter 发送 / Shift+Enter 换行" rows="1" ' + (activeStreaming ? 'disabled' : '') + '>' + escapeHtml(state.chatInput) + '</textarea>' +
            (activeStreaming
              ? '<button class="btn danger chat-stop" id="chatStop" type="button" title="停止生成">' + iconStop() + '</button>'
              : '<button class="btn primary chat-send" id="chatSend" type="button" title="发送">' + iconSend() + '</button>') +
          '</div>';
      }

      return '<section class="chat-layout">' +
        '<aside class="card chat-side">' +
          '<div class="card-head knowledge-head">' +
            '<div style="min-width:0"><div class="kicker" style="overflow:hidden;text-overflow:ellipsis">CHAT · AGENT</div><h3>会话</h3></div>' +
            '<div class="knowledge-actions">' +
              (state.chatSessions.length ? '<button class="btn sm danger" id="chatClear" type="button" title="清空全部历史会话" ' + (anyStreaming ? 'disabled' : '') + '>' + iconTrash() + '清空</button>' : '') +
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
      bindC('chatClear', clearChatSessions);
      bindC('chatEmptyNew', newChatSession);
      bindC('chatSend', sendChatMessage);
      bindC('chatStop', () => stopChatStream(state.chatActive));
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
