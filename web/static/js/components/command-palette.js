/* Command palette (⌘K / Ctrl+K): Linear-style quick switcher. Receives flat
 * items [{id, label, hint, icon, kind}] from the shell; emits run(item) and
 * close(). Owns filter text, arrow-key selection, Esc, and focus restore.
 */
export const CommandPalette = {
  name: "CommandPalette",
  props: {
    open: { type: Boolean, default: false },
    items: { type: Array, default: () => [] },
  },
  emits: ["close", "run"],
  data() {
    return { query: "", active: 0 };
  },
  computed: {
    filtered() {
      const q = this.query.trim().toLowerCase();
      if (!q) return this.items;
      return this.items.filter((it) =>
        `${it.label} ${it.hint || ""}`.toLowerCase().includes(q)
      );
    },
  },
  watch: {
    open(isOpen) {
      if (!isOpen) return;
      this.query = "";
      this.active = 0;
      this.$nextTick(() => {
        if (this.$refs.box) this.$refs.box.focus();
      });
    },
    filtered() {
      this.active = 0;
    },
  },
  methods: {
    onKey(event) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        this.active = Math.min(this.active + 1, Math.max(this.filtered.length - 1, 0));
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        this.active = Math.max(this.active - 1, 0);
      } else if (event.key === "Enter") {
        event.preventDefault();
        const item = this.filtered[this.active];
        if (item) this.run(item);
      } else if (event.key === "Escape") {
        event.preventDefault();
        this.$emit("close");
      }
    },
    run(item) {
      this.$emit("run", item);
      this.$emit("close");
    },
  },
  template: `
    <transition name="palette">
      <div v-if="open" class="palette-overlay" @click.self="$emit('close')">
        <div class="palette" role="dialog" aria-modal="true" aria-label="命令面板">
          <div class="palette-search">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
              stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="m21 21-4.35-4.35M17 10.5a6.5 6.5 0 1 1-13 0 6.5 6.5 0 0 1 13 0z"></path>
            </svg>
            <input ref="box" v-model="query" type="text" placeholder="跳转视图或执行操作…"
              aria-label="搜索命令" @keydown="onKey" />
            <kbd class="kbd">esc</kbd>
          </div>
          <ul class="palette-list" role="listbox" aria-label="命令列表">
            <li v-if="!filtered.length" class="palette-empty muted">没有匹配的命令</li>
            <li v-for="(it, i) in filtered" :key="it.id" role="option"
              :aria-selected="i === active" class="palette-item"
              :class="{ active: i === active }" @mouseenter="active = i" @click="run(it)">
              <svg v-if="it.icon" class="nav-icon" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" stroke-width="1.7" stroke-linecap="round"
                stroke-linejoin="round" aria-hidden="true"><path :d="it.icon"></path></svg>
              <span class="palette-label">{{ it.label }}</span>
              <span v-if="it.hint" class="palette-hint">{{ it.hint }}</span>
            </li>
          </ul>
          <div class="palette-foot muted">
            <span><kbd class="kbd">↑</kbd><kbd class="kbd">↓</kbd> 选择</span>
            <span><kbd class="kbd">↵</kbd> 执行</span>
            <span><kbd class="kbd">esc</kbd> 关闭</span>
          </div>
        </div>
      </div>
    </transition>
  `,
};
