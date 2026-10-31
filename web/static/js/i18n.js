// @ts-check
/**
 * UI copy (§9): simplified Chinese, full-width punctuation, a space between a figure and its unit,
 * verbs first, and every string in this file — components hold none.
 */
export const Locale = Object.freeze({ ZH_CN: 'zh-CN' });

export const zh = Object.freeze({
  app: {
    title: 'AgenticGraphRAG', subtitle: '推理笔记本', loadFailed: '视图加载失败',
    menu: '打开菜单', closeMenu: '关闭菜单',
  },
  nav: { chat: '问答', graph: '知识图谱', knowledge: '知识库', review: '人工审核', ops: '系统运维' },
  topbar: {
    scope: '知识库', scopeHint: '当前租户', search: '搜索问题或命令…', searchAria: '搜索问题或命令',
    mode: '推理模式', theme: '切换主题', themeDark: '靛夜', themeLight: '晨纸', settings: '设置',
    health: '服务状态', healthOk: '服务正常', healthDegraded: '服务降级', healthUnknown: '未连接',
    healthDown: '无法连接服务',
  },
  notebook: {
    new: '新问题', history: '会话历史', admin: '管理', open: '打开笔记本', close: '关闭笔记本',
    empty: '还没有问题。问一个需要多步推理的问题，它会出现在这里。',
    remove: '删除', today: '今天', week: '本周', earlier: '更早', unnamed: '未命名的问题',
  },
  page: {
    yourQuestion: '你的问题', edit: '改写问题', save: '重新提问', cancel: '取消',
    editHint: '改写后生成新的一页，原页保留。',
  },
  hoprail: {
    label: '推理路径', plan: '规划', planning: '规划子问题…', hop: '第 {n} 跳', answer: '作答',
    evidence: '{n} 条证据', elapsed: '{ms} ms', retry: '从此跳重跑', focus: '在第 {n} 跳的证据中筛选',
    aria: '第 {hop} 跳，{state}，{n} 条证据', ariaAnswer: '{label}，{state}', summary: '{n} 跳', live: '推理进行中', path: '路径',
    cache: '命中答案缓存',
    states: { pending: '等待', active: '进行中', done: '已完成', failed: '失败', stopped: '已停止' },
  },
  answer: {
    notFoundTitle: '未能得出结论', reason: '原因', missing: '缺少的信息', supply: '去补充知识',
    partial: '基于部分路径', copy: '复制', copyAnswer: '复制结论', copyId: '复制 query_id', copied: '已复制',
    followups: '继续追问', finding: '{hops} 跳 · {evidence} 条证据 · 置信度 {confidence}',
    noConfidence: '未评估', meta: '{hops} 跳 · {time} · {calls} 次调用 · {tokens} tok', askAgain: '重新提问',
    confidenceTag: '置信度 {level} {pct}%',
    confidence: '置信度', confidenceLevels: { high: '高', medium: '中', low: '低' },
    status: { answered: '已回答', partial: '部分回答', no_answer: '未找到答案' },
    routes: { agentic: '多跳', fast_path: '快速', baseline: '基线', chitchat: '闲聊' },
    critics: { sufficient: '充分', next_hop: '继续下一跳', rewrite: '改写子问题', give_up: '放弃' },
    cites: '引用', openEvidence: '查看证据 {n}', snippet: '原文摘录',
  },
  composer: {
    placeholder: '问一个需要多步推理的问题，@ 引用实体，/ 打开命令',
    ariaInput: '问题输入框', send: '发送', sendAria: '发送问题', stop: '停止生成', stopAria: '停止生成',
    hint: 'Enter 发送 · Shift+Enter 换行',
    mention: '选择实体', mentionEmpty: '没有匹配的实体', mentionHint: '↑↓ 选择 · Enter 确认',
    slashHint: '命令', slashEmpty: '没有匹配的命令',
    planUnavailable: '后端尚未提供计划预览接口（/v1/query/plan），本次按「多跳」模式直接执行。',
    rerunUnavailable: '后端尚未提供「从某一跳重跑」接口，已把该跳子问题填回输入框。',
  },
  modes: { auto: '快速', agentic: '多跳', plan_confirm: '先确认计划' },
  commands: { wizard: '引导提问', plan: '先确认计划', import: '知识导入' },
  plan: { run: '执行', cancelled: '已取消执行。' },
  welcome: {
    title: '问一个需要多步推理的问题',
    groups: { twoHop: '2 跳', threeHop: '3 跳', compare: '比较', intersect: '交集' },
  },
  inspector: {
    title: '检查器', tabs: { evidence: '证据', subgraph: '子图', log: '日志' },
    filterAll: '全部', filterHop: 'H{n}', sortLabel: '排序', sortScore: '按分数', sortHop: '按跳',
    empty: '选择一条回答后，这里显示它的证据、子图与日志。', evidenceEmpty: '本题没有检索到证据',
    subgraphEmpty: '后端暂未提供路径数据', open: '打开检查器', close: '关闭检查器', toggle: '打开或关闭检查器',
    count: '{n} 条证据 · {hops} 跳', expand: '展开全文', collapse: '收起', highlight: '在子图中高亮',
    openInGraph: '在图谱视图中查看', nodes: '节点', edges: '路径', truncated: '（已截断）',
    sourceDoc: '文档 {doc} · 第 {n} 段', sourceGraph: '图谱 · {relation}', sourceGraphPlain: '图谱',
    score: '得分', graphRow: '图谱行',
  },
  log: {
    route: '路由', queryId: 'query_id', trace: '事件流水', copy: '复制日志', copied: '已复制',
    empty: '还没有事件。问题开始推理后，事件会逐条出现在这里。',
    event: '{name}', started: '开始', finished: '完成', frames: '{n} 条事件',
  },
  clarify: {
    entityTitle: '「{mention}」有多个匹配，你指的是哪一个？', broadTitle: '这个问题比较宽泛，要补充一些信息吗？',
    missingTitle: '答案不完整，是否补充以下信息后重新提问？',
    openWizard: '用向导补全', sendAnyway: '直接发送', continueWith: '按所选继续', skip: '跳过', multiHint: '可多选',
  },
  wizard: {
    title: '引导提问', subtitle: '四步生成一个多跳问题，随时可以跳过。',
    steps: ['起始实体', '关系链', '约束', '预览'],
    entityLabel: '从哪个实体出发？', entityPlaceholder: '输入实体名称，如 Apex Holdings',
    entityEmpty: '没有找到实体，可以直接使用输入的名称。', useTyped: '直接使用「{name}」',
    chosen: '已选择', relationLabel: '沿着哪些关系走？（最多 {max} 步）', chainEmpty: '还没有选择关系',
    relationCatalogNote: '关系类型来自 configs/schema/domain_v0.yaml；后端提供 /v1/graph/relations 后将改为在线读取。',
    constraintLabel: '需要加约束吗？', time: '时间点（如 2024）', count: '只问数量',
    intersect: '交集：结果还要满足', intersectEntity: '交集实体', previewLabel: '生成的问题（可直接修改）',
    planLabel: '子问题草案（可删除、重排）', planHint: '顺序即执行顺序；删除会同步更新问题。',
    generate: '填入输入框', generateAndSend: '填入并发送', keyboard: 'Enter 下一步 · Esc 跳过',
    prevResult: '上一步结果',
  },
  feedback: {
    prompt: '这个回答有帮助吗？', up: '有帮助', down: '没帮助', sent: '感谢反馈',
    reasonTitle: '哪里出了问题？',
    reasons: { wrong_entity: '实体错', wrong_chain: '链路错', missing_answer: '缺答案', extra_answers: '多答' },
    failed: '反馈提交失败',
  },
  palette: {
    title: '命令面板', placeholder: '搜索问题、视图或命令', empty: '没有匹配的结果',
    kinds: { question: '问题', view: '视图', mode: '模式', theme: '主题', action: '动作' },
    newQuestion: '新问题', switchTheme: '切换到{theme}', hints: '↑↓ 选择 · Enter 打开 · Esc 关闭',
  },
  settings: {
    title: '设置', apiKey: 'API Key', apiKeyPlaceholder: '输入 API Key', apiKeyHint: '仅保存在本机浏览器；留空即清除。',
    hops: '跳数预算', hopsHint: '1–20 之间，默认 6。', language: '语言', languageValue: '简体中文',
    languageHint: '当前仅有简体中文。', close: '关闭', scope: '身份', shortcuts: '快捷键',
    theme: '主题', themeHint: '跟随系统，或手动选择。',
  },
  shortcuts: {
    palette: '命令面板', newQuestion: '新问题', send: '发送', stop: '停止生成', prevHop: '上一跳',
    nextHop: '下一跳', inspector: '打开或关闭检查器', theme: '切换主题',
  },
  states: {
    connecting: '连接中…', stopped: '已停止', stoppedAt: '已在第 {n} 跳停止。', retry: '重试', offline: '无法连接服务，请检查网络后重试。',
    offlineTitle: '连接失败', notFoundReason: '现有知识中没有找到相关事实。',
  },
  common: {
    next: '下一步', back: '上一步', skip: '跳过', cancel: '取消', confirm: '确认', apply: '采用', close: '关闭',
    copy: '复制', copied: '已复制', send: '发送', stop: '停止', retry: '重试', loading: '加载中',
    none: '暂无', search: '搜索', remove: '删除', moveUp: '上移', moveDown: '下移', unknownError: '未知错误',
    optional: '可选', refresh: '刷新', more: '更多', expand: '展开', collapse: '收起', count: '{n} 项',
  },
  thread: { newThread: '新问题', question: '问题' },
  graph: {
    title: '知识图谱', entities: '{n} 个实体', search: '搜索实体', filterType: '筛选类型', empty: '没有匹配的实体',
    unknownType: '未知类型', page: '第 {page} 页，共 {pages} 页', prev: '上一页', next: '下一页',
  },
  knowledge: {
    title: '知识库构建', wizardTitle: '知识导入向导', steps: ['选择文档', '预览', '确认入库', '查看进度'],
    dropHint: '拖拽 Markdown 或文本文档到此处，或点击浏览文件',
    dropRule: 'UTF-8 · .md / .txt · 每文件最大 5 MiB · 单次最多 20 个文件',
    selected: '已选择 {n} 个文件', invalid: '不符合要求', tooLarge: '超过 5 MiB', badType: '仅支持 .md / .txt',
    tooMany: '最多 20 个文件', previewTitle: '文档预览',
    extractionUnavailable: '后端尚未提供抽取预览接口（/v1/docs/preview）。文档入库后由 worker 异步抽取，结果会进入人工审核队列。',
    confirmTitle: '确认入库', confirmHint: '确认后上传文档并创建接入任务。', uploading: '上传中',
    progressTitle: '接入进度', taskId: '任务 ID', status: '状态', docs: '文档', message: '说明', created: '创建时间',
    polling: '每 2 秒刷新一次', done: '任务已完成', failed: '任务失败', review: '等待人工审核',
    gotoReview: '前往审核', newImport: '再导入一批', tasksTitle: '接入任务', refresh: '刷新', browse: '浏览文件',
    noTasks: '还没有接入任务。导入第一批文档后，任务会出现在这里。', total: '共 {n} 个任务',
    uploadFailed: '上传失败：{msg}', page: '第 {page} 页，共 {pages} 页', prev: '上一页', next: '下一页',
  },
  review: {
    title: '人工审核', pending: '待审核', approved: '已批准', rejected: '已拒绝',
    empty: '没有{status}的项目', id: 'ID', type: '类型', status_: '状态', confidence: '置信度', detail: '详情',
    actions: '操作', approve: '通过', reject: '拒绝', skip: '跳过', total: '共 {n} 项',
    failed: '审核失败：{msg}', pendingCount: '{n} 个待审核',
  },
  ops: {
    title: '系统运维', metrics: '监控指标', budget: '配额分析', audit: '安全审计',
    totalQueries: '总查询数', p50: '延迟 P50', p95: '延迟 P95', p99: '延迟 P99', budgetTrips: '配额耗尽次数',
    routes: '路由分布', errors: '错误统计', health: '健康检查', check: '检查项', status_: '状态',
    noMetrics: '暂无指标数据', noBudget: '暂无配额数据', noAudit: '暂无审计事件',
    time: '时间', action: '操作', target: '目标', outcome: '结果', tenant: '租户',
  },
});

/** Active locale table (single locale today). */
export const text = zh;

/** @param {string} template @param {Record<string, string | number>} vars */
export function fmt(template, vars) {
  return template.replace(/\{(\w+)\}/g, (_, key) => String(vars[key] ?? ''));
}
