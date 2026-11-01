// @ts-check
/** Static composition; user text always travels through Vue interpolation. @param {string} content */
export function publicFrame(content) {
  return `<main class="public-page">
  <header class="public-nav">
    <router-link to="/" class="topbar__brand"><ag-icon name="mark" :size="24"></ag-icon><span>{{ P.brand }}</span></router-link>
    <button type="button" class="btn btn--ghost" @click="toggleTheme" :aria-label="P.theme"><ag-icon name="contrast"></ag-icon><span>{{ P.theme }}</span></button>
  </header>
  <div class="auth-card">
    <section class="auth-brand" :aria-label="P.notebook">
      <div class="auth-brand__intro"><p class="auth-brand__name">{{ P.notebook }}</p>
        <h1>{{ P.tagline }}</h1><p class="auth-brand__description">{{ P.brandIntro }}</p></div>
      <ol class="auth-journey"><li v-for="(step, index) in P.journey" :key="step"><span class="auth-journey__number">{{ index + 1 }}</span>{{ step }}</li></ol>
    </section>
    <section class="auth-content">${content}</section>
  </div>
</main>`;
}
