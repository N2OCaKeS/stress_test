"""Type-correct diff в `update_account`.

`update_account` сравнивает присланные поля с текущим состоянием ORM-row'а,
чтобы no-op PATCH не дёргал fan-out. Раньше дамп шёл `mode="json"` — enum'ы и
datetime превращались в строки, и сравнение `str != native` давало бы
false-positive «изменилось» для любого нестрокового поля. Теперь дамп
`mode="python"` держит native-типы.

Прямой unit на источник, без БД: проверяем, что python-mode дамп схемы
сохраняет native-тип значения (на datetime — самый показательный случай),
а json-mode — нет. Это фиксирует контракт comparator'а независимо от того,
есть ли уже нестроковое поле в `ServerAccountUpdate`.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

from pydantic import BaseModel

from src.services import server_account as svc


class _Probe(BaseModel):
    """Минимальная схема с native-полями — модель того, что comparator
    обязан сравнивать корректно при появлении такого поля в Update-схеме."""

    flag: bool | None = None
    when: datetime | None = None


class TestPythonModeKeepsNativeTypes:
    def test_python_mode_preserves_datetime(self):
        ts = datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc)
        probe = _Probe(flag=True, when=ts)
        py = probe.model_dump(exclude_unset=True, mode="python")
        assert py["when"] == ts
        assert isinstance(py["when"], datetime)
        # На json-mode это была бы строка — сравнение с native-атрибутом
        # дало бы false-positive.
        js = probe.model_dump(exclude_unset=True, mode="json")
        assert isinstance(js["when"], str)
        assert py["when"] != js["when"]


class TestSourceUsesPythonMode:
    def test_update_account_dumps_in_python_mode(self):
        """Гард на источник: comparator не должен вернуться на mode='json'."""
        src = inspect.getsource(svc.update_account)
        assert 'mode="python"' in src
        assert 'model_dump(exclude_unset=True, mode="json")' not in src
