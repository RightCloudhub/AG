#!/usr/bin/env bash
# 项目初始化单一入口：解释器 → 锁定依赖 → .env → 环境体检。
# 幂等，可重复执行；已存在的 .env 永不被覆盖。
# 设计与取舍见 docs/design/SETUP_DESIGN.md。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

EXTRAS="dev,webapp"
ALL_EXTRAS=0
PY_VERSION=""
LOCKED=0
INEXACT=0
OFFLINE=0
WITH_DEMO_DATA=0
VERIFY=1
DRY_RUN=0
MIN_UV="0.9.0"

usage() {
  cat <<'MSG'
用法: ./scripts/setup.sh [选项]

  --extras LIST      逗号分隔的可选依赖组（默认 dev,webapp）
  --all-extras       安装全部可选依赖
  --python VERSION   指定解释器版本（默认取 .python-version）
  --locked           严格使用已提交 uv.lock，锁文件过期即失败（CI / 复现）
  --inexact          保留环境中不在锁文件里的额外包（默认精确同步）
  --offline          不访问网络
  --with-demo-data   追加构建离线演示数据（ingest + seed 图 + BM25 索引）
  --no-verify        跳过 scripts/doctor.py 环境体检
  --dry-run          只打印将执行的命令
  -h, --help         显示本帮助
MSG
}

step() { printf '\n==> %s\n' "$1"; }
run() { if (( DRY_RUN )); then printf '    [dry-run] %s\n' "$*"; else "$@"; fi; }

# 可选依赖组必须存在于 pyproject.toml 的 [project.optional-dependencies]
extra_exists() {
  awk -v want="$1" '
    /^\[project\.optional-dependencies\]/ { inblock = 1; next }
    /^\[/ { inblock = 0 }
    inblock {
      line = $0; sub(/#.*/, "", line)
      if (split(line, kv, "=") >= 2) {
        name = kv[1]; gsub(/[ \t]/, "", name)
        if (name == want) { found = 1 }
      }
    }
    END { exit (found ? 0 : 1) }
  ' "$ROOT/pyproject.toml"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --extras) EXTRAS="${2:?--extras 需要参数}"; shift 2 ;;
    --extras=*) EXTRAS="${1#*=}"; shift ;;
    --all-extras) ALL_EXTRAS=1; shift ;;
    --python) PY_VERSION="${2:?--python 需要参数}"; shift 2 ;;
    --python=*) PY_VERSION="${1#*=}"; shift ;;
    --locked) LOCKED=1; shift ;;
    --inexact) INEXACT=1; shift ;;
    --offline) OFFLINE=1; shift ;;
    --with-demo-data) WITH_DEMO_DATA=1; shift ;;
    --no-verify) VERIFY=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage >&2; exit 2 ;;
  esac
done

step "检查 uv"
if ! command -v uv >/dev/null 2>&1; then
  cat >&2 <<'MSG'
缺少 uv（本仓库依赖与解释器的唯一安装入口，见 plan/engineering/tech-stack.md ADR-008）。
  curl -LsSf https://astral.sh/uv/install.sh | sh
  或: pipx install uv  ·  https://docs.astral.sh/uv/getting-started/installation/
安装后重新运行 ./scripts/setup.sh
MSG
  exit 1
fi
UV_HAVE="$(uv --version | awk '{print $2}')"
if [[ "$(printf '%s\n%s\n' "$MIN_UV" "$UV_HAVE" | sort -V | head -1)" != "$MIN_UV" ]]; then
  echo "uv 版本过低：$UV_HAVE < $MIN_UV（uv sync --extra 语义要求 >= $MIN_UV）" >&2
  exit 1
fi
echo "    uv $UV_HAVE"

step "准备 Python"
if [[ -z "$PY_VERSION" ]]; then
  PY_VERSION="$(cat "$ROOT/.python-version" 2>/dev/null || echo 3.12)"
fi
run uv python install "$PY_VERSION"

step "校验可选依赖组"
SYNC_ARGS=(sync)
if (( ALL_EXTRAS )); then
  SYNC_ARGS+=(--all-extras)
else
  IFS=',' read -r -a _extras <<< "$EXTRAS"
  for extra in "${_extras[@]}"; do
    extra="${extra// /}"
    [[ -n "$extra" ]] || continue
    if ! extra_exists "$extra"; then
      echo "pyproject.toml 的 [project.optional-dependencies] 中没有 '$extra'" >&2
      exit 2
    fi
    SYNC_ARGS+=(--extra "$extra")
    echo "    + $extra"
  done
fi
(( LOCKED )) && SYNC_ARGS+=(--locked)
(( INEXACT )) && SYNC_ARGS+=(--inexact)
(( OFFLINE )) && SYNC_ARGS+=(--offline)

step "同步依赖：uv ${SYNC_ARGS[*]}"
run uv "${SYNC_ARGS[@]}"

step "准备 .env"
if [[ -f "$ROOT/.env" ]]; then
  echo "    已存在 .env，未改动"
elif (( DRY_RUN )); then
  echo "    [dry-run] cp .env.example .env"
else
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "    已从 .env.example 生成 .env（密钥留空；离线路径不需要任何 key）"
fi

if (( VERIFY )); then
  step "环境体检"
  VENV_PY="$ROOT/.venv/bin/python"
  [[ -x "$VENV_PY" ]] || VENV_PY="$(command -v python3)"
  run "$VENV_PY" "$ROOT/scripts/doctor.py" --root "$ROOT"
fi

if (( WITH_DEMO_DATA )); then
  step "构建离线演示数据"
  run bash "$ROOT/scripts/bootstrap_poc.sh"
fi

cat <<'MSG'

下一步
  source .venv/bin/activate
  agr-query --no-llm "Who is the CEO of the parent company of NovaTech Industries?"
  agr-run-cases --no-llm --cases evals/datasets/poc_cases.jsonl
  agr-api                                  # http://localhost:8000/web
  AGR_INGEST_INLINE=1 agr-api              # 上传的文档自动抽取入图
  python scripts/doctor.py --json          # 环境体检（机器可读）
MSG
