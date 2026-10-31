// @ts-check
/** Knowledge-import wizard helpers: client-side file validation, previews, task status. */
import { IngestStatus, UploadPolicy } from '../contracts.js';
import { text as T } from '../i18n.js';

/** @typedef {{file: File, name: string, bytes: number, ok: boolean, reason: string, preview: string}} StagedFile */

const KIB = 1024;
const PREVIEW_READ_BYTES = UploadPolicy.PREVIEW_CHARS * 4;

/** @param {File} file @returns {{ok: boolean, reason: string}} */
export function validateFile(file) {
  const ext = file.name.includes('.') ? file.name.split('.').pop()?.toLowerCase() ?? '' : '';
  if (!UploadPolicy.EXTENSIONS.includes(ext)) return { ok: false, reason: T.knowledge.badType };
  if (file.size > UploadPolicy.MAX_BYTES) return { ok: false, reason: T.knowledge.tooLarge };
  return { ok: true, reason: '' };
}

/** @param {File} file */
async function readPreview(file) {
  try { return (await file.slice(0, PREVIEW_READ_BYTES).text()).slice(0, UploadPolicy.PREVIEW_CHARS); }
  catch { return ''; }
}

/** Merge newly picked files into the staged list, enforcing the batch cap. @param {File[]} picked @param {StagedFile[]} existing */
export async function stageFiles(picked, existing) {
  const known = new Set(existing.map(f => `${f.name}:${f.bytes}`));
  const fresh = picked.filter(f => !known.has(`${f.name}:${f.size}`));
  const staged = await Promise.all(fresh.map(async file => {
    const verdict = validateFile(file);
    return {
      file, name: file.name, bytes: file.size, ok: verdict.ok, reason: verdict.reason,
      preview: verdict.ok ? await readPreview(file) : '',
    };
  }));
  return [...existing, ...staged].map((f, i) =>
    (i >= UploadPolicy.MAX_FILES && f.ok ? { ...f, ok: false, reason: T.knowledge.tooMany } : f));
}

/** @param {number} bytes */
export function formatBytes(bytes) {
  if (bytes < KIB) return `${bytes} B`;
  if (bytes < KIB * KIB) return `${(bytes / KIB).toFixed(1)} KiB`;
  return `${(bytes / KIB / KIB).toFixed(2)} MiB`;
}

/** @param {import('../contracts.js').IngestStatusName} status */
export function isActiveStatus(status) {
  return status === IngestStatus.QUEUED || status === IngestStatus.EXTRACTING;
}

/** @param {import('../contracts.js').IngestStatusName} status */
export function statusBadge(status) {
  const map = {
    [IngestStatus.QUEUED]: '', [IngestStatus.EXTRACTING]: 'tag--accent',
    [IngestStatus.REVIEW]: 'tag--warn', [IngestStatus.DONE]: 'tag--ok',
    [IngestStatus.FAILED]: 'tag--err', [IngestStatus.EMPTY]: '',
  };
  return map[status] || '';
}

/** @param {import('../contracts.js').IngestStatusName} status */
export function statusHint(status) {
  if (status === IngestStatus.DONE) return T.knowledge.done;
  if (status === IngestStatus.FAILED) return T.knowledge.failed;
  if (status === IngestStatus.REVIEW) return T.knowledge.review;
  return T.knowledge.polling;
}
