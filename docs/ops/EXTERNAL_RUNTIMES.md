# 外部运行时与非 Python / 非 npm 依赖清单

**用途：** 记录本项目除 **Python（uv/pip）**、**Node/npm/pnpm** 以外的运行时、二进制与镜像，避免「装过但无文档、路径散落 /tmp」无法复现。  
**最近更新：** 2026-07-21（P5-UI-01 / ADR-006：Vue 3 运行时 vendor 说明）

> Python 依赖见 `pyproject.toml` / `uv.lock`。  
> 容器编排声明见 `docker-compose.yml`（**首选**路径；无需手动解压 JDK/Neo4j）。

---

## 1. 本机新下载（用户空间，不进 git）

以下为关闭 **C3 Neo4j 回归** 时在 **无 Docker / 无系统包安装权限** 环境下的临时下载。  
路径均在 **`/tmp`**，重启或清理 `/tmp` 后会丢失，**不是**仓库正式依赖。

| 名称 | 版本 | 形态 | 本机路径 | 约体积 | 来源 | 用途 |
|------|------|------|----------|--------|------|------|
| **Eclipse Temurin JDK** | **17.0.19+10**（OpenJDK 17） | tarball 解压 | 解压：`/tmp/jdk-17/` · 包：`/tmp/jdk17.tar.gz` | ~318M 解压 / ~185M 包 | [Adoptium API](https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse?project=jdk)（`linux/x64` 或 `aarch64`） | 运行 Neo4j 社区版（`JAVA_HOME`） |
| **Neo4j Community** | **5.26.0** | unix tarball 解压 | 解压：`/tmp/neo4j-home/` · 包：`/tmp/neo4j.tgz` | ~680M 解压 / ~152M 包 | `https://dist.neo4j.org/neo4j-community-5.26.0-unix.tar.gz` | C3：`build-graph` + `run-cases --neo4j`（bolt `7687` / http `7474`） |
| **`which` shim**（若系统无 `which`） | — | 一行 shell | `/tmp/binshim/which` | 可忽略 | 本地临时创建 | Neo4j 启动脚本依赖 `which` 时补齐 |

### 环境变量（本地 tarball 方式）

```bash
export JAVA_HOME=/tmp/jdk-17
export PATH="$JAVA_HOME/bin:${PATH}"
export NEO4J_HOME=/tmp/neo4j-home
# 若缺 which：
# export PATH="/tmp/binshim:$PATH"

# 初始密码（与 .env.example / docker-compose 一致）
# $NEO4J_HOME/bin/neo4j-admin dbms set-initial-password agentic-graphrag
# $NEO4J_HOME/bin/neo4j start
```

应用侧默认连接（见 `.env.example`）：

| 变量 | 默认值 |
|------|--------|
| `NEO4J_URI` | `bolt://localhost:7687` |
| `NEO4J_USER` | `neo4j` |
| `NEO4J_PASSWORD` | `agentic-graphrag` |

### 清理（可选）

```bash
# 停库后删除临时下载
"$NEO4J_HOME/bin/neo4j" stop 2>/dev/null || true
rm -rf /tmp/jdk-17 /tmp/jdk17.tar.gz /tmp/neo4j-home /tmp/neo4j.tgz /tmp/binshim
```

---

## 2. Docker 镜像（项目声明，首选）

**声明文件：** `docker-compose.yml`  
**启动：** `docker compose up -d`  
镜像由 Docker 拉取，**不**提交到本仓库。

| 服务 | 镜像 | 端口 | 用途 |
|------|------|------|------|
| `neo4j` | `neo4j:5.26-community` | `7474`（HTTP）、`7687`（Bolt） | 图存储 / C3 回归 / 生产候选 GraphStore |
| `qdrant` | `qdrant/qdrant:v1.12.5` | `6333`、`6334` | 向量库（FR-RT-01）；离线 POC 可用内存向量 |
| `reranker`（`rerank` profile） | `ghcr.io/huggingface/text-embeddings-inference:cpu-1.8` | `127.0.0.1:8081` | 可选 TEI cross-encoder，模型 `BAAI/bge-reranker-v2-m3` |

卷名：`neo4j_data`、`qdrant_data`（Docker volume，非 git）。

### 2.1 可选 TEI 重排服务

```bash
docker compose --profile rerank up -d reranker
curl --fail http://127.0.0.1:8081/health
curl --fail http://127.0.0.1:8081/rerank \
  -H 'Content-Type: application/json' \
  -d '{"query":"Who leads Acme?","texts":["Alice is CEO of Acme.","The weather is mild."],"truncate":true}'
```

首次启动需要网络下载模型；权重保存在 `reranker_data` 卷中。CPU 镜像不需要 GPU，
但不能据此推断生产延迟；本次环境没有 Docker/TEI，容器启动及真实模型性能尚未验证。
应用在宿主机上使用 `http://127.0.0.1:8081/rerank`；若应用也在同一 Compose 网络，
改为 `http://reranker:80/rerank`。普通 `docker compose up -d` 不启动该服务。

`configs/default.yaml` 的 `retrieval.rerank.kind` **仍为 `none`**，live/hybrid 不自动开启。
显式试验可使用 `AGR_RERANK=http`，沿用已配置的 live/hybrid 和真实 LLM 设置；
`--no-llm` / `allow_llm=False` 总是关闭重排（包括 lexical/local），不会加载模型或请求 TEI。
重排配置如下：

| 配置 | 默认值 | 行为 |
|---|---|---|
| `candidate_pool` | 30 | vector、BM25、每个 graph 工具的召回上限；图搜索内部 beam/path 上限相应扩大 |
| `top_n` | null | RRF → 重排后保留 `retrieval.top_k`（默认 10）；旧配置可用 `top_n` 覆盖 |
| `model` | `BAAI/bge-reranker-v2-m3` | HTTP 服务的模型标识，作为缓存命名空间；应与实际模型及 revision 一致 |
| `timeout_seconds` | 2.0 | HTTP 每次请求的超时；仅 HTTP 5xx 重试一次，超时/4xx 不重试 |
| `batch_size` | 16 | 每次最多 16 对；需与 TEI 的 `--max-client-batch-size` 协调 |
| `max_chars` / `max_query_chars` | 2000 / 1000 | 只截断模型输入，原始证据和引用完整保留；TEI 另带 `truncate=true` |
| `graph_policy` | exempt | 图候选保留原 RRF 位置，仅重排其它候选；不额外把图候选提到 top_k |
| `graph_policy=score` / `floor` | — | 正常打分 / 将图分数下限设为 `graph_floor`（默认 0.0，按模型输出标度调整） |
| `failure_threshold` / `cooldown_seconds` | 2 / 20 | 连续两次重排失败打开熔断，20 秒后允许探测；上游健康状态跨请求共享 |

图邻居与路径均按**真实边方向**渲染成 `Alice ceo of Acme.` 这样的句子，路径按边顺序连接。
重排异常、缺失/重复/非法分数均使整次融合保留 RRF 顺序；成功的部分批次不会改变该次排序。
`metadata.rerank_fallback` 标记候选，最终 chain 带 `metadata.rerank_degraded` 和 `metadata.rerank`
（耗时、请求数、评分对数、缓存命中、降级次数），并记录不含问题/响应正文的 WARNING。
当前分支和 `origin/main` 未含 WT2 helper，按任务约定使用 WARNING。
降级答案不写入答案缓存；重排启用时跳过最终检索结果缓存，防止其隐藏恢复或配置变化。

分数缓存仅在一次 query 的 executor 中共享，键为 `(query, chunk_id, model)`；
图候选使用独立 candidate id，且校验文本变化，防止图路径/来源 chunk 混用。
register seed、后续搜索及并行分支共用分数；新 query 清空分数，跨租户请求不共享。
总时延受批次数影响，2 秒是每次请求的边界，不是整个多轮 query 的边界；仍受 query 总超时约束。

无 TEI 的环境可以显式安装本地适配器：

```bash
./scripts/setup.sh --extras dev,webapp,rerank-local --locked
# 设置 retrieval.rerank.model 为模型名或已经下载的模型目录，再使用 AGR_RERANK=local
```

`sentence-transformers`/PyTorch 是可选依赖，默认安装和离线 CI 不导入它们。
本地适配器懒加载并共享权重，复用批处理、字符截断、图策略、缓存和降级逻辑，
`max_length=512` 约束 tokenizer 的 query/document 对长度；模型加载/推理失败可降级。
进程内推理不能由 HTTP 超时中断，冷启动下载和推理耗时需要单独评估。

评测入口为 `PYTHONPATH=src .venv/bin/python scripts/rerank_eval.py`，显式 `--endpoint` 才测 HTTP。
它复用现有 case runner、gold scorer 和 McNemar 比较，以相同语料扫描 10/20/30 池。
该脚本的 mock embedding/answer 实验不等于 live 验收；当前结果和开启条件见
[`reports/rerank_eval.md`](../../reports/rerank_eval.md)。生产开启前应固定模型 revision 和相同 LLM 缓存，
在真实 live 栈重跑，要求准确率提升（McNemar p < 0.05）且 P95 增量满足事先约定的预算。

> 与 §1 的关系：Docker 可用时 **不要** 依赖 `/tmp` 的 JDK/Neo4j；C3 脚本 `scripts/neo4j_regression.sh` 优先探测本机 Bolt，并在 Docker 可用时尝试 `docker compose up -d neo4j`。

---

## 3. 系统级 / 未纳入仓库的其它非 Python 依赖

| 项 | 是否本项目下载 | 说明 |
|----|----------------|------|
| **Docker / Docker Compose** | 否（环境自备） | 首选拉起 Neo4j/Qdrant；当前部分环境无 Docker、无 root 安装权限 |
| **Java 17+** | 临时：§1 Temurin | 仅 tarball Neo4j 需要；用 Docker 镜像时 **不必** 本机装 JDK |
| **curl** | 否（系统已有） | 下载 tarball / 健康检查 |
| **Node / npm / pnpm** | **无** | **不**引入前端包管理 / 构建链（零构建实质；ADR-006） |
| **Vue 3 运行时** | 浏览器加载 / 可选 vendor | 钉版 **3.5.13** `vue.esm-browser.prod.js`；**非 npm**（见下方 §3.1） |
| **系统包管理器装的 jdk-openjdk / neo4j** | 未使用 | Arch 上曾无 passwordless sudo，未走 `pacman` 安装 |

### 3.1 Vue 3 运行时 vendor（Web 工作台，ADR-006）

| 项 | 值 |
|----|-----|
| 版本 | **3.5.13**（升级须同步 ADR-006、vendor 文件与本文件） |
| 产物 | `vue.esm-browser.prod.js`（全量构建，含浏览器内模板编译） |
| 本地路径 | `web/static/vendor/vue.esm-browser.prod.js`（已入库，完全离线） |
| 加载方式 | **静态 ESM import**：各模块 `import … from '../vendor/vue.esm-browser.prod.js'`（入口 `web/static/js/app.js`、`web/static/js/product/main.js`）。无 CDN 兜底、无运行时加载器、无 `VUE_VERSION` 常量 |
| 说明文档 | [`web/static/vendor/README.md`](../../web/static/vendor/README.md) |
| 许可 | MIT · 约 170 KB min |

```bash
# 仅在需要重新 vendor / 升级版本时执行（日常运行只读已入库的 vendor 文件）
mkdir -p web/static/vendor
curl -fsSL -o web/static/vendor/vue.esm-browser.prod.js \
  https://unpkg.com/vue@3.5.13/dist/vue.esm-browser.prod.js
```

---

### 3.2 Lucide 图标几何（Web 工作台，vendored 子集）

`Prompt.md` §3.3 规定界面图标用 Lucide 线性图标（16px、1.5 描边、`currentColor`）。`web/` 零构建，不引入 npm 依赖，也不在运行时取外链：用到的 33 个图标的几何数据从 `lucide-static` 的 `icon-nodes.json` 生成为 `web/static/vendor/lucide-icons.js`（`ICON_IDS` 名称表 + `LUCIDE` 节点表），与 §3.1 的 Vue 运行时同样 vendored 后入库。

| 项 | 值 |
|------|------|
| 上游 | `lucide-static`（与参考 demo 的 `lucide-react` 同为 **1.65.0**） |
| 许可证 | **ISC** |
| 产物 | `web/static/vendor/lucide-icons.js`（生成文件，勿手改） |
| 生成器 | `scripts/build_lucide_icons.mjs`（名称表为唯一来源，CI 可 `--check`） |
| 说明文档 | [`web/static/vendor/README.md`](../../web/static/vendor/README.md) |

```bash
npm pack lucide-static@1.65.0 && tar xzf lucide-static-1.65.0.tgz                       # 取上游 icon-nodes.json
node scripts/build_lucide_icons.mjs --source package/icon-nodes.json --version 1.65.0   # 重新生成
node scripts/build_lucide_icons.mjs --check --source package/icon-nodes.json            # 校验是否漂移
```

品牌标记 `mark` 是界面唯一自绘字形，位于 `web/static/js/icons.js`，不在此清单内。

### 3.3 Vue Router 4.5.1（MIT）

ADR-007 的独立 Web 应用使用本地 `web/static/vendor/vue-router.esm-browser.prod.js`。
上游为官方 npm 包 `vue-router@4.5.1` 的 ESM browser production 构建；唯一改动是
将 Vue 的 bare import 改为同目录钉版 Vue 3.5.13。许可证同目录保存，来源与包装方式见
[`web/static/vendor/README.md`](../../web/static/vendor/README.md)。无需 npm 构建或运行时 CDN。

## 4. 与 Python 依赖的边界

| 类别 | 管理方式 | 勿写入本清单 |
|------|----------|--------------|
| 应用代码库 | `pyproject.toml` + `uv.lock` | `neo4j` Python 驱动、`qdrant-client`、`fastapi` 等 |
| 图/向量**服务进程** | Docker 或 §1 二进制 | 不是 pip 包 |
| LLM HTTP API | `.env`（`LLM_*`） | 云服务，非本机包 |

---

## 5. 建议约定（后续）

1. **正式开发机**：只用 `docker compose`，不要把 JDK/Neo4j 解压进仓库。  
2. 若必须 vendoring 本地二进制，放到 **`.tools/`**（已建议 gitignore，见下），并在本文件追加版本与校验和。  
3. 门禁 C3 通过判据仍是：**Bolt 上真实 Neo4j**（`backend=neo4j`），内存图不算 C3 通过。  
4. 变更镜像 tag（如升级 Neo4j 5.26 → 更新）时：改 `docker-compose.yml` + 本文件 + [CHANGELOG.md](../../CHANGELOG.md)。

### `.gitignore` 建议条目（若使用项目内 `.tools/`）

```gitignore
# Local non-Python runtimes (JDK, Neo4j tarball extracts, etc.)
.tools/
```

（仓库若尚未包含该行，添加时与本文同步。）

---

## 6. 快速核对命令

```bash
# Docker 路径
docker compose ps
docker images | grep -E 'neo4j|qdrant'

# 临时 tarball 路径
test -x /tmp/jdk-17/bin/java && /tmp/jdk-17/bin/java -version
test -d /tmp/neo4j-home && cat /tmp/neo4j-home/README.txt | head -5
du -sh /tmp/jdk-17 /tmp/neo4j-home 2>/dev/null

# 应用能否连上 Neo4j
# source .venv && python -c "from agentic_graphrag.stores.neo4j_store import Neo4jGraphStore; ..."
```

---

## 7. 已知运行态与坑（本机 `/tmp` 路径）

| 现象 | 原因 / 处理 |
|------|-------------|
| `neo4j start` 成功，HTTP `7474` 有响应，但 `cypher-shell -u neo4j -p agentic-graphrag` **认证失败** | `set-initial-password` 仅对**全新 data 目录**有效；若 `/tmp/neo4j-home/data` 已初始化过，密码不是脚本里那次设置的值。日志常见：`The client is unauthorized due to authentication failure` |
| 健康检查脚本误判「Neo4j not ready」 | 进程已起（如 `neo4j status` → running），只是 **密码不对**，不是 JVM 未就绪 |
| 无 Docker 时 C3 仍 FAIL | 必须 Bolt 认证成功后再跑 `scripts/neo4j_regression.sh` |

**重置本地 tarball 图库密码（会清空该实例数据）：**

```bash
export JAVA_HOME=/tmp/jdk-17
export PATH="$JAVA_HOME/bin:/tmp/binshim:$PATH"
export NEO4J_HOME=/tmp/neo4j-home
"$NEO4J_HOME/bin/neo4j" stop
rm -rf "$NEO4J_HOME/data"/* "$NEO4J_HOME/logs"/*
rm -f "$NEO4J_HOME/.password_set"
"$NEO4J_HOME/bin/neo4j-admin" dbms set-initial-password agentic-graphrag
"$NEO4J_HOME/bin/neo4j" start
# 等就绪后：
"$NEO4J_HOME/bin/cypher-shell" -u neo4j -p agentic-graphrag 'RETURN 1'
```

更稳妥：有 Docker 时用 `docker compose up -d neo4j`（`NEO4J_AUTH=neo4j/agentic-graphrag`），不必维护 `/tmp` 密码状态。

---

## 8. 变更追踪

运行时版本与配置变更统一记录在 [CHANGELOG.md](../../CHANGELOG.md)。
