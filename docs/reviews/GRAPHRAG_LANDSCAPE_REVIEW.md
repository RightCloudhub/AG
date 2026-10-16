# GraphRAG 主流项目对比与改进建议

**评估日期：** 2026-09-23  
**对象：** AgenticGraphRAG 当前仓库实现  
**用途：** 用主流开源项目的公开能力校准本项目的产品与工程优先级；不是同数据、同模型的性能排名。

## 结论摘要

AgenticGraphRAG 已具备多跳 Agent、图/向量/BM25 混合召回、增量冲突处理、人工复核、租户隔离和可观测治理。与主流项目相比，优先差距不在“再加一个 Agent 框架”，而在**真实场景效果与延迟证据、可用的 reranker、结构化文档接入、全局型图检索，以及证据蕴含校验**。

建议保持现有的离线确定性与模块协议，不整体迁移到某个竞品；按下表优先级补强，并在真实语料上证明每项改动确实带来质量或成本收益。

## 本项目基线

仓库架构文档标注于 2026-09-23：默认离线运行；Agentic 路径用 LangGraph DAG 调度子问题；向量、图 beam、BM25 并行召回并以 RRF 融合；知识抽取已有 schema/confidence gate、实体解析、增量冲突处理与人工复核；API 上传则是另一条仅做文档、向量和全文索引的任务路径。见 [`ARCHITECTURE.md`](../ARCHITECTURE.md) 与 [`README.md`](../../README.md)。

| 领域 | 仓库现状 | 评估注意点 |
|---|---|---|
| 检索与推理 | 三路召回、RRF、Agent 计划 DAG、Fast Path/Agentic 分流、引用绑定与推理链审计 | `Reranker` 目前是可注入协议，默认 `IdentityReranker`，不会实际重排；见 [`retrieval/fusion.py`](../../src/agentic_graphrag/retrieval/fusion.py)。 |
| 索引与上传 | 图谱构建有抽取、校验、冲突处理；HTTP 上传只接受 UTF-8 Markdown/纯文本，交给单独 worker 建文档/向量/BM25 索引 | 上传路径不是完整图谱抽取路径；PDF 不支持，worker 不由 API 自动启动；见 [`ARCHITECTURE.md`](../ARCHITECTURE.md)。 |
| 分块 | 默认固定字符窗口（1200 字符、150 重叠） | 已有设计审计指出尚未保留标题、列表、表格等结构；见 [`DESIGN_VS_IMPLEMENTATION.md`](../design/DESIGN_VS_IMPLEMENTATION.md) 与 [`default.yaml`](../../configs/default.yaml)。 |
| 质量与性能 | 有 dev/heldout/guardrail 数据集和离线评测脚手架 | 仓库的待办总账最新汇总日期为 2026-08-08；其中真实域签字、live fair baseline、生产 P95 等仍列为开放项。旧的约 92 秒 P95 是历史记录，不应当作 2026-09-23 的当前实测值；应先复测。见 [`IMPORTANT.md`](./IMPORTANT.md)。 |
| 证据校验 | claim 与检索证据绑定，执行规则性引用门禁 | 当前规则门禁不等于自然语言蕴含判定；待办总账也记录了真 NLI 支撑判定缺口。见 [`ARCHITECTURE.md`](../ARCHITECTURE.md) 与 [`IMPORTANT.md`](./IMPORTANT.md)。 |
| 企业治理 | RBAC、租户过滤、预算、审计、Prometheus/可选 OTel 已有代码 | 多副本共享队列/状态及生产后端联调仍属于部署验证或规模化工作；不要把应用层租户过滤等同于物理隔离。见 [`ENTERPRISE_READINESS.md`](./ENTERPRISE_READINESS.md)。 |

## 主流项目对照

以下为截至评估日查阅的项目官方文档或仓库所描述的能力。它们的定位不同，功能存在不代表在本项目数据上必然更准确或更便宜。

| 项目 | 官方资料中值得关注的能力 | 对本项目的启示 |
|---|---|---|
| Microsoft GraphRAG（`v3.1.2`，2026-08-21 release） | 图谱抽取后生成社区与社区报告；公开 Basic、Local、Global、DRIFT 等搜索模式，用于实体/局部问题与全局主题问题等不同问题形态。 | 补充“跨全库主题/概览”检索模式；避免把每个问题都拆成局部多跳子问题。其社区摘要索引有额外构建成本，应先用真实问法验证收益。 [发布记录](https://github.com/microsoft/graphrag/releases/tag/v3.1.2) · [索引概览](https://microsoft.github.io/graphrag/index/overview/) · [查询概览](https://microsoft.github.io/graphrag/query/overview/) |
| LightRAG（`v1.5.7`，2026-09-02 release） | 图与向量结合，提供 `local`、`global`、`hybrid`、`naive`、`mix` 等查询模式；该版还增加 graph-first ingestion（延后向量索引）和显式自定义 chunking 选择，并提供 SDK、API、Web UI 与多种存储后端。 | 借鉴“按问题类型切换检索策略”和图谱/文本证据共同组织，也可评估可配置的分阶段索引；不照搬其实现或把论文/仓库报告当作本项目基准。 [发布记录](https://github.com/HKUDS/LightRAG/releases/tag/v1.5.7) · [官方仓库](https://github.com/HKUDS/LightRAG) · [原始论文](https://arxiv.org/abs/2410.05779) |
| Neo4j GraphRAG for Python（`1.19.0`，2026-08-26 release） | 提供知识图谱构建器和多种检索器，包括 Vector、Hybrid、Vector+Cypher、Text2Cypher 等，可在 Neo4j 生态中组合使用；官方将 KG Builder 标为 experimental。 | 借鉴清晰、可替换的检索器边界；本项目已有 `GraphStore` 等协议，不需要仅为接入检索模式而整体替换应用编排。生成式图查询需额外验证权限与查询约束。 [发布记录](https://github.com/neo4j/neo4j-graphrag-python/releases/tag/1.19.0) · [RAG 指南](https://neo4j.com/docs/neo4j-graphrag-python/current/user_guide_rag.html) · [KG Builder](https://neo4j.com/docs/neo4j-graphrag-python/current/user_guide_kg_builder.html) |
| LlamaIndex Property Graph（`llama-index-core 0.14.25`，2026-09-21 release） | 把图谱抽取器、图存储和检索器拆成可组合部件；Property Graph 检索可使用 sub-retriever 组合图、向量和 Cypher 等能力。 | 借鉴插件化抽取/检索与小范围实验；重点是把 reranker、社区摘要、图查询等做成可测的可选策略，而非引入整套框架依赖。 [发布记录](https://github.com/run-llama/llama_index/releases/tag/v0.14.25) · [属性图指南](https://developers.llamaindex.ai/python/framework/module_guides/indexing/lpg_index_guide/) |
| RAGFlow（端到端 RAG 平台） | 官方资料强调多格式文档解析、可配置分块、知识库管理与检索测试，并包含图谱相关能力。 | 最明显的对照是文档接入和索引运维体验：本项目上传目前仅适合文本试用。优先补 parser/metadata/重建流程，不必追求复制完整平台。 [官方文档](https://ragflow.io/docs/) · [官方仓库](https://github.com/infiniflow/ragflow) |

## 改进建议

### P0：用真实评测和性能数据闭环

1. **建立代表性真实语料门禁。** 锁定真实业务域的 dev/heldout/guardrail 分集，由领域人员签署 gold；覆盖单跳、多跳、跨文档、无答案、冲突/时效和低证据问题。保留合成集做回归，但不能代替正式效果门禁。
2. **拆开评测检索、答案与证据。** 至少报告 Recall@K、MRR/nDCG、答案准确率、引用 precision/recall 或人工 entailment 评分、拒答正确率，并按 hop 数/问题类别切片。
3. **加入组件消融和运行成本。** 比较 vector-only、BM25-only、graph-only、融合、融合+reranker，以及 Fast Path/Agentic；一起报告 P50/P95、LLM 调用数、token、单问成本、错误率。仓库已有 P95 目标（Agentic ≤8s、Fast Path ≤3s），应以 staging/live 实测作为发布门禁，而不是离线毫秒结果。

**完成标准建议：** 人工签署的真实域 heldout 固定版本；每次关键检索/提示词改动产出可复现报告；达到已定义的准确率、证据质量、延迟与成本门槛。若门槛未达，记录未通过而不是用合成集覆盖。

### P0：补上真实重排并降低长尾

1. 将当前 identity 默认替换为可选的真实 reranker 实现（例如本地 cross-encoder 或受控远端服务），保留协议和离线 identity fallback；增加超时、候选数限制、模型版本和失败降级策略。
2. 用前述 heldout 集测 reranker 的增益及额外延迟/成本；对没有显著提升的 query class 不启用重排。
3. 给每个阶段记录耗时和调用量：规划、每路召回、融合/重排、critic、答案生成。用慢查询 trace 调整 Agent 升级、图展开宽度、Top-K、缓存与预算，而不是只收紧全局 hop 上限。

**完成标准建议：** 对照 identity baseline，报告分 query class 的排序质量变化和 p95/成本；开启 reranker 不得突破预算，故障时仍能回退到确定性排序。

### P1：补充全局图问答与按问题路由

当前强项是局部实体锚定和多跳路径；建议增加可选的“全局问题”路线：离线/异步生成社区或子图摘要，问题分流到 Local（实体/关系）、Global（主题/分布/概览）、Agentic multi-hop（显式跨实体推理）或文本检索。可先基于现有图存储实现小规模摘要原型，并把摘要版本与源文档版本绑定。

社区摘要可能增加建索引时间、LLM 花费和过期风险。先在真实用户问题中识别“跨全库总结”占比，通过覆盖率、答案证据质量、构建成本和更新时效决定是否投入；不要默认对所有语料运行高成本的 GraphRAG 式索引。

### P1：把文档结构、来源与索引生命周期做完整

1. 将 API 上传和 CLI 图谱构建收敛到共享的版本化 ingest pipeline，明确文档状态、解析错误、重试和索引完成状态；现有 API 文本索引与图谱抽取流程的边界需要对用户可见。
2. 按业务需求增加格式 parser（先 PDF，再决定 DOCX/HTML 等），保留页码、标题层级、表格/列表、段落和原始 span；分块采用结构优先、长度兜底，并保持离线可测。
3. 给文档、chunk、实体/关系保留稳定 provenance 与版本；支持文档更新/删除后的向量、全文和图事实撤回或重建，避免过期事实继续参与回答。

**完成标准建议：** 任一答案证据能回到文档版本及精确页/段；更新或删除后旧证据不可检索；解析/索引失败可重试且可审计。新增格式按目标用户的真实语料优先级验收，不以格式数量作目标。

### P1：提高 claim 级证据校验强度

在现有规则性引用门禁上增加可选的 claim-to-evidence 蕴含检查，并记录不支持、部分支持、冲突和来源过期等状态；对离线路径保留确定性规则。可先用小型人工标注集评估“词面命中但事实不蕴含”的误放行率，再决定在线 NLI/LLM 校验的覆盖范围和预算。

引用应优先指向具体 source span，而不仅是 chunk/content；答案中每个事实 claim 都要能回溯来源。不要把“有引用”作为“被引用内容支持答案”的同义词。

### P2：按生产规模补齐共享状态与部署验证

当需要多副本或高并发时，将当前面向单机的任务/worker 状态与进程内限流、缓存逐步换成具备共享持久化、租约/幂等和恢复语义的实现；在 Neo4j、Qdrant、OTel collector 等真实后端上跑租户隔离、故障恢复和负载验证。建议继续通过现有 store/scheduling 协议接入，保持 offline 默认与内存测试替身。

这项应由目标部署拓扑和并发量触发，不必为尚未验证的规模提前引入复杂基础设施。

## 建议执行顺序

| 顺序 | 交付 | 为什么先做 |
|---|---|---|
| 1 | 真实域评测集签字、当前 live P95/成本复测、关键消融 | 先确认问题和瓶颈，避免拿合成集优化错方向。 |
| 2 | reranker 原型 + 端到端 trace 分阶段耗时 | 低侵入验证检索排序和长尾是否可改善。 |
| 3 | 结构化 chunk/provenance 与 API/CLI ingest 共用核心 | 让生产文档真正进入可审计的知识管线。 |
| 4 | global/community-summary 路线小范围实验 | 只在真实问法证明局部多跳不足时扩展。 |
| 5 | claim 蕴含评估与验证器、生产共享队列和多副本演练 | 提升可信度并按部署需求逐步产品化。 |

## 资料与边界

- 本文对竞品能力的描述来自表中项目官方文档、代码仓库或论文；查阅日期为 **2026-09-23**。滚动文档可能随上游更新，正式立项前应再核对版本与许可证。
- 竞品表对照的是公开能力和架构方向，没有以相同语料、模型、成本预算运行统一基准；不据此宣称某项目整体优于本项目。
- 本项目现状以仓库文档和代码接口为依据。尤其是 [`IMPORTANT.md`](./IMPORTANT.md) 的状态汇总日期是 2026-08-08；本文明确将其中延迟与验收记录视作历史待复测证据，而不是当前 live 结果。
