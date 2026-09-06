/* Command-palette item builder: nav entries from the (role-filtered) view
 * registry plus shell actions. Pure function — the shell owns state.
 * `view` carries the registry id so the shell never parses the item id.
 */
import { MOON_ICON, SUN_ICON } from "./icons.js";

export function buildPaletteItems(navViews, theme, hasApiKey) {
  const nav = navViews.map((v) => ({
    id: `nav:${v.id}`,
    kind: "nav",
    view: v.id,
    label: v.title,
    hint: v.subtitle,
    icon: v.icon,
  }));
  const actions = [
    {
      id: "act:theme",
      kind: "action",
      label: theme === "dark" ? "切换到亮色主题" : "切换到暗色主题",
      hint: "外观",
      /* icon previews the theme being switched TO (matches the rail button). */
      icon: theme === "dark" ? SUN_ICON : MOON_ICON,
    },
  ];
  if (hasApiKey) {
    actions.push({ id: "act:clearkey", kind: "action", label: "清除 API Key（登出）", hint: "身份" });
  }
  return [...nav, ...actions];
}
