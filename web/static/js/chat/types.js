// @ts-check
/** Shared chat-domain typedefs (no runtime code). */

/** @typedef {import('../responses.js').Answer} Answer */
/** @typedef {import('../responses.js').Entity} Entity */
/** @typedef {import('../contracts.js').MessageRoleName} MessageRoleName */
/** @typedef {import('../contracts.js').StepPhaseName} StepPhaseName */
/** @typedef {import('../contracts.js').StepStateName} StepStateName */
/** @typedef {import('../contracts.js').ClarifyKindName} ClarifyKindName */
/** @typedef {import('../contracts.js').ConstraintKindName} ConstraintKindName */

/**
 * @typedef {object} HopProgress
 * @property {number} hop
 * @property {string} subQuestion
 * @property {number} startedAt
 * @property {number} elapsedMs
 * @property {string[]} tools
 * @property {number} hits
 * @property {string} conclusion
 * @property {string} criticAction
 * @property {StepStateName} state
 */

/**
 * @typedef {object} ProgressModel
 * @property {number} startedAt
 * @property {StepPhaseName} phase
 * @property {StepStateName} state
 * @property {string} planText
 * @property {string} planDetail
 * @property {string} note
 * @property {string} route
 * @property {boolean} cacheHit
 * @property {HopProgress[]} hops
 */

/** @typedef {{key: string, phase: StepPhaseName, hop: number, state: StepStateName}} StepBarItem */

/** One SSE frame as the inspector's 日志 shows it: when it arrived, its name, one line about it. */
/** @typedef {{at: string, name: string, summary: string}} LogFrame */

/** @typedef {{id: string, label: string, type: string, name: string}} ClarifyOption */

/**
 * @typedef {object} ClarifyCard
 * @property {ClarifyKindName} kind
 * @property {string} mention
 * @property {ClarifyOption[]} options
 * @property {boolean} multi
 * @property {string} question
 */

/**
 * @typedef {object} ChatMessage
 * @property {MessageRoleName} role
 * @property {number} ts
 * @property {string} [text]
 * @property {Answer} [data]
 * @property {ProgressModel | null} [progress]
 * @property {LogFrame[]} [log]
 * @property {ClarifyCard | null} [clarify]
 * @property {boolean} [resolved]
 * @property {boolean} [feedbackSent]
 * @property {boolean} [feedbackValue]
 * @property {string} [feedbackReason]
 * @property {string[]} [followups]
 */

/** A question page (§4.4): the question, the rows under it, its answer, and whether it is streaming now. */
/**
 * @typedef {object} PageModel
 * @property {string} key
 * @property {number} index
 * @property {string} question
 * @property {number} userIndex
 * @property {{msg: ChatMessage, index: number}[]} rows
 * @property {{msg: ChatMessage, index: number} | null} answer
 * @property {boolean} live
 */

/**
 * @typedef {object} Conversation
 * @property {string} id
 * @property {string} title
 * @property {number} createdAt
 * @property {number} updatedAt
 * @property {ChatMessage[]} messages
 */

/**
 * @typedef {object} RelationOption
 * @property {string} id
 * @property {string} relation
 * @property {boolean} inverse
 * @property {string} label
 * @property {string[]} from
 * @property {string} to
 * @property {boolean} many
 * @property {(x: string) => string} phrase
 * @property {string} verb
 */

/**
 * @typedef {object} WizardState
 * @property {number} step
 * @property {{name: string, type: string} | null} entity
 * @property {string} entityQuery
 * @property {string[]} chain
 * @property {string} time
 * @property {boolean} count
 * @property {{relation: string, entity: string} | null} intersect
 * @property {string} override
 */

/** @typedef {{name: string, type: string, evidenceIds: string[]}} SubgraphNode */
/** @typedef {{id: string, head: string, relation: string, tail: string, evidenceIds: string[]}} SubgraphEdge */
/** @typedef {{nodes: SubgraphNode[], edges: SubgraphEdge[]}} Subgraph */

export {};
