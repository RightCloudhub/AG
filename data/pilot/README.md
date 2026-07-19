# 试点语料库（P1-GOV-01）

本目录承载 **正式试点领域** 语料的元数据与接入约定。  
当前仓库 POC 使用的 interim 语料在 `data/raw/`（6 篇公司关系文档），**不能**替代本目录关闭 G1→G2 条件 C1。

## 布局

```
data/pilot/
├── README.md                 # 本文件
├── MANIFEST.template.yaml    # 复制为 MANIFEST.yaml 后填写
├── MANIFEST.yaml             # 正式清单（可提交非敏感元数据；默认 gitignore 大文件）
└── raw/                      # 原始文档（.md / .txt / .html）
    └── …                     # ≥100 篇；敏感语料可外挂，此处放 README 指针
```

## 接入约定

1. 文档编码 UTF-8；一文件一文（或 HTML 单页）。  
2. 文件名稳定即可；系统会生成 `doc_id`（内容哈希）。  
3. 禁止将未脱敏的 PII / 未授权商业秘密提交到 git。  
4. 外挂路径：在 `MANIFEST.yaml` 的 `raw_path` 写绝对路径或挂载点，并保证 CI/开发机可读。

## 合成语料（工程脚手架）

若尚无产品授权的真实领域语料，可生成 **合成公司关系语料**（≥100 篇，英文 Markdown，无 PII）供 C1 工程校验与 ingest 冒烟：

```bash
python3 scripts/generate_pilot_corpus.py
# 或指定规模
python3 scripts/generate_pilot_corpus.py --count 120
```

- 输出目录：`data/pilot/raw/`（默认 gitignore，见仓库根 `.gitignore`）
- 同步填写：`data/pilot/MANIFEST.yaml`（从 `MANIFEST.template.yaml` 复制；仓库内已有 synthetic 草稿）
- **注意**：合成语料可关闭工程向 C1 规模/接入门槛，**不能**单独作为 G2 效果门禁的「真实试点领域」锁定；`domain_locked` / `product_signoff` 须产品重签。

## 校验

```bash
./scripts/validate_pilot_corpus.sh
```

## 与 interim 语料关系

| | interim (`data/raw/`) | pilot (`data/pilot/`) |
|--|----------------------|------------------------|
| 用途 | G1 offline POC | G1→G2 C1 + Phase-2/G2 效果 |
| 规模 | 6 篇 | ≥100 篇 |
| Schema | `corporate_relations_v0` | 按领域修订 |
| 评测 | `evals/datasets/poc_cases.jsonl` | 扩展 ≥200（P2-EV-01） |
