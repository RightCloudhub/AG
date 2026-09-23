/* Global component registration for the Vue workspace (ADR-006). */
import { AnswerTurn } from "./answer-turn.js";
import {
  PathList,
  PlanTree,
  ProgressLog,
  StepsList,
  ThinkingPanel,
} from "./widgets.js";
import { ChatView } from "../views/chat.js";
import { GraphView } from "../views/graph.js";
import { KnowledgeView } from "../views/knowledge.js";
import { OpsView } from "../views/ops.js";
import { ReviewView } from "../views/review.js";

export function registerComponents(app) {
  app.component("chat-view", ChatView);
  app.component("knowledge-view", KnowledgeView);
  app.component("review-view", ReviewView);
  app.component("graph-view", GraphView);
  app.component("ops-view", OpsView);
  app.component("answer-turn", AnswerTurn);
  app.component("progress-log", ProgressLog);
  app.component("thinking-panel", ThinkingPanel);
  app.component("plan-tree", PlanTree);
  app.component("path-list", PathList);
  app.component("steps-list", StepsList);
}
