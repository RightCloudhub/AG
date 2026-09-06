/* Global component registration (P5-UI-02): chat widgets (P5-UI-01, reused)
 * + console widgets + the five console views.
 */
import { AnswerTurn } from "./answer-turn.js";
import { DataTable, FilterBar, StatCard, StateCard } from "./console-widgets.js";
import { PathList, PlanTree, ProgressLog, StepsList, ThinkingPanel } from "./widgets.js";
import { ChatView } from "../views/chat.js";
import { GraphView } from "../views/graph.js";
import { KnowledgeView } from "../views/knowledge.js";
import { OpsView } from "../views/ops.js";
import { ReviewView } from "../views/review.js";

export function registerComponents(app) {
  app.component("answer-turn", AnswerTurn);
  app.component("progress-log", ProgressLog);
  app.component("thinking-panel", ThinkingPanel);
  app.component("plan-tree", PlanTree);
  app.component("path-list", PathList);
  app.component("steps-list", StepsList);
  app.component("stat-card", StatCard);
  app.component("state-card", StateCard);
  app.component("data-table", DataTable);
  app.component("filter-bar", FilterBar);
  app.component("chat-view", ChatView);
  app.component("knowledge-view", KnowledgeView);
  app.component("review-view", ReviewView);
  app.component("ops-view", OpsView);
  app.component("graph-view", GraphView);
}
