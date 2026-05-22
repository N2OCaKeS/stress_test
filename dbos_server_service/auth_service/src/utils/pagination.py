"""Хелперы пагинации. Сейчас не очень активно используется, но пригодится."""

from dataclasses import dataclass


@dataclass
class PaginationParams:
    """offset/limit с sanity-капами. limit max 200 — больше за один запрос отдавать жадно."""
    offset: int = 0
    limit: int = 50

    def __post_init__(self) -> None:
        """Зажать значения в безопасный диапазон."""
        if self.offset < 0:
            self.offset = 0
        if self.limit < 1:
            self.limit = 1
        if self.limit > 200:
            self.limit = 200
