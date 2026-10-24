// @ts-check
import { SseField, isStreamEvent } from './contracts.js';

/** @typedef {import('./contracts.js').StreamHandlers} StreamHandlers */

export class SseParser {
  /** @param {StreamHandlers} handlers */
  constructor(handlers) {
    this.handlers = handlers;
    this.buffer = '';
    this.event = '';
    /** @type {string[]} */
    this.data = [];
  }

  /** @param {string} text */
  feed(text) {
    this.buffer += text;
    const lines = this.buffer.split('\n');
    this.buffer = lines.pop() || '';
    for (const line of lines) this.line(line.replace(/\r$/, ''));
  }

  finish() {
    if (this.buffer) this.line(this.buffer.replace(/\r$/, ''));
    this.buffer = '';
    this.dispatch();
  }

  /** @param {string} line */
  line(line) {
    if (!line) {
      this.dispatch();
      return;
    }
    if (line.startsWith(SseField.EVENT)) {
      this.event = line.slice(SseField.EVENT.length).trim();
    } else if (line.startsWith(SseField.DATA)) {
      this.data.push(line.slice(SseField.DATA.length).replace(/^ /, ''));
    }
  }

  dispatch() {
    const event = this.event;
    const lines = this.data;
    this.event = '';
    this.data = [];
    if (!lines.length || !isStreamEvent(event)) return;
    const handler = this.handlers[event];
    if (!handler) return;
    const text = lines.join('\n');
    /** @type {unknown} */
    let payload;
    try { payload = JSON.parse(text); } catch { payload = text; }
    handler(payload, event);
  }
}

/** @param {ReadableStream<Uint8Array>} body @param {StreamHandlers} handlers */
export async function consumeStream(body, handlers) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser(handlers);
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.feed(decoder.decode(value, { stream: true }));
    }
    parser.feed(decoder.decode());
    parser.finish();
  } finally {
    reader.releaseLock();
  }
}
