/* View registry — the single source of truth for the hash/id/role/component
 * mapping (p5-ui-02 §4). Role semantics mirror the server: anonymous=reader
 * when auth is off; with a key, the key's role applies. Ranking:
 * anonymous < reader < operator < admin.
 */
const ROLE_RANK = Object.freeze({ anonymous: 0, reader: 1, operator: 2, admin: 3 });

export const VIEWS = Object.freeze([
  { id: "chat", title: "问答", subtitle: "单次独立提问 · 答案绑定证据引用", minRole: "anonymous", component: "chat-view" },
  { id: "knowledge", title: "知识运维", subtitle: "文档上传 · 抽取任务", minRole: "operator", component: "knowledge-view" },
  { id: "review", title: "审核", subtitle: "审核队列 · 决策与图谱写回", minRole: "operator", component: "review-view" },
  { id: "ops", title: "可观测", subtitle: "指标 · 预算 · 安全事件 · 审计回查", minRole: "admin", component: "ops-view" },
  { id: "graph", title: "图谱浏览", subtitle: "图实体分页浏览", minRole: "reader", component: "graph-view" },
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
