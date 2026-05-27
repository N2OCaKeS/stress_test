"""Хелперы пагинации для list-эндпоинтов (offset/limit + total)."""

from dataclasses import dataclass

from fastapi import Query

# Дефолтная страница и defensive-кап на размер страницы — защита от запроса
# гигантских выборок.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@dataclass
class PaginationParams:
    """offset/limit с sanity-капами."""
    offset: int = 0
    limit: int = DEFAULT_LIMIT

    def __post_init__(self) -> None:
        """Зажать значения в безопасный диапазон."""
        if self.offset < 0:
            self.offset = 0
        if self.limit < 1:
            self.limit = 1
        if self.limit > MAX_LIMIT:
            self.limit = MAX_LIMIT


def pagination_params(
    limit: int = Query(
        DEFAULT_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description=f"Сколько записей вернуть (1..{MAX_LIMIT}).",
    ),
    offset: int = Query(0, ge=0, description="Сколько записей пропустить."),
) -> PaginationParams:
    """FastAPI-зависимость: пагинация из query-параметров `limit`/`offset`."""
    return PaginationParams(offset=offset, limit=limit)
