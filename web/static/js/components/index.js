/* Global component registration for the trial UI (P5-UI-01/02 / ADR-006). */
import { AnswerTurn } from "./answer-turn.js";
import { AppShell } from "./layout.js";
import {
  PathList,
  PlanTree,
  ProgressLog,
  StepsList,
  ThinkingPanel,
} from "./widgets.js";
import { ChatView } from "../views/chat.js";
import { DashboardView } from "../views/dashboard.js";
import { GraphView } from "../views/graph.js";
import { HistoryView } from "../views/history.js";
import { ReviewView } from "../views/review.js";

export function registerComponents(app) {
  app.component("app-shell", AppShell);
  app.component("answer-turn", AnswerTurn);
  app.component("progress-log", ProgressLog);
  app.component("thinking-panel", ThinkingPanel);
  app.component("plan-tree", PlanTree);
  app.component("path-list", PathList);
  app.component("steps-list", StepsList);
  app.component("chat-view", ChatView);
  app.component("graph-view", GraphView);
  app.component("dashboard-view", DashboardView);
  app.component("review-view", ReviewView);
  app.component("history-view", HistoryView);
}
