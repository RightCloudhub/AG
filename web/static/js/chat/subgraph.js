// @ts-check
/**
 * Parse graph-sourced evidence rows ("Head -[REL]-> Tail (Type)", path chains)
 * into a small node/edge model for the workspace subgraph tab. Only the
 * persisted evidence catalog is used — no neighbourhood API exists yet
 * (FeatureFlag.GRAPH_NEIGHBORS).
 */
import { EvidenceIdPrefix, UiPolicy } from '../contracts.js';

/** @typedef {import('../responses.js').Evidence} Evidence */
/** @typedef {import('./types.js').Subgraph} Subgraph */
/** @typedef {import('./types.js').SubgraphEdge} SubgraphEdge */
/** @typedef {import('./types.js').SubgraphNode} SubgraphNode */

const MARKER = /\s(<-\[[^\]]+\]-|-\[[^\]]+\]->)\s/;
const RELATION_IN_MARKER = /\[([A-Za-z_]+)/;
const TYPE_SUFFIX = /\s*\(([A-Za-z]+)\)\s*$/;
const BRACKET_SUFFIX = /\s*\[[^\]]*\]\s*$/;

/** @param {Evidence} row */
export function isGraphEvidence(row) {
  return row.id.startsWith(EvidenceIdPrefix.NEIGHBOR) || row.id.startsWith(EvidenceIdPrefix.PATH)
    || MARKER.test(row.content);
}

/** @param {string} segment @returns {{name: string, type: string}} */
function parseNode(segment) {
  let name = segment.trim().replace(BRACKET_SUFFIX, '');
  let type = '';
  const typed = TYPE_SUFFIX.exec(name);
  if (typed) {
    type = typed[1];
    name = name.replace(TYPE_SUFFIX, '');
  }
  return { name: name.trim(), type };
}

/** @param {string} content @returns {{head: string, relation: string, tail: string, headType: string, tailType: string}[]} */
export function parseEdges(content) {
  const parts = content.split(MARKER);
  /** @type {ReturnType<typeof parseEdges>} */
  const edges = [];
  for (let i = 1; i + 1 < parts.length; i += 2) {
    const marker = parts[i];
    const relation = RELATION_IN_MARKER.exec(marker)?.[1] ?? '';
    const left = parseNode(parts[i - 1]);
    const right = parseNode(parts[i + 1]);
    const forward = marker.startsWith('-');
    const [head, tail] = forward ? [left, right] : [right, left];
    if (head.name && tail.name && relation) {
      edges.push({ head: head.name, relation, tail: tail.name, headType: head.type, tailType: tail.type });
    }
  }
  return edges;
}

/** Neighbour evidence ids encode the query entity: nbr:{entity}:{REL}:{neighbour}:{i}. @param {string} id */
export function neighbourOf(id) {
  if (!id.startsWith(EvidenceIdPrefix.NEIGHBOR)) return null;
  const parts = id.slice(EvidenceIdPrefix.NEIGHBOR.length).split(':');
  const MIN_PARTS = 4;
  if (parts.length < MIN_PARTS) return null;
  return { entity: parts[0], relation: parts[1], neighbour: parts.slice(2, -1).join(':') };
}

/** @param {Evidence[]} evidence @returns {Subgraph} */
export function buildSubgraph(evidence) {
  /** @type {Map<string, SubgraphNode>} */
  const nodes = new Map();
  /** @type {Map<string, SubgraphEdge>} */
  const edges = new Map();
  for (const row of evidence.filter(isGraphEvidence)) {
    const neighbour = neighbourOf(row.id);
    for (const edge of parseEdges(row.content)) {
      const key = `${edge.head}|${edge.relation}|${edge.tail}`;
      const known = edges.get(key);
      edges.set(key, known
        ? { ...known, evidenceIds: [...known.evidenceIds, row.id] }
        : { id: key, head: edge.head, relation: edge.relation, tail: edge.tail, evidenceIds: [row.id] });
      touchNode(nodes, edge.head, nodeType(edge.head, edge.headType, edge.tailType, neighbour), row.id);
      touchNode(nodes, edge.tail, nodeType(edge.tail, edge.tailType, edge.headType, neighbour), row.id);
    }
  }
  return { nodes: [...nodes.values()], edges: [...edges.values()].slice(0, UiPolicy.SUBGRAPH_MAX_EDGES) };
}

/**
 * The "(Type)" suffix describes the neighbour entity, which may sit on either side.
 * @param {string} name @param {string} ownType @param {string} otherType
 * @param {{entity: string, neighbour: string} | null} neighbour
 */
function nodeType(name, ownType, otherType, neighbour) {
  if (ownType) return ownType;
  if (neighbour && neighbour.neighbour === name) return otherType;
  return '';
}

/** @param {Map<string, SubgraphNode>} nodes @param {string} name @param {string} type @param {string} evidenceId */
function touchNode(nodes, name, type, evidenceId) {
  const known = nodes.get(name);
  nodes.set(name, known
    ? { ...known, type: known.type || type, evidenceIds: [...known.evidenceIds, evidenceId] }
    : { name, type, evidenceIds: [evidenceId] });
}

/** Edge ids supported by the given evidence ids. @param {Subgraph} graph @param {string[]} evidenceIds */
export function edgesCitedBy(graph, evidenceIds) {
  const wanted = new Set(evidenceIds);
  return new Set(graph.edges.filter(e => e.evidenceIds.some(id => wanted.has(id))).map(e => e.id));
}
