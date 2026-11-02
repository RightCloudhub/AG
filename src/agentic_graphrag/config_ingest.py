"""Durable indexing and upload capacity limits."""

from pydantic import BaseModel, Field


class IndexingConfig(BaseModel):
    embedding_batch_size: int = Field(default=32, ge=1)
    embedding_max_attempts: int = Field(default=4, ge=1)
    retry_base_seconds: float = Field(default=0.5, ge=0)
    retry_max_seconds: float = Field(default=30, ge=0)
    max_file_bytes: int = Field(default=50 * 1024 * 1024, ge=1)
    max_batch_files: int = Field(default=100, ge=1)
    max_upload_bytes: int = Field(default=500 * 1024 * 1024, ge=1)
    upload_part_bytes: int = Field(default=5 * 1024 * 1024, ge=1)
    upload_expiry_seconds: int = Field(default=86400, ge=1)
