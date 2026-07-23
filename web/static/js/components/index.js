/* Global component registration for the trial UI (P5-UI-01 / ADR-006). */
import { AnswerTurn } from "./answer-turn.js";
import {
  PathList,
  PlanTree,
  ProgressLog,
  StepsList,
  ThinkingPanel,
} from "./widgets.js";

export function registerComponents(app) {
  app.component("answer-turn", AnswerTurn);
  app.component("progress-log", ProgressLog);
  app.component("thinking-panel", ThinkingPanel);
  app.component("plan-tree", PlanTree);
  app.component("path-list", PathList);
  app.component("steps-list", StepsList);
}
