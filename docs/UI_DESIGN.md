# UI 视觉设计基准 —「制图室」方向（P5-UI-02 配套）

**版本**：V1.1（2026-09-07）· **状态**：**已被 P5-UI-03 结构化视觉改造取代，未实施**——2026-09-07 实际落地的是「双主题暗色优先」方向（Vercel/Linear 风：`tokens.css` 双主题令牌、zinc 中性色 + 靛蓝 accent、系统 sans 字体栈、`prefers-reduced-motion` 全量降级），见 [p5-ui-02-console-replan.md](../plan/phases/p5-ui-02-console-replan.md) M2b 备注。本文件以下内容保留为历史设计提案（「制图室」：宋体标题 + 图纸网格 + 朱砂印章），若日后启用需按 §9 重新验收并先改本文件使之一致。
**约束母文件**：[rules.md](../plan/engineering/rules.md) §8 · ADR-006（零构建，不变）
**当前生效的视觉口径**：`web/static/tokens.css`（令牌唯一真源）+ 各 CSS 模块；`web/static/*.css` 与本文件历史提案冲突时以 tokens.css 实际值为准。

---

## 1. 概念：制图室（Drafting Room）

本系统的心智模型是**把知识图谱当一张工程图纸来读**：实体是节点、关系是标注线、推理链是审图记录、审核决策是盖章。视觉方向据此定为「制图室」——

- **纸**：暖调图纸纸面 + 极淡制图网格与纸纹（内容卡为纯白图幅，四角有图框刻线）。
- **墨**：正文与结构线用墨青（ink navy），替代现在的近黑；层级靠墨色深浅而非灰阶。
- **章**：唯一强调色为**朱砂**（seal vermilion）——引用角标、激活态、焦点环、以及「印章状态语言」（§7.6）。
- **字**：标题/品牌用**宋体衬线**展示层（CJK 语境里罕见于工具类 UI，成为最强识别点）；正文保持无衬线可读性；一切 id / 指标 / 路径用等宽。

一句话记忆点：**宋体标题 + 图纸网格 + 朱砂印章**。现状「Claude 风格壳」是 P5-UI-01 已交付形态；重构时按本基准做**值级刷新**（改令牌值、加装饰类，不重写 DOM/JS——与 M2「行为冻结」不冲突）。

## 2. 硬约束映射（设计在什么笼子里跳舞）

| 约束 | 来源 | 对设计的含义 |
|---|---|---|
| 零构建、无新运行时依赖 | ADR-006 / rules §8 | 无 CSS 预处理器、无图标库、无动效库；全部原生 CSS |
| 离线优先 | rules §6/§8 | **禁止 CDN 字体与任何外链资源**；字体仅系统栈，或走 vendored 文件 + EXTERNAL_RUNTIMES 流程（可选项，非默认） |
| 每文件 ≤300 行 | rules §1/§8 | 令牌独立成 `tokens.css`（≤140）；装饰配方按视图落到对应 CSS |
| 禁 `v-html`/`innerHTML` | rules §5/§8 | 装饰一律 CSS（`::before/::after`）；印章是真文本元素 |
| 可达性 | 本文件 §9 | 文本对比 ≥4.5:1（AA）；`prefers-reduced-motion` 全量降级；焦点环可见 |

## 3. 字体系统（离线安全）

```css
--font-display: "Songti SC", "Noto Serif CJK SC", "Source Han Serif SC",
                STSong, SimSun, Georgia, serif;            /* 标题/品牌/印章/视图名 */
--font:         "PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC",
                "Microsoft YaHei", "Segoe UI", sans-serif; /* 正文/控件 */
--font-mono:    ui-monospace, "SF Mono", Menlo, Consolas,
                "Liberation Mono", monospace;              /* id/指标/路径/行号 */
```

- 现栈中的 `"Söhne"`、`"JetBrains Mono"` 首位项**从未在本环境解析**（未 vendored）——删除，不留装饰性死项。
- 展示层规格：视图标题 20px/600/字距 .02em；品牌字标 17px/600；印章见 §7.6。正文 14.5px/1.6；辅助 12.5px；等宽一律 `font-variant-numeric: tabular-nums`。
- **可选增强**（默认不做）：vendored 拉丁展示字体（如 OFL 的 Fraunces 子集）与等宽字体文件放 `web/static/vendor/fonts/`，须同步 `EXTERNAL_RUNTIMES.md` + vendor README；`@font-face` 仅允许指向本地 vendored 文件。

## 4. 色彩令牌（值级刷新 `app.css :root` → 迁入 `tokens.css`）

| 令牌 | 现值 | 新值 | 用途 |
|---|---|---|---|
| `--bg` | `#f5f2eb` | `#f6f2e9` | 图纸纸面（近乎延续，靠 §5 质感区分） |
| `--bg-elevated` | `#faf8f4` | `#faf7f0` | 顶部渐晕高光 |
| `--surface` | `#ffffff` | `#ffffff` | 图幅（内容卡） |
| `--text` | `#1f1e1b` | `#22304c` | **墨青**正文/标题 |
| `--text-secondary` | `#5c574e` | `#57627c` | 次级墨 |
| `--muted` | `#8a8478` | `#778099` | 弱化墨（仅辅助文字） |
| `--border` / `--border-strong` | `#e6e0d4` / `#d4cbbd` | `#ded6c4` / `#c9bfa9` | 细线 / 图框刻线 |
| `--accent` | `#c96442` | `#c2401f` | **朱砂**：引用角标、链接、激活、焦点环 |
| `--accent-hover` / `--accent-soft` | `#b4532a` / `#f3e0d8` | `#a33517` / `#f6e2d9` | 悬停 / 朱砂底纹 |
| `--good` / `--good-bg` | `#2f6f4e` / `#e6f2ea` | `#35694d` / `#e7f1ea` | 成功（印章「通过」同源） |
| `--bad` / `--bad-bg` | `#a33b3b` / `#f8e8e6` | `#9e2b25` / `#f7e7e4` | 错误（与朱砂拉开明度） |
| `--warn` | `#c9a227` | `#8a5a12`（文本）+ `--warn-glyph: #c9a227`（图形） | 现值对纸面对比不足，拆双令牌 |
| （新增） | — | `--grid-line: rgba(34,48,76,.05)` | 制图网格 |
| （新增） | — | `--node-ink: #2f5d68` / `--edge-ink: #8a5a12` | 图路径 chips：节点石青 / 边赭 |
| （新增） | — | `--stamp-red: #b23a24` | 印章红（与 `--accent` 分工：印章专用） |
| `--radius` / `--radius-sm` | `16px` / `10px` | `12px` / `8px` | 制图感：更利落的圆角 |

**朱砂纪律**：`--accent` 是唯一强调色，只出现在可交互/激活/引用处；状态色（good/warn/bad）不做大面积填充，只用于文字、印章与 2px 指示线。禁止引入本表之外的新颜色字面量（rules §1 魔法数字条款同样适用于色值——一律走令牌）。

## 5. 纸面质感（CSS-only 配方）

壳背景 = 纸纹 + 24px 制图网格 + 顶部渐晕（三层叠加，无图片资源）：

```css
body.claude-app {
  background:
    repeating-linear-gradient(45deg, rgba(34,48,76,.012) 0 2px, transparent 2px 4px),
    linear-gradient(var(--grid-line) 1px, transparent 1px),
    linear-gradient(90deg, var(--grid-line) 1px, transparent 1px),
    radial-gradient(1200px 800px at 50% -10%, var(--bg-elevated), var(--bg));
  background-size: auto, 24px 24px, 24px 24px, auto;
}
```

图幅卡四角「图框刻线」（工程图角标记；用于答案卡 / 仪表卡 / 状态卡）：

```css
.card-tick::before, .card-tick::after {
  content: ""; position: absolute; width: 10px; height: 10px;
  border: 0 solid var(--border-strong); pointer-events: none;
}
.card-tick::before { top: -1px; left: -1px; border-top-width: 2px; border-left-width: 2px; }
.card-tick::after  { bottom: -1px; right: -1px; border-bottom-width: 2px; border-right-width: 2px; }
```

阴影维持极轻双层（现 `--shadow` 保留）；不做毛玻璃、不做大渐变色块。

## 6. 版式与空间

- **侧栏 = 图签栏**（280px 不变）：顶部宋体品牌字标；导航项带等宽序号 `01 问答 / 02 知识 / 03 审核 / 04 观测 / 05 图谱`（角色不足的项不渲染，见 p5-ui-02 §4）；底部是「图签块」——细线框内等宽小字：tenant / role / 健康点 / 版本，模仿图纸标题栏。
- 问答视图中栏 760px（不变，行为冻结）；知识/审核/观测/图谱内容区 max 1040px，8px 基距（8/12/16/24/32）。
- 观测视图首行 4 张仪表卡（§7.4），下方双栏不对称分割（预算 1/3 + 事件表 2/3），表格允许高密度。
- 层级手段只有三种：墨色深浅、细线、留白。**不用**大色块分区。

## 7. 组件视觉规范

1. **引用角标**（chat/审计回查共用）：朱砂方括号角标 `[1]`，等宽 11px；激活论断 `claim-active` 底色 `--accent-soft` + 左侧 2px 朱砂线。
2. **图路径 chips**：节点=石青描边胶囊（`--node-ink`），边=赭色等宽小字 + `→`；同一行内节点/边交替，溢出提示「+N 条未显示」维持现状。
3. **计划树 / hop 时间轴**：连接线 2px 墨青，SSE 推进时新一段以 `ink-draw`（§8）自上而下画出；节点圆点完成后填实。
4. **仪表卡（stat-card）**：白底 + 图框刻线；32px 等宽 `tabular-nums` 数值 + 宋体 12.5px 标签；异常值仅数字变 `--bad`，卡片不整体变色。
5. **数据表（data-table）**：表头宋体 12.5px 字距 .06em；行号列等宽弱化墨；行悬停 `--surface-soft`；粘性表头，细线分隔，无斑马纹。
6. **印章状态语言（signature detail）**：终态/权限一律用「章」，真文本、可读屏：

```css
.stamp {
  display: inline-block; padding: 2px 10px; border: 2px solid currentColor;
  border-radius: 4px; font-family: var(--font-display); font-weight: 600;
  letter-spacing: .35em; text-indent: .35em; color: var(--stamp-red);
  transform: rotate(-4deg);
}
.stamp--pass { color: var(--good); }
```

　　用法：403 状态卡 →「无权」；审核决策后 →「通过」/「驳回」；turn 异常 →「异常」；中止 →「已停止」。印章只标**终态**，进行中状态不盖章。
7. **状态卡（state-card：空态/无权限/错误）**：居中小图幅 + 图框刻线 + 印章（若为终态）+ 一句话说明 + 单个后续动作按钮；永不空白。

## 8. 动效（CSS-only，克制的「落墨」节奏）

```css
--t-fast: 120ms; --t-med: 240ms; --ease-draft: cubic-bezier(.22,.8,.26,1); /* 令牌 */
```

- **加载**：侧栏淡入；对话/卡片 `translateY(8px)→0` + 淡入，240ms，逐项延迟 45ms（最多 6 项参与 stagger）。
- **SSE 流中**：思考指示为墨点呼吸（opacity .35↔1，900ms alternate）；每个 hop 到达时时间轴 `ink-draw`：

```css
@keyframes ink-draw { from { transform: scaleY(0); } }
.hop-line { transform-origin: top; animation: ink-draw var(--t-med) var(--ease-draft) both; }
```

- **微交互**：卡片悬停上浮 1px + 阴影加深（120ms）；印章悬停 `rotate(-4deg)→(-2deg)`；「停止」触发进度卡朱砂描边闪烁一次（240ms）。
- **降级**：`@media (prefers-reduced-motion: reduce)` 内全量 `animation: none; transition: none`。
- 禁用：视差、循环背景动画、超过 300ms 的入场、一切 JS 驱动动画。

## 9. 可达性与验证（落地时逐项执行；配合 p5-ui-02 §8）

对比度抽查表（估算值，落地时以工具实测为准，目标 AA ≥4.5:1）：

| 前景 / 背景 | 估算 | 用法限制 |
|---|---|---|
| `#22304c` / `#f6f2e9` | ~10.5:1 | 无限制 |
| `#57627c` / `#f6f2e9` | ~5.6:1 | 无限制 |
| `#c2401f` / `#f6f2e9` | ~4.6:1 | 正文级朱砂文字须 ≥600 字重或 ≥16px；角标为等宽加粗可用 |
| `#35694d` · `#8a5a12` · `#9e2b25` / 纸面 | ~5.5 / ~5.8 / ~6.5:1 | 无限制 |
| `#778099` / `#f6f2e9` | ~3.9:1 | **仅**辅助/占位文字，不承载必读信息 |

- 焦点环：`outline: 2px solid var(--accent); outline-offset: 2px`，全部可交互元素 `:focus-visible` 可见。
- 印章/角标均为真文本；流式进度区维持 `aria-live` 语义；命中区 ≥32px。
- CI 可断言项（并入 `test_web_console.py`，见 p5-ui-02 §8）：CSS 无 `http` 外链、`@font-face` 仅指向 `vendor/fonts/`（或零命中）、`tokens.css` 在文件清单且 ≤140 行。

## 10. 明确不做

- 暗色主题（单主题做精；试用工具不维护双主题）
- CDN 字体、图标字体、任何外链静态资源
- 图表库/动效库（引入任何运行时依赖先走 ADR）
- 大面积品牌插画、拟物纹理图片（质感一律 CSS 配方）
- 移动端适配（rules §8 V1 边界，不变）

## 11. 落地映射

| 落点 | 内容 | 任务 |
|---|---|---|
| `web/static/tokens.css`（新） | §3 字体栈 + §4 色彩/圆角/动效令牌 + §5 壳背景配方 | U-14 |
| `web/static/app.css` | 抽出 `:root` 后回落 ≤240；图签栏/导航序号样式 | U-14 |
| `web/static/chat.css` / `panels.css` | 角标/chips/时间轴按 §7 值级刷新（选择器结构不动） | U-15 |
| `web/static/console.css`（新） | 仪表卡/数据表/状态卡/印章 | U-15（随 M3/M4 视图） |
| `web/index.html` | `tokens.css` link（置于 app.css 前）；无其它资源引入 | U-14 |
| `tests/unit/test_web_*.py` | 文件清单 + §9 CI 断言项 | U-12/U-14 |
