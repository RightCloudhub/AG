# IMPORTANT — 延期工作总账

**用途：** 汇总所有**有意延期、被阻塞、未完成或明确不做**的事项，便于一眼扫完。  
**不是**路线图重写——细节仍以各阶段计划为准；本文件是债务 / 缺口总览。  
**最近汇总：** 2026-07-20  
**来源：** `plan/roadmap.md`、各阶段计划、`reports/G1_review.md`、`reports/G1_to_G2_status.json`、PRD 开放问题、风险登记册、`pyproject.toml` 覆盖率 omit、代码注释。

**符号约定**

| 符号 | 含义 |
|------|------|
| 🔴 | 阻塞下一门禁 / 关键路径 |
| 🟠 | 本阶段承诺仍未关闭 |
| 🟡 | 部分完成 / 仅脚手架 / 质量口径有保留 |
| ⚪ | 排在后续阶段（未逾期） |
| ⬛ | V1 明确不做 / 仅规模化立项 |

门禁翻转、任务关闭或评审接受新延期时，请同步更新本文件。

---

## 0. 当前态势（快照）

| 项 | 状态 |
|----|------|
| G1（POC 出口） | **Conditional-Go** 遗留项：过渡门禁已于 2026-07-20 **工程关闭** |
| G1 → G2 门禁 | **PASS**（`reports/G1_to_G2_status.json`）— 见 `reports/G1_to_G2_closeout.md` |
| P2-ENTRY-01 | **工程入场允许**（有条件：合成语料 + C2 live 配额 caveat） |
| 阶段二 MVP 代码 | 多数 **P2-ARCH / KG / RT / AG** 已落地；**评测金标规模 + 全量跑**仍开着 |
| 阶段三及以后 | 整体排在 G2 之后 |
| 本环境基础设施 | 本地 tarball Neo4j + Temurin 17（`/tmp`）；LLM 经 gemai 网关（易 403 限流） |

```bash
# 刷新门禁快照
./scripts/g1_to_g2_gate.sh              # → reports/G1_to_G2_status.json
./scripts/g1_to_g2_gate.sh --with-llm   # 尽量带上 C2 自动化部分
```

---

## 1. 🔴 阻塞 — G1 → G2 过渡（必须关闭或书面豁免）

剧本：[`plan/phases/g1-to-g2-transition.md`](../plan/phases/g1-to-g2-transition.md)  
状态产物：[`reports/G1_to_G2_status.json`](../reports/G1_to_G2_status.json)

| ID | 任务 | 延期 / 缺口 | 通过判据 | 脚本 / 产物 |
|----|------|-------------|----------|-------------|
| **C1** | **P1-GOV-01** 真实试点语料 | 工程侧脚手架已有（合成 ≥100 篇），但 **产品域未锁定**；`domain_locked: false`、`product_signoff: false`，负责人 TBD | 产品签字的领域；≥100 篇授权文档；MANIFEST 完整；Schema 可适配 | `data/pilot/MANIFEST.yaml` · `scripts/validate_pilot_corpus.sh` · `reports/pilot_corpus_validation.json` |
| **C2** | **P1-EV-04** 实时 LLM 重跑 | 尚无闭环 live 报告；LLM 抽检行仍 **pending_human** | 人工抽取抽检 ≥70%（`pending_human == 0`）；20 case live 跑通并出报告 | `scripts/llm_live_rerun.sh` · `spotcheck --mode llm` · `reports/triple_spotcheck_llm*` · `reports/llm_live_rerun.json` |
| **C3** | **P1-EV-05** Neo4j 回归 | Docker/Neo4j 不可用时常 **SKIP**，不能算通过 | `build-graph` + `run-cases --neo4j` 绿（或失败可解释） | `scripts/neo4j_regression.sh` · `reports/neo4j_regression.json` |
| 入场 | **P2-ENTRY-01** | 未书面豁免前不宜按阶段二全量计 | 门禁通过 **或** 豁免归档 + 风险更新 | 评审纪要 |

**G1 相关保留项**（见 `reports/G1_review.md`）：离线答案启发式 ≠ 生产 LLM 质量；seed 三元组抽检是 schema 合法基线，非人工 LLM 审计；interim/合成语料 ≠ 正式试点领域。

---

## 2. 🟠 阶段二 MVP — 仍未关闭

权威清单：[`plan/phases/phase-2-mvp.md`](../plan/phases/phase-2-mvp.md)

| ID | 事项 | 为何延期 / 缺口 | 如何解 |
|----|------|-----------------|--------|
| **P2-KG-04** | 图谱扩至试点全量语料 | 依赖锁定真实领域 + 授权文档（C1） | C1 后：ingest → extract → build-graph 到试点规模 |
| **P2-EV-02** | 金标 ≥200 条（含证据） | 仅有 substrate（`eval/cases.py`、`eval/gold_gen.py`）；人工/扩充集未填满 | 标注或生成+复核至 ≥200；补标注规范文档 |
| **P2-EV-04** *（部分）* | 评测全量执行 | 脚本与报告写入已完成；**全量 live agentic/baseline 重跑 deferred** — 现对比依赖已有离线 run 产物 | `agr-run-cases` / `agr-run-baseline` 后再 `agr-eval`（held-out） |
| **P2-EV-05** | 首轮全量评测 + badcase 归因 | 卡在 EV-02 规模 + 可信跑数 | 检索/分解/生成/图谱缺失四类归因 |
| **P2-EV-06** | 二轮评测 + G2 材料 | 在 EV-05 优化之后 | G2 评审包 |

### G2 门禁清单（均未关闭）

来自 [`plan/roadmap.md`](../plan/roadmap.md)：

- [ ] 全部 P0 需求在**真实试点条件**下实现并通过测试  
- [ ] 评测集 ≥200 条已标注；一键评测脚本端到端可用  
- [ ] Accuracy 相对 Baseline 趋势 **≥ +15pp**（为最终 +25pp 留空间）  
- [ ] 证据 Recall **≥ 75%**（最终目标 85%）

> 说明：离线 interim 对比（`reports/eval_comparison.md`）在 **20 条离线 case** 上已约 +15pp / 高 recall——**不能**当作 G2 held-out 证据。

### 阶段二「已完成但未完」

| 领域 | 已交付 | 仍延期 |
|------|--------|--------|
| P2-AG-03 Memory | typed state 上的 snapshot 字段 | **持久化 LangGraph checkpointer**（仅有序列化钩子；`build_graph` 未接 durable checkpointer） |
| P2-EV-01 | Case schema + 确定性金标生成器 | ≥200 人工/精选集（EV-02） |
| P2-RT-01 图 beam | 词法 relation cue + beam 上限 | **Embedding 重排**（`graph_beam.py` 标明 P3） |
| P2-KG-01 抽取管线 | journal / retry / quarantine / 溯源 | 试点语料上的 live 抽取质量；人工隔离区审核 UX |
| 覆盖率门禁 P2-ARCH-04 | fail_under 80% | 仍有模块 **omit**（见 §6） |

### 阶段二明确不做（计划原文）

- 复杂度分诊、SSE 流式、缓存/并行融合 → **阶段三**  
- 实体消歧界面、增量更新 → **阶段三**  
- 问答 Web 界面 → **阶段四**

---

## 3. 🟡 质量 / 环境替代（POC 可接受，G2/G3 须替换）

| 替代 | 用途 | 须在此前替换 |
|------|------|--------------|
| InMemoryGraphStore + seed 三元组 | 离线多跳演示 / CI | C3 Neo4j 回归 + 试点规模图 |
| `--no-llm` + 离线答案启发式 | 确定性 20 case 准确率 | C2 live Planner/Critic/Answer；生产 LLM 路径 |
| Seed 三元组「抽检」（schema 合法即 correct） | G1 抽取门禁 | 人工标注 LLM 抽取样本 ≥70% |
| 合成试点语料（`scripts/generate_pilot_corpus.py`） | 工程 C1 规模冒烟 | 产品授权真实领域 |
| Interim 6 篇 `data/raw/` | 早期 POC 叙事 | 试点重建 |
| 无 live Qdrant 的 BM25 / 进程内向量 | 本地检索测试 | 试点部署 Qdrant（+ embedding） |
| 非融合的多路候选拼接 | Executor 工具选择 | P3 RRF + re-ranker（`fusion.py` 未建） |

---

## 4. ⚪ 阶段三 — 工程优化（整阶段延期）

权威：[`plan/phases/phase-3-optimization.md`](../plan/phases/phase-3-optimization.md)  
**前提：** G2 通过。

| 轨道 | ID | 主题 |
|------|-----|------|
| PERF | P3-PERF-01 … 07 | 分诊 / Fast Path；检索并行；RRF + re-ranker 钩子；缓存；模型分级；SSE；压测 P95 |
| OP | P3-OP-01 … 04 | 查询级指标；租户/用户/单查询预算硬上限；全链路 trace；护栏专项集 |
| KG | P3-KG-01 … 04 | 增量更新 + 冲突；实体消歧；审核 UI；知识管理 API |
| AN | P3-AN-01 … 02 | 推理链落库 + 审计 API；可视化组件 |
| EV | P3-EV-01 … 03 | 最终 AC-1/2（+25pp、Recall ≥85%）；分诊 A/B；G3 材料 |

**仓库骨架中尚未实现的占位**（见 `plan/engineering/repo-structure.md`）：

- `agent/triage.py`、`retrieval/fusion.py`、`agent/tools/`、`knowledge/resolution.py`、`knowledge/incremental.py`、`knowledge/review/`、`api/auth.py`、`api/sse.py`、`observability/`、`web/`

---

## 5. ⚪ 阶段四 / 五 — 试点与规模化（延期）

### 阶段四试点（[`phase-4-pilot.md`](../plan/phases/phase-4-pilot.md)）

| ID | 主题 |
|----|------|
| P4-UI-01/02 | 问答 Web 界面；API 鉴权 + 限流 |
| P4-REL-01…04 | 生产部署、安全检查、告警、运维手册 |
| P4-OPS-01…04 | 灰度放量、反馈→badcase、人工复核回路、周会机制 |
| P4-AC-01…03 | AC-1…7 全量证据；生产审计抽样；G4 评审 |

### 阶段五规模化（[`phase-5-scale.md`](../plan/phases/phase-5-scale.md)）— 仅方向，试点后另行立项

| ID | 主题 |
|----|------|
| P5-EXT-01…03 | 多领域图谱；跨域实体对齐；多租户隔离 |
| P5-CAP-01…04 | 图谱浏览 UI；外部工具（SQL/API）；答案置信度分级；学习型 re-ranker |
| P5-GOV-01…04 | 图谱月度体检；评测集持续扩充；策略变更治理；成本持续优化 |

---

## 6. 工程债务与有意覆盖率缺口

### 覆盖率 omit（`pyproject.toml` — 扩测后再移除）

| 省略路径 | 记录原因 | 延期动作 |
|----------|----------|----------|
| `cli/*`、`__main__.py` | 入口胶水 | 补薄测或保留 omit 并写清理由 |
| `stores/neo4j_store.py` | 在线适配器 | Docker 可用时做集成测（挂钩 C3） |
| `llm/provider.py` | 在线 HTTP 客户端 | 契约/mock 测；C2 下 live 冒烟 |
| `generation/answer.py` | LLM 生成路径 | live 生成测试 |
| `generation/offline_answer.py` | 大启发式；靠 agent 离线 E2E 覆盖 | 拆分后优先单测 heuristics 模块 |

另延期：**分支覆盖**（`branch = false`）。

### 产品面部分完成

| 面 | 当前状态 | 延期 |
|----|----------|------|
| `POST /v1/query` | 已有（P2-ARCH-03） | 鉴权、限流、SSE、多租户（P3/P4） |
| 推理链 | Schema + 响应内 chain | 落库 + 按 query id 审计（P3-AN-01） |
| BudgetTracker | 单次运行记账 / 熔断 | 租户与用户级硬上限 + 错误码（P3-OP-02） |
| 图关系打分 | 仅词法 cue | Embedding 重排（P3） |
| 接入格式 | 偏 MD/TXT | 若试点需要，PDF 文本作为一等路径（PRD 列了 PDF 文本） |
| 评测集布局 | `evals/datasets/poc_cases.jsonl` | `dev` / `heldout` / `guardrail` 分集（R7） |
| LLM 判卷 | 不存在 | 结构计划中的 `evals/judge.py` |

### 规范 / 结构备注

- 超大文件合规拆分（2026-07-20）**已完成**；新模块保持约 200–400 行（`plan/engineering/repo-structure.md`）。  
- 进一步包布局（如 `knowledge/extraction/` 包、`ingest/` 包等）为可选对齐完整目录树。

---

## 7. PRD 开放问题（产品层仍未拍板）

来自 [`PRD.md`](../PRD.md) §9：

| # | 问题 | 代码当前默认 | 需要 |
|---|------|--------------|------|
| 1 | 图库：Neo4j vs NebulaGraph | tech-stack ADR 选 Neo4j；适配器已有 | 确认试点规模假设 |
| 2 | 延迟 P95 ≤8s Agentic / ≤3s Fast Path | 未压测 | 业务确认 + P3-PERF-07 |
| 3 | ≥200 条金标谁来标 | 仅 substrate + 合成生成 | 人力/流程（R6） |
| 4 | 首个真实试点领域 | 合成公司关系脚手架 | 产品锁定（R5、C1） |
| 5 | 各角色是否分级用模 | 有 Tier 枚举；生产策略未调优 | P3-PERF-05 + 预算 |

PRD 仍为**初稿待评审**；AC 数值指标需结合试点业务最终确认。

---

## 8. 活跃风险中编码的延期缓解

权威：[`plan/governance/risk-register.md`](../plan/governance/risk-register.md)

| ID | 风险 | 仍未落地的缓解 |
|----|------|----------------|
| R5 | 试点领域/语料不定 | 产品签字关闭 C1 |
| R1 | 图谱构建成本/周期 | C1 后试点规模抽取；质量治理在 P3 |
| R2 | Agent 多轮成本失控 | 完整 FR-OP-02 三级；分诊 FR-AG-01（P3） |
| R3 | 效果提升 &lt;25pp | EV-02 后可信 held-out；EV-05/06 badcase 闭环 |
| R6 | 标注资源不足 | EV-02 排人 / 合成+复核剧本 |
| R7 | 评测过拟合 | held-out 分集 + 线上反馈（P4） |
| R8 | 路径爆炸 | beam 上限已上；大子图压测仍在 P3 |
| R9 | 真实场景编造 | 引用绑定已有基础；人工复核回路 P4-OPS-03；真实流量 AC-7 |
| R10 | 增量更新质量回退 | 整条增量路径在 P3 |
| R11 | LangGraph 依赖风险 | 版本钉死 + 回归；自研退路未实战验证 |

---

## 9. ⬛ 明确不做（V1 / PRD）

未改 PRD 前，**不要**当阶段二/三「漏项」来排：

- 开放域全网问答（仅已接入语料）  
- 多模态（图片 / 表格图像）  
- 面向终端消费者的开放注册产品（V1 面向企业内部与集成方）  
- 完整 NebulaGraph 多集群故事（规模化立项前）  
- 阶段五 P2 能力（图谱浏览 FR-KG-07、外部工具 FR-AG-08、置信度分级 FR-AN-05、学习型 re-ranker）— 试点后再立项  

---

## 10. 验收项（AC）— 证据仍延期

| AC | 目标（PRD） | 当前证据 | 延期证明 |
|----|-------------|----------|----------|
| AC-1 Accuracy | 相对向量 Baseline +≥25pp | 离线 interim 约 +15pp / 20 case | ≥200 held-out + live agentic/baseline |
| AC-2 证据 Recall | ≥85%（跳数 ≥2） | 离线 interim 约 0.96 / 20 | 同上 + 金标证据标注 |
| AC-3 审计 | 可按查询 ID 回查链 | 响应内 / run JSONL 中有 chain | 落库 + API（P3-AN-01）；生产抽样（P4） |
| AC-4 延迟 | Agentic P95 ≤8s；Fast Path ≤3s | 仅离线毫秒级 | 压测 P3-PERF-07 |
| AC-5 增量 | 更新不中断查询 | 未实现 | P3-KG-01 演练 |
| AC-6 护栏/预算 | 硬上限 + 专项集 | 离线可配跳数/token 护栏 | P3-OP-02/04；生产告警 P4 |
| AC-7 无引用不编造 | 编造率 0 | 离线引用绑定 + 报告字段 | live LLM + 人工抽样；灰度流量 |

---

## 11. 优先下一步（建议顺序）

1. **关闭或豁免 C1–C3** — 产品域签字、live LLM 审计、Neo4j 回归。  
2. **P2-EV-02** — 金标扩到 ≥200 并带证据。  
3. **P2-KG-04 + 全量抽取** — C1 后在试点语料上跑通。  
4. **P2-EV-04/05/06** — 全量 agentic vs baseline、badcase 归因、G2 材料。  
5. 然后才进入 **阶段三** PERF/OP/KG（分诊、融合、缓存、SSE、增量、可观测性）。  
6. **阶段四** UI、鉴权、灰度、AC 全套在 G3 之后。  

---

## 12. 索引

| 主题 | 权威文档 |
|------|----------|
| 路线图与门禁 | [`plan/roadmap.md`](../plan/roadmap.md) |
| G1 评审 | [`reports/G1_review.md`](../reports/G1_review.md) |
| G1→G2 剧本 | [`plan/phases/g1-to-g2-transition.md`](../plan/phases/g1-to-g2-transition.md) |
| 阶段一…五任务 | [`plan/phases/`](../plan/phases/) |
| 风险 | [`plan/governance/risk-register.md`](../plan/governance/risk-register.md) |
| PRD / AC / 开放问题 | [`PRD.md`](../PRD.md) |
| 目标目录树 | [`plan/engineering/repo-structure.md`](../plan/engineering/repo-structure.md) |
| 门禁 JSON | [`reports/G1_to_G2_status.json`](../reports/G1_to_G2_status.json) |
| 外部运行时（JDK/Neo4j/镜像，非 pip/npm） | [`docs/EXTERNAL_RUNTIMES.md`](./EXTERNAL_RUNTIMES.md) |

---

*关闭某项时：在此勾记，并同步阶段清单；门禁 JSON / 风险状态尽量同一变更集更新。*
