"""Store wiring for a resolved profile (single entry point for every driver)."""

from __future__ import annotations

from agentic_graphrag.bootstrap.profile import ProfileError, RuntimeProfile, missing_credentials
from agentic_graphrag.config import AppConfig, Settings, get_config, get_settings
from agentic_graphrag.stores.factory import StoreBundle, create_bundle


def build_stores(
    rp: RuntimeProfile,
    *,
    cfg: AppConfig | None = None,
    settings: Settings | None = None,
) -> StoreBundle:
    """Build the StoreBundle the profile declares, or raise ``ProfileError``."""
    cfg = cfg or get_config()
    settings = settings or get_settings()
    missing = missing_credentials(rp, settings)
    if missing:
        raise ProfileError(f"profile {rp.profile.value!r} missing " + ", ".join(missing))
    try:
        return create_bundle(
            rp.caps.graph,
            rp.caps.vector,
            cfg=cfg,
            settings=settings,
            fulltext_backend=rp.caps.fulltext,
            load_bm25=rp.caps.load_local_indexes,
            load_embeddings=rp.caps.load_local_indexes,
        )
    except Exception as exc:  # noqa: BLE001 — re-raised with profile context
        raise ProfileError(
            f"profile {rp.profile.value!r} could not open its stores "
            f"(graph={rp.caps.graph.value}, vector={rp.caps.vector.value}): "
            f"{type(exc).__name__}: {exc}"
        ) from exc
