/* Component registry: map component kebab-case names to their definition
 * objects. All component modules are zero-Vue-import plain objects
 * (ADR-006). Registered with app.component() from app.js boot. */

import { progressLog } from "./widgets.js";
import { answerTurn } from "./answer-turn.js";

export function registerComponents(app) {
  app.component("progress-log", progressLog);
  app.component("answer-turn", answerTurn);
}
