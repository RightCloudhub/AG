/* Sidebar navigation model (P5-UI-02). Pure data + helpers; the layout
 * component binds against these. Each entry maps to a registered view.
 */
export const NAV_ITEMS = Object.freeze([
  { name: "chat", label: "对话问答", icon: "✦" },
  { name: "graph", label: "知识图谱", icon: "◈" },
  { name: "dashboard", label: "指标仪表盘", icon: "▤" },
  { name: "review", label: "审核队列", icon: "✎" },
  { name: "history", label: "问答历史", icon: "≡" },
]);

export function navLabel(name) {
  const item = NAV_ITEMS.find((n) => n.name === name);
  return item ? item.label : "";
}

export function navIcon(name) {
  const item = NAV_ITEMS.find((n) => n.name === name);
  return item ? item.icon : "";
}

export function isActive(name, current) {
  return name === current;
}
