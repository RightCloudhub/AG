"""Admin use cases for immutable, tenant-owned model profile revisions."""

from __future__ import annotations

from pathlib import Path

from agentic_graphrag.config import AppConfig, Settings, runtime_dir
from agentic_graphrag.core.models import ModelProfile, ModelProfileTest, ModelRole
from agentic_graphrag.llm.capability_tests import test_profile
from agentic_graphrag.llm.model_catalog import (
    catalog_fingerprint,
    public_catalog,
    validate_bindings,
)
from agentic_graphrag.llm.profile_store import ModelProfileStore, ProfileConflict


class ModelProfiles:
    def __init__(self, cfg: AppConfig, settings: Settings, *, path: Path | None = None) -> None:
        self.cfg = cfg
        self.settings = settings
        self.store = ModelProfileStore(path or runtime_dir(cfg) / "model_profiles.sqlite3")

    def catalog(self, *, tenant_id: str) -> list[dict[str, object]]:
        return public_catalog(self.cfg.models, tenant_id)

    def save_draft(self, profile: ModelProfile, *, expected_revision: int) -> ModelProfile:
        validate_bindings(self.cfg.models, profile.roles, tenant_id=profile.tenant_id)
        draft = profile.model_copy(
            update={"catalog_fingerprint": catalog_fingerprint(self.cfg.models, profile.roles)}
        )
        return self.store.save_draft(draft, expected_revision=expected_revision)

    def test(self, profile_id: str, *, tenant_id: str, revision: int) -> ModelProfileTest:
        profile = self.store.get(profile_id, tenant_id=tenant_id, revision=revision)
        report = test_profile(profile, self.cfg, self.settings)
        self.store.save_test(report, tenant_id=tenant_id)
        return report

    def publish(
        self,
        profile_id: str,
        *,
        tenant_id: str,
        revision: int,
        expected_active_revision: int,
        actor: str,
    ) -> ModelProfile:
        profile = self.store.get(profile_id, tenant_id=tenant_id, revision=revision)
        self._validate_current_catalog(profile)
        active_revision = self.store.active_revision(profile_id, tenant_id=tenant_id)
        if active_revision:
            previous = self.store.get(profile_id, tenant_id=tenant_id, revision=active_revision)
            if self.embedding_changed(previous, profile):
                raise ProfileConflict("Embedding changes require a new profile and KB reindex")
        self.store.publish(profile, expected_active_revision=expected_active_revision, actor=actor)
        return profile

    def active(self, profile_id: str, *, tenant_id: str) -> ModelProfile:
        profile = self.store.active(profile_id, tenant_id=tenant_id)
        self._validate_current_catalog(profile)
        return profile

    def revision(self, profile_id: str, *, tenant_id: str, revision: int) -> ModelProfile:
        profile = self.store.published(profile_id, tenant_id=tenant_id, revision=revision)
        self._validate_current_catalog(profile)
        return profile

    def _validate_current_catalog(self, profile: ModelProfile) -> None:
        validate_bindings(self.cfg.models, profile.roles, tenant_id=profile.tenant_id)
        current = catalog_fingerprint(self.cfg.models, profile.roles)
        if current != profile.catalog_fingerprint:
            raise ProfileConflict("Model catalogue changed; create and test a new draft")

    def embedding_changed(self, before: ModelProfile, after: ModelProfile) -> bool:
        return before.roles.get(ModelRole.EMBEDDING) != after.roles.get(ModelRole.EMBEDDING)
