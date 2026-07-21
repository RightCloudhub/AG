/* Pure view-model builders for the reasoning-chain UI. Framework-free:
 * every function maps API payloads to plain data that templates interpolate
 * via mustache / textContent only (rules.md §8 injection policy).
 */
export const MAX_PATH_ROWS = 40;

/* SSE progress notes. `answer`/`error` are handled by the root component;
 * unknown event types return null and are silently ignored (rules.md §8). */
const STREAM_NOTES = {
  cache_hit: () => ({ kind: "info", text: "缓存命中" }),
  triage: (p) => ({
    kind: "info",
    text: `分诊 → ${p.route}${p.rationale ? ` (${p.rationale})` : ""}`,
  }),
  sub_question: (p) => ({ kind: "info", text: `子问题 hop=${p.hop}: ${p.sub_question}` }),
  hop_done: (p) => ({
    kind: "info",
    text: `hop ${p.hop} 完成: ${p.conclusion || p.critic_action || ""}`,
  }),
};

export function describeStreamEvent(evt) {
  const format = STREAM_NOTES[evt.type];
  return format ? format(evt.payload || {}) : null;
}

export function confidenceLine(data) {
  const conf = (data.metadata && data.metadata.confidence) || {};
  const route = `路由 ${data.route || "—"} · ${data.status || "—"}`;
  if (!conf.level) return route;
  const score = conf.score != null ? ` · ${conf.score}` : "";
  return `置信度 ${conf.level}${score} · ${route}`;
}

export function buildChainSummary(data) {
  return {
    query_id: data.query_id,
    route: data.route,
    status: data.status,
    claims: data.claims,
    cost: data.cost,
    explored_paths: data.explored_paths,
  };
}

/* Answer body as text/cite segments; prefers inline claim-text markers,
 * else appends citation badges after the answer. */
export function buildAnswerSegments(answer, claims) {
  const text = answer || "(empty)";
  const list = Array.isArray(claims) ? claims : [];
  if (!list.length) return [{ type: "text", text }];
  const inline = inlineCitationSegments(text, list);
  if (inline) return inline;
  const segments = [{ type: "text", text: `${text} ` }];
  list.forEach((claim, i) => {
    segments.push(citeSegment(i + 1, claim));
    segments.push({ type: "text", text: " " });
  });
  return segments;
}

function inlineCitationSegments(text, claims) {
  const segments = [];
  let remaining = text;
  let matched = false;
  claims.forEach((claim, i) => {
    const claimText = (claim && claim.text) || "";
    const idx = claimText ? remaining.indexOf(claimText) : -1;
    if (idx < 0) return;
    matched = true;
    if (idx > 0) segments.push({ type: "text", text: remaining.slice(0, idx) });
    segments.push({ type: "text", text: claimText });
    segments.push(citeSegment(i + 1, claim));
    remaining = remaining.slice(idx + claimText.length);
  });
  if (!matched) return null;
  if (remaining) segments.push({ type: "text", text: remaining });
  return segments;
}

function citeSegment(n, claim) {
  const evidence = (claim && claim.evidence_ids) || [];
  return {
    type: "cite",
    n,
    title: evidence.length ? `证据: ${evidence.join(", ")}` : `论断 ${n}`,
  };
}

export function buildClaimItems(claims) {
  const list = Array.isArray(claims) ? claims : [];
  return list.map((claim, i) => ({
    n: i + 1,
    text: (claim && claim.text) || "(empty claim)",
    evidence: `证据: ${(((claim && claim.evidence_ids) || []).join(", ")) || "—"}`,
  }));
}

export function buildPlanNodes(steps) {
  const list = Array.isArray(steps) ? steps : [];
  return list.map((step, i) => ({
    key: i,
    head: `Hop ${step.hop != null ? step.hop : i + 1} · ${step.critic_action || "open"}`,
    question: step.sub_question || "",
    conclusion: step.conclusion || "",
    deps: (step.depends_on || []).join(", "),
    statusClass: planStatusClass(step.critic_action),
  }));
}

function planStatusClass(action) {
  const a = String(action || "").toLowerCase();
  if (a === "sufficient") return "ok";
  if (a === "give_up") return "fail";
  if (a === "rewrite" || a === "next_hop") return "partial";
  return "open";
}

export function buildStepItems(steps) {
  const list = Array.isArray(steps) ? steps : [];
  return list.map((step, i) => ({
    key: i,
    hopLabel: `Hop ${step.hop != null ? step.hop : i + 1} · ${step.critic_action || ""}`,
    subQuestion: step.sub_question || "",
    conclusion: step.conclusion || "—",
    evidence: (step.evidence_ids || []).join(", ") || "—",
    tools: (step.tool_calls || []).map((t) => t.tool).join(", ") || "—",
  }));
}

/* Explored paths → chip rows; the cap is surfaced, never silent. */
export function buildPathRows(paths) {
  const list = Array.isArray(paths) ? paths : [];
  const rows = list.slice(0, MAX_PATH_ROWS).map(parsePath);
  return { rows, hiddenCount: Math.max(0, list.length - MAX_PATH_ROWS) };
}

/* Common shapes: "A -[REL]-> B", "A -> B -> C", or free text. */
export function parsePath(raw) {
  const s = String(raw || "").trim();
  if (!s) return [{ kind: "node", text: "(empty)" }];
  const edgeRe = /\s*-\[([^\]]+)\]->\s*|\s*->\s*|\s*→\s*/g;
  const parts = [];
  let last = 0;
  let match;
  while ((match = edgeRe.exec(s)) !== null) {
    const node = s.slice(last, match.index).trim();
    if (node) parts.push({ kind: "node", text: node });
    const rel = match[1] ? match[1].trim() : "→";
    parts.push({ kind: "edge", text: rel === "→" ? "rel" : rel });
    last = match.index + match[0].length;
  }
  const tail = s.slice(last).trim();
  if (tail) parts.push({ kind: "node", text: tail });
  return parts.length ? parts : [{ kind: "node", text: s }];
}
