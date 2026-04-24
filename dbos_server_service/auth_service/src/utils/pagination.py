"""Pagination helpers."""

from dataclasses import dataclass


@dataclass
class PaginationParams:
    offset: int = 0
    limit: int = 50

    def __post_init__(self) -> None:
        if self.offset < 0:
            self.offset = 0
        if self.limit < 1:
            self.limit = 1
        if self.limit > 200:
            self.limit = 200
