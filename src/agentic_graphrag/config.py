"""Load application configuration from YAML + environment variables."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from agentic_graphrag.llm.budget import BudgetTracker
    from agentic_graphrag.llm.provider import LLMProvider


def _find_root() -> Path:
    """Locate repo root (handles editable install, site-packages, and cwd)."""
    env = os.environ.get("AGENTIC_GRAPHRAG_ROOT")
    if env:
        return Path(env).resolve()
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        found = _root_from_start(start)
        if found is not None:
            return found
    return _root_near_src() or Path.cwd().resolve()


def _root_from_start(start: Path) -> Path | None:
    for candidate in [start, *start.parents]:
        if _is_full_root(candidate) or _is_light_root(candidate):
            return candidate.resolve()
    return None


def _is_full_root(candidate: Path) -> bool:
    if not (candidate / "configs" / "default.yaml").exists():
        return False
    return (candidate / "pyproject.toml").exists() or (candidate / "PRD.md").exists()


def _is_light_root(candidate: Path) -> bool:
    return (candidate / "pyproject.toml").exists() and (candidate / "configs").exists()


def _root_near_src() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists() and (parent / "src").exists():
            return parent
    return None


ROOT_DIR = _find_root()
DEFAULT_CONFIG_PATH = ROOT_DIR / "configs" / "default.yaml"


class GuardrailsConfig(BaseModel):
    max_hops: int = 5
    max_llm_calls: int = 20
    max_tokens_per_query: int = 50_000
    query_timeout_seconds: int = 60
    recursion_limit: int = 15


class GraphRetrievalConfig(BaseModel):
    """Graph retrieval caps (P2-RT-01) — no magic numbers in retriever code paths."""

    max_hop_neighbors: int = 2
    max_path_hops: int = 4
    max_neighbors_per_layer: int = 50
    max_paths: int = 20
    beam_width: int = 20
    high_degree_threshold: int = 30
    relation_relevance_threshold: float = 0.12


class RetrievalConfig(BaseModel):
    top_k: int = 10
    vector_top_k: int = 10
    fulltext_top_k: int = 10
    fusion_method: str = "rrf"
    fusion_k: int = 60
    parallel: bool = True
    cache_answer_ttl_seconds: float = 3600.0
    graph: GraphRetrievalConfig = Field(default_factory=GraphRetrievalConfig)


class TriageConfig(BaseModel):
    enabled: bool = True
    escalate_fast_path: bool = True


class KnowledgeConfig(BaseModel):
    chunk_size_chars: int = 1200
    chunk_overlap_chars: int = 150
    extract_confidence_threshold: float = 0.5
    schema_path: str = "configs/schema/domain_v0.yaml"
    # P2-KG-01 extraction pipeline
    extract_max_attempts: int = 3
    extract_retry_base_delay_seconds: float = 0.5
    extract_journal_path: str = "data/processed/extract_journal.jsonl"
    extract_quarantine_path: str = "data/processed/extract_quarantine.jsonl"


class LLMConfig(BaseModel):
    strong_model: str = "gpt-4.1"
    light_model: str = "gpt-4.1-mini"
    embedding_model: str = "text-embedding-3-small"
    # Optional defaults; env LLM_EMBEDDING_BASE_URL / LLM_EMBEDDING_API_KEY override.
    # Empty → use chat endpoint (LLM_BASE_URL / LLM_API_KEY).
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    temperature: float = 0.0
    max_retries: int = 2
    timeout_seconds: int = 60


class PathsConfig(BaseModel):
    data_dir: str = "data"
    raw_docs_dir: str = "data/raw"
    processed_dir: str = "data/processed"
    cache_dir: str = "data/cache"
    indexes_dir: str = "data/indexes"
    prompts_dir: str = "configs/prompts"


class EvalConfig(BaseModel):
    cases_path: str = "evals/datasets/poc_cases.jsonl"
    report_dir: str = "reports"


class AppConfig(BaseModel):
    name: str = "agentic-graphrag"
    env: str = "local"
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    triage: TriageConfig = Field(default_factory=TriageConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    eval: EvalConfig = Field(default_factory=EvalConfig)


class Settings(BaseSettings):
    """Secrets and connection endpoints from environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_base_url: str = Field(default="https://api.openai.com/v1", alias="LLM_BASE_URL")
    llm_strong_model: str | None = Field(default=None, alias="LLM_STRONG_MODEL")
    llm_light_model: str | None = Field(default=None, alias="LLM_LIGHT_MODEL")
    llm_embedding_model: str | None = Field(default=None, alias="LLM_EMBEDDING_MODEL")
    # Separate embedding provider (OpenAI-compatible). Empty → fall back to chat URL/key.
    llm_embedding_base_url: str | None = Field(default=None, alias="LLM_EMBEDDING_BASE_URL")
    llm_embedding_api_key: str | None = Field(default=None, alias="LLM_EMBEDDING_API_KEY")

    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", alias="NEO4J_USER")
    neo4j_password: str = Field(default="agentic-graphrag", alias="NEO4J_PASSWORD")

    qdrant_url: str = Field(default="http://localhost:6333", alias="QDRANT_URL")
    qdrant_collection: str = Field(default="agentic_chunks", alias="QDRANT_COLLECTION")

    max_hops: int | None = Field(default=None, alias="MAX_HOPS")
    max_llm_calls: int | None = Field(default=None, alias="MAX_LLM_CALLS")
    max_tokens_per_query: int | None = Field(default=None, alias="MAX_TOKENS_PER_QUERY")


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping: {path}")
    return data


def resolve_path(relative: str | Path) -> Path:
    """Resolve a project-relative path against repo root."""
    p = Path(relative)
    if p.is_absolute():
        return p
    return (ROOT_DIR / p).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_config(config_path: str | None = None) -> AppConfig:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.is_absolute():
        path = ROOT_DIR / path
    raw = _load_yaml(path)
    cfg = AppConfig.model_validate(raw)
    _apply_settings_overrides(cfg, get_settings())
    _ = os.environ.get("AGENTIC_GRAPHRAG_ENV", "local")
    return cfg


def _apply_settings_overrides(cfg: AppConfig, settings: Settings) -> None:
    if settings.llm_strong_model:
        cfg.llm.strong_model = settings.llm_strong_model
    if settings.llm_light_model:
        cfg.llm.light_model = settings.llm_light_model
    if settings.llm_embedding_model:
        cfg.llm.embedding_model = settings.llm_embedding_model
    if settings.llm_embedding_base_url:
        cfg.llm.embedding_base_url = settings.llm_embedding_base_url
    if settings.llm_embedding_api_key:
        cfg.llm.embedding_api_key = settings.llm_embedding_api_key
    if settings.max_hops is not None:
        cfg.guardrails.max_hops = settings.max_hops
    if settings.max_llm_calls is not None:
        cfg.guardrails.max_llm_calls = settings.max_llm_calls
    if settings.max_tokens_per_query is not None:
        cfg.guardrails.max_tokens_per_query = settings.max_tokens_per_query


def resolve_chat_base_url(settings: Settings | None = None) -> str:
    s = settings or get_settings()
    return (s.llm_base_url or "https://api.openai.com/v1").rstrip("/")


def resolve_embedding_base_url(
    settings: Settings | None = None,
    cfg: AppConfig | None = None,
) -> str:
    """Embedding endpoint; falls back to chat ``LLM_BASE_URL`` when unset."""
    s = settings or get_settings()
    c = cfg or get_config()
    for candidate in (s.llm_embedding_base_url, c.llm.embedding_base_url):
        if candidate and str(candidate).strip():
            return str(candidate).strip().rstrip("/")
    return resolve_chat_base_url(s)


def resolve_embedding_api_key(
    settings: Settings | None = None,
    cfg: AppConfig | None = None,
) -> str:
    """Embedding API key; falls back to ``LLM_API_KEY`` when unset."""
    s = settings or get_settings()
    c = cfg or get_config()
    for candidate in (s.llm_embedding_api_key, c.llm.embedding_api_key):
        if candidate is not None and str(candidate).strip():
            return str(candidate).strip()
    return s.llm_api_key or ""


def build_llm_provider(
    *,
    budget: BudgetTracker | None = None,
    cache_dir: str | Path | None = None,
    settings: Settings | None = None,
    cfg: AppConfig | None = None,
) -> LLMProvider:
    """Construct ``LLMProvider`` with chat vs embedding endpoint split."""
    from agentic_graphrag.llm.provider import LLMProvider

    s = settings or get_settings()
    c = cfg or get_config()
    return LLMProvider(
        api_key=s.llm_api_key,
        base_url=resolve_chat_base_url(s),
        strong_model=c.llm.strong_model,
        light_model=c.llm.light_model,
        embedding_model=c.llm.embedding_model,
        embedding_base_url=resolve_embedding_base_url(s, c),
        embedding_api_key=resolve_embedding_api_key(s, c),
        temperature=c.llm.temperature,
        timeout_seconds=c.llm.timeout_seconds,
        budget=budget,
        cache_dir=cache_dir,
    )


def load_prompt(name: str, prompts_dir: str | Path | None = None) -> str:
    """Load a prompt file by stem name (e.g. 'extract' → extract.md)."""
    base = resolve_path(prompts_dir or get_config().paths.prompts_dir)
    path = base / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")
