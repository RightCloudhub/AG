/* View registry: one entry per workspace view. `capability` keys match the
 * capabilities map returned by GET /v1/me; the backend stays the final authority. */
export const VIEWS = [
  {
    id: "chat",
    label: "推理问答",
    desc: "多跳问题 · 流式推理 · 可追溯引用",
    icon: "chat",
    capability: "chat",
    group: "工作区",
    load: () => import("./chat.js"),
  },
  {
    id: "graph",
    label: "知识图谱",
    desc: "实体浏览、类型筛选，一键发起提问",
    icon: "graph",
    capability: "graph",
    group: "工作区",
    load: () => import("./graph.js"),
  },
  {
    id: "knowledge",
    label: "知识接入",
    desc: "上传文档，跟踪接入任务",
    icon: "knowledge",
    capability: "knowledge",
    group: "管理",
    load: () => import("./knowledge.js"),
  },
  {
    id: "review",
    label: "审核队列",
    desc: "抽检、冲突与用户反馈的人工决策",
    icon: "review",
    capability: "review",
    group: "管理",
    load: () => import("./review.js"),
  },
  {
    id: "ops",
    label: "运行观测",
    desc: "指标、预算、安全事件与查询追溯",
    icon: "ops",
    capability: "ops",
    group: "管理",
    load: () => import("./ops.js"),
  },
];

export const DEFAULT_VIEW = "chat";

export function findView(id) {
  return VIEWS.find((v) => v.id === id) || null;
}

/** Views the current identity may open, given /v1/me capabilities. */
export function visibleViews(capabilities) {
  const caps = capabilities || {};
  return VIEWS.filter((v) => Boolean(caps[v.capability]));
}

/** Visible views grouped for the rail: [{ group, views }]. */
export function groupedViews(capabilities) {
  const groups = [];
  for (const view of visibleViews(capabilities)) {
    let bucket = groups.find((g) => g.group === view.group);
    if (!bucket) groups.push((bucket = { group: view.group, views: [] }));
    bucket.views.push(view);
  }
  return groups;
}
