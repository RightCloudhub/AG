/* View registry — the single source of truth for the hash/id/role/component
 * mapping (p5-ui-02 §4). Role semantics mirror the server: anonymous=reader
 * when auth is off; with a key, the key's role applies. Ranking:
 * anonymous < reader < operator < admin. `icon` is a stroke-path d string
 * rendered by <nav-icon> (Lucide-style, 24×24).
 */
const ROLE_RANK = Object.freeze({ anonymous: 0, reader: 1, operator: 2, admin: 3 });

export const VIEWS = Object.freeze([
  { id: "chat", title: "问答", subtitle: "单次独立提问 · 答案绑定证据引用", minRole: "anonymous", component: "chat-view", icon: "M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" },
  { id: "knowledge", title: "知识运维", subtitle: "文档上传 · 抽取任务", minRole: "operator", component: "knowledge-view", icon: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8" },
  { id: "review", title: "审核", subtitle: "审核队列 · 决策与图谱写回", minRole: "operator", component: "review-view", icon: "m9 11 3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" },
  { id: "ops", title: "可观测", subtitle: "指标 · 预算 · 安全事件 · 审计回查", minRole: "admin", component: "ops-view", icon: "M22 12h-4l-3 9L9 3l-3 9H2" },
  { id: "graph", title: "图谱浏览", subtitle: "图实体分页浏览", minRole: "reader", component: "graph-view", icon: "M21 5a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM9 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM21 19a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM8.6 13.5l6.8 4M15.4 6.5l-6.8 4" },
]);

export const DEFAULT_VIEW_ID = "chat";

export function roleAllows(minRole, role) {
  const have = ROLE_RANK[role] ?? ROLE_RANK.anonymous;
  const need = ROLE_RANK[minRole] ?? ROLE_RANK.anonymous;
  return have >= need;
}

export function viewById(id) {
  return VIEWS.find((v) => v.id === id) || null;
}
