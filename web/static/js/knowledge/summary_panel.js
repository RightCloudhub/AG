// @ts-check
import { summaryState } from './summary_state.js';
import { knowledgeText as K, text as T, fmt } from '../i18n.js';

export default {
  name: 'SummaryStatus',
  setup() { return { ...summaryState(), K, T, fmt }; },
  template: `<section class="summary-status" :aria-label="K.summaryIndex" :aria-busy="loading">
    <div class="summary-status__heading"><h3>{{ K.summaryIndex }}</h3>
      <button class="btn btn--ghost btn--sm" type="button" @click="reload" :disabled="loading || busy">{{ K.refresh }}</button>
    </div>
    <p v-if="!status && loading" role="status">{{ K.summaryLoading }}</p>
    <template v-if="status">
      <p class="summary-status__state" :class="{ 'summary-status__state--warn': status.failed || status.partial }">{{ K.summaryStates[status.status] || status.status }}</p>
      <template v-if="status.enabled">
        <p>{{ fmt(K.summaryCoverage, { ready: status.ready, total: status.total, partial: status.partial }) }}</p>
        <progress v-if="status.total" :value="status.ready" :max="status.total" :aria-label="K.summaryCoverageLabel"></progress>
        <p class="hint">{{ fmt(K.summaryJobs, { pending: status.pending, running: status.running, failed: status.failed }) }}</p>
        <button v-if="canManage && status.status !== 'ready'" class="btn btn--secondary btn--sm" type="button" @click="run()" :disabled="busy || !status.total || status.pending > 0 || status.running > 0">{{ busy ? K.summaryWorking : K.summaryBuild }}</button>
        <ul v-if="jobs.length" class="summary-status__failures" :aria-label="K.summaryFailed">
          <li v-for="job in jobs" :key="job.id"><span>{{ K.summaryKinds[job.kind] || job.kind }} · {{ job.artifact }}<small>{{ job.error }} · {{ fmt(K.summaryAttempts, { n: job.attempts }) }}</small></span>
            <button class="btn btn--ghost btn--sm" type="button" @click="run(job.id)" :disabled="busy">{{ K.retry }}</button>
          </li>
        </ul>
        <p v-if="status.failed > jobs.length && canManage" class="hint">{{ K.summaryMoreFailures }}</p>
      </template>
    </template>
    <p v-if="error" class="row-error" role="alert">{{ error }}</p>
  </section>`,
};
