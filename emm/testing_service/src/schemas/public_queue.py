"""Публичные операции запуска принимают только несекретный контекст ОС."""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class QueueLaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=128)
    test_id: str = Field(min_length=1, max_length=64)
    stand_id: str = Field(min_length=1, max_length=64)
    os_version_id: str = Field(min_length=1, max_length=64)
    kernel: str = Field(min_length=1, max_length=64)
    mode: Literal["orel", "smolensk"]
    debug_mode: bool = False


class QueueRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=128)


class PublicQueueItem(BaseModel):
    test_code: str | None = None
    test_name: str | None = None
    is_current: bool = True
    id: str
    test_id: str
    stand_id: str
    test_run_id: str | None
    retry_of_id: str | None
    debug_mode: bool
    state: str
    rc: str | None
    kernel: str | None
    mode: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
