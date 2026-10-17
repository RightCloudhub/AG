/* Pure view-model builders for answers, reasoning chains and stream events.
 * No Vue, no DOM: everything here returns plain data rendered by text interpolation. */

const ARROW = /(-\[[^\]]+\]->|<-\[[^\]]+\]-)/;
const STAGE_LABELS = { plan: "规划", retrieve: "检索", critic: "评估", answer: "生成答案", verify: "校验" };
export const PROGRESS_EVENTS = ["cache_hit", "triage", "thinking", "sub_question", "hop_done"];

function cleanName(raw) {
  return String(raw || "")
    .replace(/\s*\[[^\]]*\]\s*$/, "") // validity suffix " [2019–now]"
    .replace(/\s*\(([^)]*)\)\s*$/, "") // entity type "(Company)"
    .trim();
}

function relLabel(token) {
  const inner = token.replace(/^<?-\[/, "").replace(/\]->?$/, "");
  return inner.split(/[:#]/)[0].trim();
}

/** "A -[REL]-> B (Type)" or multi-hop paths → { nodes, edges }; null when not a graph fact. */
export function parsePath(content) {
  const text = String(content || "");
  if (!ARROW.test(text)) return null;
  const parts = text.split(ARROW);
  const nodes = [];
  const edges = [];
  for (let i = 0; i < parts.length; i += 2) nodes.push(cleanName(parts[i]));
  for (let i = 1; i < parts.length; i += 2) {
    const from = nodes[(i - 1) / 2];
    const to = nodes[(i + 1) / 2];
    const reverse = parts[i].startsWith("<-");
    edges.push({ from: reverse ? to : from, to: reverse ? from : to, rel: relLabel(parts[i]) });
  }
  return nodes.every(Boolean) ? { nodes, edges } : null;
}

/** Evidence item → card view-model (kind graph|text, short source label). */
export function describeEvidence(ev, index) {
  const path = parsePath(ev.content);
  return {
    id: ev.id,
    n: index + 1,
    kind: path ? "graph" : "text",
    label: path ? (path.edges.length > 1 ? "图谱路径" : "图谱事实") : "文本片段",
    text: ev.content || "",
    score: typeof ev.score === "number" ? ev.score : null,
    path,
  };
}

/** Citation numbers: evidence cited by claims first (in claim order), then the rest. */
export function citationIndex(claims, evidence) {
  const order = [];
  for (const claim of claims || []) {
    for (const id of claim.evidence_ids || []) if (!order.includes(id)) order.push(id);
  }
  const known = new Set((evidence || []).map((e) => e.id));
  const map = new Map();
  order.filter((id) => known.has(id)).forEach((id, i) => map.set(id, i + 1));
  return map;
}

/** Answer text split into [{type:'text'}|{type:'cite', n, id}] segments. */
export function buildAnswerSegments(answer, claims, evidence, maxCites = 3) {
  const text = String(answer || "");
  const index = citationIndex(claims, evidence);
  const segments = [];
  let cursor = 0;
  const trailing = [];
  for (const claim of claims || []) {
    const cites = (claim.evidence_ids || []).filter((id) => index.has(id)).slice(0, maxCites);
    if (!cites.length) continue;
    const at = claim.text ? text.indexOf(claim.text, cursor) : -1;
    const marks = cites.map((id) => ({ type: "cite", n: index.get(id), id }));
    if (at < 0) {
      trailing.push(...marks);
      continue;
    }
    const end = at + claim.text.length;
    segments.push({ type: "text", text: text.slice(cursor, end) }, ...marks);
    cursor = end;
  }
  if (cursor < text.length) segments.push({ type: "text", text: text.slice(cursor) });
  const seen = new Set(segments.filter((s) => s.type === "cite").map((s) => s.id));
  segments.push(...trailing.filter((m) => !seen.has(m.id) && seen.add(m.id)));
  return segments;
}

/** Steps → reasoning-chain nodes. */
export function buildPlanNodes(steps) {
  return (steps || []).map((step, i) => ({
    key: `${step.hop ?? i}-${i}`,
    hop: step.hop ?? i + 1,
    question: step.sub_question || "",
    conclusion: step.conclusion || "",
    critic: step.critic_action || "",
    dependsOn: step.depends_on || [],
    evidenceIds: step.evidence_ids || [],
    tools: (step.tool_calls || []).map((t) => ({
      tool: t.tool,
      reason: t.reason || "",
      hits: (t.hits || []).length,
    })),
  }));
}

export function describeThinkingEvent(payload) {
  const p = payload || {};
  const stage = STAGE_LABELS[p.stage] || p.stage || "思考";
  return { icon: "sparkle", title: `${stage} · ${p.text || "推理中"}`, detail: p.detail || "" };
}

/** One SSE event → { kind, icon, title, detail } for the live progress log. */
export function describeStreamEvent(event, payload) {
  const p = payload || {};
  switch (event) {
    case "cache_hit":
      return { kind: event, icon: "bolt", title: "命中答案缓存", detail: "" };
    case "triage": {
      const route = p.route === "fast" || p.route === "fast_path" ? "快速通道" : "深度推理";
      const hops = p.estimated_hops ? ` · 预计 ${p.estimated_hops} 跳` : "";
      return { kind: event, icon: "route", title: `路由：${route}${hops}`, detail: p.rationale || "" };
    }
    case "thinking":
      return { kind: event, ...describeThinkingEvent(p) };
    case "sub_question":
      return { kind: event, icon: "search", title: `第 ${p.hop ?? "?"} 跳 · ${p.sub_question || ""}`, detail: "" };
    case "hop_done":
      return {
        kind: event,
        icon: "check",
        title: `第 ${p.hop ?? "?"} 跳完成${p.conclusion ? `：${p.conclusion}` : ""}`,
        detail: p.critic_action ? `critic → ${p.critic_action}` : "",
      };
    case "answer":
      return { kind: event, icon: "check", title: "答案已生成", detail: "" };
    case "error":
      return { kind: event, icon: "alert", title: p.message || "推理失败", detail: p.code || "" };
    default:
      return { kind: event, icon: "info", title: event, detail: "" };
  }
}

function relax(nodes, links, { width, height, iterations = 240 }) {
  // Tiny deterministic force layout (Fruchterman–Reingold): repulsion between all
  // nodes, springs along edges, gravity to the centre, cooling step size.
  const k = Math.sqrt((width * height) / Math.max(1, nodes.length)) * 0.55;
  let step = width / 8;
  for (let it = 0; it < iterations; it++) {
    for (const a of nodes) {
      a.dx = (width / 2 - a.x) * 0.02;
      a.dy = (height / 2 - a.y) * 0.02;
      for (const b of nodes) {
        if (a === b) continue;
        const vx = a.x - b.x;
        const vy = a.y - b.y;
        const d2 = Math.max(1, vx * vx + vy * vy);
        a.dx += (vx * k * k) / d2 / 6;
        a.dy += (vy * k * k) / d2 / 6;
      }
    }
    for (const [a, b] of links) {
      const vx = a.x - b.x;
      const vy = a.y - b.y;
      const d = Math.max(1, Math.hypot(vx, vy));
      const f = (d * d) / k / d / 3;
      a.dx -= vx * f;
      a.dy -= vy * f;
      b.dx += vx * f;
      b.dy += vy * f;
    }
    for (const n of nodes) {
      const len = Math.max(1, Math.hypot(n.dx, n.dy));
      n.x = Math.min(width - 70, Math.max(70, n.x + (n.dx / len) * Math.min(len, step)));
      n.y = Math.min(height - 30, Math.max(24, n.y + (n.dy / len) * Math.min(len, step)));
    }
    step = Math.max(1, step * 0.97);
  }
}

/** Graph facts in the evidence → a small force-directed layout for the evidence graph. */
export function buildEvidenceGraph(evidence, answer, { maxNodes = 14, width = 720, height = 380 } = {}) {
  const edges = [];
  const degree = new Map();
  for (const ev of evidence || []) {
    const path = parsePath(ev.content);
    for (const e of path ? path.edges : []) {
      edges.push({ ...e, id: ev.id });
      for (const n of [e.from, e.to]) degree.set(n, (degree.get(n) || 0) + 1);
    }
  }
  const names = [...degree.entries()].sort((a, b) => b[1] - a[1]).slice(0, maxNodes).map(([n]) => n);
  if (names.length < 2) return null;
  const hit = String(answer || "").toLowerCase();
  const nodes = names.map((name, i) => {
    const angle = (i / names.length) * Math.PI * 2;
    return {
      name,
      x: width / 2 + Math.cos(angle) * width * 0.3,
      y: height / 2 + Math.sin(angle) * height * 0.3,
      answer: hit.includes(name.toLowerCase()),
    };
  });
  const at = new Map(nodes.map((n) => [n.name, n]));
  const shown = edges.filter((e) => at.has(e.from) && at.has(e.to) && e.from !== e.to);
  relax(nodes, shown.map((e) => [at.get(e.from), at.get(e.to)]), { width, height });
  nodes.forEach((n) => ((n.x = Math.round(n.x)), (n.y = Math.round(n.y)), delete n.dx, delete n.dy));
  return {
    width,
    height,
    nodes,
    edges: shown.map((e, i) => ({
      ...e,
      key: `${e.id}-${i}`,
      a: at.get(e.from),
      b: at.get(e.to),
      labelled: shown.length <= 10 || at.get(e.from).answer || at.get(e.to).answer,
    })),
  };
}
