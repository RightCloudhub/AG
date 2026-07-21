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

卷名：`neo4j_data`、`qdrant_data`（Docker volume，非 git）。

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

### 3.1 Vue 3 运行时 vendor（试用 Web，ADR-006）

| 项 | 值 |
|----|-----|
| 版本 | **3.5.13**（升级须同步 ADR-006、`web/static/app.js` 的 `VUE_VERSION`、本文件） |
| 产物 | `vue.esm-browser.prod.js`（全量构建，含浏览器内模板编译） |
| 本地路径 | `web/static/vendor/vue.esm-browser.prod.js`（**建议 vendor 后提交入库**，完全离线） |
| 加载顺序 | vendor → 钉版 jsDelivr → 钉版 unpkg（见 `web/static/app.js`） |
| 说明文档 | [`web/static/vendor/README.md`](../web/static/vendor/README.md) |
| 许可 | MIT · 约 170 KB min |

```bash
mkdir -p web/static/vendor
curl -fsSL -o web/static/vendor/vue.esm-browser.prod.js \
  https://unpkg.com/vue@3.5.13/dist/vue.esm-browser.prod.js
```

---

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
4. 变更镜像 tag（如升级 Neo4j 5.26 → 更新）时：改 `docker-compose.yml` + 本文件 + 简短 changelog。

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

## 8. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-07-20 | 首次记录：`/tmp` Temurin 17.0.19 + Neo4j 5.26.0 tarball；对照 `docker-compose.yml` 的 neo4j/qdrant 镜像 |
| 2026-07-20 | 补充：tarball Neo4j 已能 start，但初始密码/已有 data 导致认证失败；增加重置步骤 |
| 2026-07-21 | P5-UI-01 / ADR-006：§3 增加 Vue 3.5.13 运行时（浏览器 ESM / vendor，**非 npm**）与 §3.1 vendor 小节 |
