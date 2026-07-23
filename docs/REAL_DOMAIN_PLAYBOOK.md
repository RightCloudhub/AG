# 真域语料接入剧本（产品可验收）

**目的：** 用**产品授权**的真实领域语料替换合成 `corporate_relations` pilot，关闭 C1 产品 caveat 与 G2 效果门禁的语料前提。

**工程不能代替产品签字。** 本剧本只提供目录、校验与流水线。

---

## 1. 决策清单（产品 / 法务）

| # | 问题 | 负责人 | 输出 |
|---|------|--------|------|
| 1 | 试点领域是什么？（制度/合同/工单/供应链…） | 产品 | `domain.id` + 中文名 |
| 2 | 数据来源与授权协议？ | 法务/产品 | 协议编号或内部批复 |
| 3 | 是否脱敏？可否进 git / 仅挂载？ | 安全 | `pii_desensitized` + 路径策略 |
| 4 | 多跳问题类型？（组织→人→事件…） | 产品+KG | `multihop_focus` 列表 |
| 5 | Schema 实体/关系类型 | KG | 修订 `configs/schema/` |

全部勾选后填写 `data/pilot/MANIFEST.yaml` 并将 `status: approved` + `checklist.product_signoff: true`。

---

## 2. 目录约定

```text
data/pilot/
  MANIFEST.yaml          # 正式清单（可提交非敏感元数据）
  raw/                   # ≥100 篇 UTF-8 .md/.txt（或外挂路径）
docs/REAL_DOMAIN_PLAYBOOK.md
scripts/import_real_corpus.sh
scripts/validate_pilot_corpus.sh
```

敏感原文**不要**进 git：`raw_path` 可写绝对路径 / NFS 挂载。

---

## 3. 一键接入

```bash
# 1) 把授权文档拷入（示例）
./scripts/import_real_corpus.sh /path/to/authorized_docs

# 2) 编辑 MANIFEST.yaml：domain / authorization / product_signoff

# 3) 校验规模与清单
./scripts/validate_pilot_corpus.sh

# 4) 入库 + 图（live 抽取需 LLM_API_KEY）
source .venv/bin/activate
export PYTHONPATH=src
set -a && source .env && set +a
agr-ingest --input data/pilot/raw --out data/processed/pilot_chunks.jsonl
# 真域：live 抽取（勿用 pilot_triples 合成宇宙冒充）
agr-build-graph   # 无 --triples；写 triples.jsonl + Neo4j/memory
agr-index

# 5) 新域金标（模板需按 schema 调整后再 gen-cases）
# 不可直接复用公司关系 g2_* 金标宣称真域效果
```

---

## 4. 与合成 pilot 的关系

| | 合成 pilot | 真域 |
|--|-----------|------|
| 用途 | CI / offline / 工程门禁 | 产品 G2/G3 效果与验收 |
| 金标 | `g2_*.jsonl` 路径模板 | **新**标注或改模板再生 |
| MANIFEST | `source: synthetic_generated` | 授权字段完整 + 产品签字 |

`reports/ACCEPTANCE_STATUS.json` 在真域签字前保持 `corpus: synthetic_pilot_caveat`。

---

## 5. 验收证据包

1. 签字版 `MANIFEST.yaml`（或导出 PDF 批复）  
2. `validate_pilot_corpus.sh` 日志  
3. 抽检 ≥70% 三元组 spotcheck  
4. heldout live agentic vs baseline 报告（**该域**金标）  
5. 更新 `docs/IMPORTANT.md` / `plan/roadmap.md` G2 勾选  

---

## 6. 若暂时没有真域

继续使用合成 pilot 做工程回归；**不得**把合成 offline/live 数字写成产品关闸结论。  
当前 live heldout（合成）仅作工程效果信号。
