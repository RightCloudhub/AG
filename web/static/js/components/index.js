/* Global component registration (called once from app.js). */
import { AnswerTurn } from "./answer-turn.js";
import { AppIcon, DataPager, EvidenceGraph, StatCard, StateCard, ToastHost } from "./widgets.js";

const COMPONENTS = {
  "app-icon": AppIcon,
  "stat-card": StatCard,
  "state-card": StateCard,
  "data-pager": DataPager,
  "toast-host": ToastHost,
  "evidence-graph": EvidenceGraph,
  "answer-turn": AnswerTurn,
};

export function registerComponents(app) {
  for (const [name, component] of Object.entries(COMPONENTS)) app.component(name, component);
  return app;
}
