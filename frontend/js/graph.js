    async function loadGraph() {
      state.graphLoading = true;
      render();
      try {
        const r = await fetch('/api/graph');
        if (!r.ok) throw new Error('HTTP ' + r.status);
        state.graph = await r.json();
        if (!state.graphSelected && state.graph.nodes && state.graph.nodes.length) {
          const firstCase = state.graph.nodes.find(n => n.type === 'case') || state.graph.nodes[0];
          state.graphSelected = firstCase.id;
        }
      } catch (e) {
        console.error('[log-wiki] graph load failed', e);
        showToast('图谱加载失败');
      } finally {
        state.graphLoading = false;
        render();
      }
    }

    function graphColor(type) {
      return {
        case: '#17a2b8',
        concept: '#7c6ee6',
        raw: '#7a8598',
        component: '#1f9d66',
        tag: '#c99622'
      }[type] || '#6b7689';
    }

    function graphFiltered() {
      const graph = state.graph || { nodes: [], edges: [] };
      const q = state.graphSearch.trim().toLowerCase();
      let nodes = graph.nodes.filter(n => {
        const typeOk = state.graphFilter === 'all' || n.type === state.graphFilter;
        const text = [n.id, n.title, n.description, ...(n.tags || []), ...(n.components || [])].join(' ').toLowerCase();
        return typeOk && (!q || text.includes(q));
      });
      const ids = new Set(nodes.map(n => n.id));
      if (state.graphFilter === 'neighbors' && state.graphSelected) {
        const keep = new Set([state.graphSelected]);
        graph.edges.forEach(e => {
          if (e.source === state.graphSelected) keep.add(e.target);
          if (e.target === state.graphSelected) keep.add(e.source);
        });
        nodes = graph.nodes.filter(n => keep.has(n.id));
        ids.clear();
        nodes.forEach(n => ids.add(n.id));
      }
      const edges = graph.edges.filter(e => ids.has(e.source) && ids.has(e.target));
      return { nodes, edges };
    }

    const GRAPH_W = 980;
    const GRAPH_H = 520;
    const GRAPH_PAD = 42;

    function clampGraphPoint(x, y) {
      return {
        x: Math.max(GRAPH_PAD, Math.min(GRAPH_W - GRAPH_PAD, Number.isFinite(x) ? x : GRAPH_W / 2)),
        y: Math.max(GRAPH_PAD, Math.min(GRAPH_H - GRAPH_PAD, Number.isFinite(y) ? y : GRAPH_H / 2))
      };
    }

    function initialGraphPoint(node, index, typeIndex) {
      const columns = { case: 145, concept: 320, component: 495, tag: 670, raw: 835 };
      const x = columns[node.type] || (120 + (index % 5) * 185);
      const laneCount = Math.max(1, Math.floor((GRAPH_H - GRAPH_PAD * 2) / 72));
      const lane = typeIndex % laneCount;
      const wrap = Math.floor(typeIndex / laneCount);
      const point = clampGraphPoint(x + wrap * 34, GRAPH_PAD + lane * 72);
      return point;
    }

    function graphLayout(nodes) {
      const typeCounts = {};
      return nodes.map((node, index) => {
        const typeIndex = typeCounts[node.type] || 0;
        typeCounts[node.type] = typeIndex + 1;
        const saved = state.graphPositions[node.id];
        const point = clampGraphPoint(saved && saved.x, saved && saved.y);
        const initial = saved ? point : initialGraphPoint(node, index, typeIndex);
        state.graphPositions[node.id] = initial;
        return Object.assign({}, node, initial);
      });
    }

    function graphPointFromEvent(svg, event) {
      if (svg.createSVGPoint && svg.getScreenCTM) {
        const point = svg.createSVGPoint();
        point.x = event.clientX;
        point.y = event.clientY;
        const matrix = svg.getScreenCTM();
        if (matrix) {
          const transformed = point.matrixTransform(matrix.inverse());
          return { x: transformed.x, y: transformed.y };
        }
      }
      const rect = svg.getBoundingClientRect();
      return {
        x: ((event.clientX - rect.left) / rect.width) * GRAPH_W,
        y: ((event.clientY - rect.top) / rect.height) * GRAPH_H
      };
    }

    function scheduleGraphRender() {
      if (state.graphRenderQueued) return;
      state.graphRenderQueued = true;
      requestAnimationFrame(() => {
        state.graphRenderQueued = false;
        render();
      });
    }

    function startGraphDrag(event) {
      const id = event.currentTarget.dataset.nodeId;
      const svg = event.currentTarget.ownerSVGElement;
      const current = state.graphPositions[id];
      if (!id || !svg || !current) return;
      const point = graphPointFromEvent(svg, event);
      state.graphDrag = { id, dx: point.x - current.x, dy: point.y - current.y, moved: false };
      if (event.pointerId != null && event.currentTarget.setPointerCapture) {
        event.currentTarget.setPointerCapture(event.pointerId);
      }
      event.preventDefault();
    }

    function moveGraphDrag(event) {
      if (!state.graphDrag) return;
      const svg = document.querySelector('.graph-stage svg');
      if (!svg) return;
      const point = graphPointFromEvent(svg, event);
      const next = clampGraphPoint(point.x - state.graphDrag.dx, point.y - state.graphDrag.dy);
      const prev = state.graphPositions[state.graphDrag.id] || next;
      if (Math.abs(next.x - prev.x) > 1 || Math.abs(next.y - prev.y) > 1) {
        state.graphDrag.moved = true;
        state.graphPositions[state.graphDrag.id] = next;
        scheduleGraphRender();
      }
    }

    function endGraphDrag() {
      if (!state.graphDrag) return;
      state.graphSuppressClick = state.graphDrag.moved;
      state.graphDrag = null;
    }

    function resetGraphLayout() {
      state.graphPositions = {};
      render();
    }

    function graphNodeById(id) {
      return (state.graph && state.graph.nodes || []).find(n => n.id === id) || null;
    }

    function graphNeighbors(id) {
      const graph = state.graph || { edges: [] };
      return graph.edges
        .filter(e => e.source === id || e.target === id)
        .map(e => ({ edge: e, node: graphNodeById(e.source === id ? e.target : e.source) }))
        .filter(item => item.node);
    }

    function renderGraphMain() {
      if (state.graphLoading && !state.graph) {
        return `<section class="card"><div class="card-pad empty">${iconSpin()}<div>知识图谱加载中</div></div></section>`;
      }
      const graph = graphFiltered();
      const layout = graphLayout(graph.nodes);
      const byId = new Map(layout.map(n => [n.id, n]));
      const selected = graphNodeById(state.graphSelected) || graph.nodes[0] || null;
      const edges = graph.edges.map(e => ({ edge: e, source: byId.get(e.source), target: byId.get(e.target) })).filter(e => e.source && e.target);
      return `
        <section class="card">
          <div class="card-head">
            <div><div class="kicker">OKF GRAPH · KNOWLEDGE MAP</div><h3>知识图谱</h3></div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end">
              <button class="btn sm" id="resetGraphLayout" type="button">重置布局</button>
              <button class="btn sm" id="reloadGraph" type="button">${state.graphLoading ? iconSpin() : iconSearch()}刷新</button>
            </div>
          </div>
          <div class="card-pad graph-shell">
            <div class="graph-stage">
              <svg viewBox="0 0 ${GRAPH_W} ${GRAPH_H}" role="img" aria-label="log-wiki knowledge graph">
                <defs>
                  <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3.5" orient="auto">
                    <path d="M0,0 L8,3.5 L0,7 Z" fill="rgba(58,70,90,0.35)"></path>
                  </marker>
                </defs>
                ${edges.map(({edge, source, target}) => `<line class="graph-edge ${(edge.source === state.graphSelected || edge.target === state.graphSelected) ? 'on' : ''}" x1="${source.x}" y1="${source.y}" x2="${target.x}" y2="${target.y}" marker-end="url(#arrow)"><title>${escapeHtml(edge.type)}</title></line>`).join('')}
                ${layout.map(n => {
                  const active = n.id === state.graphSelected;
                  const dim = state.graphSelected && !active && !graphNeighbors(state.graphSelected).some(item => item.node.id === n.id);
                  const label = n.title.length > 20 ? n.title.slice(0, 19) + '…' : n.title;
                  const r = n.type === 'case' ? 18 : (n.type === 'concept' ? 16 : 13);
                  const textLeft = n.x > GRAPH_W - 190;
                  return `<g class="graph-node ${active ? 'active' : ''} ${dim ? 'dimmed' : ''}" data-node-id="${escapeHtml(n.id)}" transform="translate(${n.x},${n.y})">
                    <circle r="${r}" fill="${graphColor(n.type)}"></circle>
                    <text x="${textLeft ? -r - 8 : r + 8}" y="4" text-anchor="${textLeft ? 'end' : 'start'}">${escapeHtml(label)}</text>
                    <title>${escapeHtml(n.title)} · ${escapeHtml(n.type)}</title>
                  </g>`;
                }).join('')}
              </svg>
            </div>
            ${renderGraphDetail(selected)}
          </div>
        </section>`;
    }

    function renderGraphDetail(node) {
      if (!node) {
        return `<aside class="graph-detail"><div class="empty">${iconInfo()}<div>暂无节点</div></div></aside>`;
      }
      const neighbors = graphNeighbors(node.id);
      return `
        <aside class="graph-detail">
          <div>
            <div class="kicker">SELECTED NODE</div>
            <h3 style="margin-top:6px">${escapeHtml(node.title)}</h3>
            <div class="mono muted" style="font-size:11px;margin-top:6px;word-break:break-all">${escapeHtml(node.id)}</div>
          </div>
          <div class="graph-chip-row">
            <span class="graph-chip"><span class="dot" style="background:${graphColor(node.type)}"></span>${escapeHtml(node.type)}</span>
            ${node.status ? `<span class="graph-chip">${escapeHtml(node.status)}</span>` : ''}
            ${node.confidence ? `<span class="graph-chip">${escapeHtml(node.confidence)}</span>` : ''}
          </div>
          ${node.description ? `<p class="muted" style="font-size:12.5px;line-height:1.6;margin:0">${escapeHtml(node.description)}</p>` : ''}
          ${node.tags && node.tags.length ? `<div><div class="kicker" style="margin-bottom:7px">TAGS</div><div class="graph-chip-row">${node.tags.map(t => `<span class="graph-chip">${escapeHtml(t)}</span>`).join('')}</div></div>` : ''}
          ${node.components && node.components.length ? `<div><div class="kicker" style="margin-bottom:7px">COMPONENTS</div><div class="graph-chip-row">${node.components.map(t => `<span class="graph-chip">${escapeHtml(t)}</span>`).join('')}</div></div>` : ''}
          <div>
            <div class="kicker" style="margin-bottom:7px">NEIGHBORS · ${neighbors.length}</div>
            <div class="graph-list">
              ${neighbors.length ? neighbors.map(({edge, node: n}) => `<button type="button" data-node-id="${escapeHtml(n.id)}"><span class="mono muted">${escapeHtml(edge.type)}</span><br>${escapeHtml(n.title)}</button>`).join('') : '<div class="muted" style="font-size:12px">暂无相邻节点</div>'}
            </div>
          </div>
        </aside>`;
    }

