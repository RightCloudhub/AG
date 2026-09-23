export const VIEW_DEFINITIONS = Object.freeze([
  {
    id: "chat",
    title: "问答",
    subtitle: "多跳提问、证据引用与可审计推理链",
    icon: "问",
    component: "chat-view",
    capability: "chat",
  },
  {
    id: "knowledge",
    title: "知识库",
    subtitle: "管理文档接入与索引任务",
    icon: "库",
    component: "knowledge-view",
    capability: "knowledge",
  },
  {
    id: "review",
    title: "人工审核",
    subtitle: "处理待复核项并记录决策",
    icon: "审",
    component: "review-view",
    capability: "review",
  },
  {
    id: "graph",
    title: "知识图谱",
    subtitle: "搜索实体并浏览已接入知识",
    icon: "图",
    component: "graph-view",
    capability: "graph",
  },
  {
    id: "ops",
    title: "系统观测",
    subtitle: "运行指标、审计事件与查询追溯",
    icon: "运",
    component: "ops-view",
    capability: "ops",
  },
]);

export function visibleViews(identity) {
  const permissions = identity && identity.capabilities ? identity.capabilities : {};
  return VIEW_DEFINITIONS.filter((view) => permissions[view.capability] === true);
}

export function findView(id) {
  return VIEW_DEFINITIONS.find((view) => view.id === id) || null;
}

export function canOpenView(identity, view) {
  return Boolean(view && visibleViews(identity).some((allowed) => allowed.id === view.id));
}
