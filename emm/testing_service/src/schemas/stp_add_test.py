"""Pydantic-схемы для §D6/D7 — добавление одного теста EMM в СТП."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StpAddTestRequest(BaseModel):
    """Тело POST /stp/test-runs/{run_id}/add-test."""

    test_id: str = Field(..., min_length=1, description="test_definitions.id — тест из каталога EMM.")


class StpAddTestOperationResponse(BaseModel):
    """Ответ — текущее шаговое состояние операции (§D7). Повторный вызов на
    ту же пару `(test_id, run_id)` возвращает эту же строку, продолженную с
    первого не пройденного шага."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    department_id: str
    test_definition_id: str
    stp_test_run_id: str
    stp_test_case_id: str | None = None
    stp_cell_id: str | None = None
    zephyr_testcase_created: bool = False
    zephyr_added_to_run: bool = False
    stp_cell_created: bool = False
    life_published: bool = False
    status: str
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
