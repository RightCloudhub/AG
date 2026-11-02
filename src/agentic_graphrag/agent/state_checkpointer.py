"""Version-aware adapter around LangGraph's memory and SQLite checkpoint stores."""

from __future__ import annotations

from collections.abc import AsyncIterator, Collection, Iterator, Sequence

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

from agentic_graphrag.agent.graph_types import AgentCheckpointer, RunnableConfig
from agentic_graphrag.agent.state_migration import migrate_checkpoint
from agentic_graphrag.agent.state_serde import AgentStateSerializer


class StateCheckpointer(BaseCheckpointSaver[str]):
    """Delegate persistence, migrating channels before LangGraph sees a snapshot."""

    def __init__(self, backend: AgentCheckpointer) -> None:
        self.backend = backend
        if backend.serde is BaseCheckpointSaver.serde:
            backend.serde = AgentStateSerializer()
        super().__init__(serde=backend.serde)

    def with_allowlist(self, extra_allowlist: Collection[tuple[str, ...]]) -> StateCheckpointer:
        return StateCheckpointer(self.backend.with_allowlist(extra_allowlist))

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return migrate_checkpoint(self.backend.get_tuple(config))

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, object] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        for saved in self.backend.list(config, filter=filter, before=before, limit=limit):
            migrated = migrate_checkpoint(saved)
            if migrated is not None:
                yield migrated

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        *versions: ChannelVersions,
        new_versions: ChannelVersions | None = None,
    ) -> RunnableConfig:
        return self.backend.put(config, checkpoint, metadata, _versions(versions, new_versions))

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, object]],
        task_id: str,
        *paths: str,
        task_path: str = "",
    ) -> None:
        self.backend.put_writes(config, writes, task_id, paths[0] if paths else task_path)

    def get_next_version(self, current: str | None, channel: None) -> str:
        return self.backend.get_next_version(current, channel)

    def delete_thread(self, thread_id: str) -> None:
        self.backend.delete_thread(thread_id)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return migrate_checkpoint(await self.backend.aget_tuple(config))

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, object] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        async for saved in self.backend.alist(config, filter=filter, before=before, limit=limit):
            migrated = migrate_checkpoint(saved)
            if migrated is not None:
                yield migrated

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        *versions: ChannelVersions,
        new_versions: ChannelVersions | None = None,
    ) -> RunnableConfig:
        return await self.backend.aput(
            config, checkpoint, metadata, _versions(versions, new_versions)
        )

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, object]],
        task_id: str,
        *paths: str,
        task_path: str = "",
    ) -> None:
        await self.backend.aput_writes(config, writes, task_id, paths[0] if paths else task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        await self.backend.adelete_thread(thread_id)


def _versions(
    positional: tuple[ChannelVersions, ...], named: ChannelVersions | None
) -> ChannelVersions:
    if positional:
        return positional[0]
    if named is None:
        raise TypeError("new_versions is required")
    return named


def typed_checkpointer(backend: AgentCheckpointer) -> StateCheckpointer:
    return backend if isinstance(backend, StateCheckpointer) else StateCheckpointer(backend)
