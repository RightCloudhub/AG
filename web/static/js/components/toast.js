/* Toast notifications: module-level pub-sub bus + <toast-stack> component.
 * Views import pushToast() for transient feedback (key saved, decision
 * recorded, upload result); the stack mounts once in the shell template.
 * Mustache/textContent only (rules.md §8 injection policy).
 */
const LISTENERS = new Set();
const TOAST_MS = 3600;
let seq = 0;

export function pushToast(message, kind = "info") {
  const toast = { id: ++seq, message: String(message || ""), kind };
  for (const fn of LISTENERS) fn(toast);
  return toast.id;
}

export const ToastStack = {
  name: "ToastStack",
  data() {
    return { toasts: [] };
  },
  mounted() {
    this._onToast = (toast) => {
      this.toasts.push(toast);
      window.setTimeout(() => this.dismiss(toast.id), TOAST_MS);
    };
    LISTENERS.add(this._onToast);
  },
  beforeUnmount() {
    LISTENERS.delete(this._onToast);
  },
  methods: {
    dismiss(id) {
      this.toasts = this.toasts.filter((t) => t.id !== id);
    },
  },
  template: `
    <div class="toast-stack" role="status" aria-live="polite">
      <transition-group name="toast">
        <div v-for="t in toasts" :key="t.id" class="toast" :data-kind="t.kind">
          <span class="toast-dot" aria-hidden="true"></span>
          <span class="toast-msg">{{ t.message }}</span>
          <button type="button" class="toast-x" aria-label="关闭提示" @click="dismiss(t.id)">×</button>
        </div>
      </transition-group>
    </div>
  `,
};
