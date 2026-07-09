"""Схемы целей пробинга для server_worker.

Воркер держит фоновые probe-циклы (reachability = ping+ssh, power = ipmi/
domstate), которые вместо диспатча задач тянут плоский список целей через
`GET /internal/probe-targets` и снимают сигналы сами, без task-row'ов. Здесь —
контракт этого ответа: минимум полей адресации, которых хватает воркеру для
пробы и обратного callback'а состояния.

Серверы и ВМ идут отдельными списками: серверную пробу воркер снимает напрямую
(ping/ssh по `host`, ipmi через отдельный internal-fetch кред), а ВМ — через
управляющую SSH-сессию к hub'у, поэтому в цели ВМ уезжает адресация hub'а.
"""

from pydantic import BaseModel, Field


class ServerProbeTarget(BaseModel):
    """Одна серверная цель для reachability/power-пробы воркера.

    Поля зеркалят payload авто-задач (`auto_inventory._auto_payload`): их хватает
    и для сетевой пробы (`host`/`ssh_port`), и для IPMI-fetch'а по `server_id`
    (`department_id` уезжает в `X-Target-Department-Id`).
    """

    server_id: str = Field(description="ID сервера.")
    department_id: str | None = Field(
        default=None,
        description="Отдел сервера — форвардится воркером как X-Target-Department-Id.",
    )
    host: str = Field(description="IP-адрес сервера (str), SSH/ping-таргет.")
    ssh_port: int = Field(description="SSH-порт сервера для TCP-пробы.")
    is_managed: bool = Field(description="Подготовлен ли сервер (управляющий вход).")
    management_user: str | None = Field(
        default=None, description="Имя управляющего пользователя, если prepared.",
    )


class VmProbeTarget(BaseModel):
    """Одна ВМ-цель для статус-пробы воркера (питание domstate + ping/ssh гостя).

    ВМ живёт на hub-сервере, проба идёт через управляющую SSH-сессию к нему,
    поэтому цель несёт адресацию hub'а (`hub_*`) плюс имя домена и, если известен,
    LAN-адрес гостя. `department_id` (общий у ВМ и hub'а) уезжает в
    `X-Target-Department-Id` при callback'е состояния.
    """

    vm_id: str = Field(description="ID ВМ.")
    vm_name: str = Field(description="Имя домена libvirt на hub'е.")
    department_id: str | None = Field(
        default=None,
        description="Отдел ВМ (совпадает с отделом hub'а) для X-Target-Department-Id.",
    )
    network_mode: str = Field(description="Сетевой режим ВМ (bridge/nat).")
    guest_ip: str | None = Field(
        default=None,
        description="LAN-адрес гостя, если известен (для ping/ssh). None у NAT/SLIRP.",
    )
    hub_server_id: str = Field(description="ID hub-сервера, на котором крутится ВМ.")
    hub_host: str = Field(description="IP-адрес hub'а (str) — SSH-таргет.")
    hub_ssh_port: int = Field(description="SSH-порт hub'а.")
    hub_is_managed: bool = Field(description="Подготовлен ли hub (управляющий вход).")
    hub_management_user: str | None = Field(
        default=None, description="Управляющий пользователь hub'а.",
    )


class ProbeTargetsResponse(BaseModel):
    """Список целей пробинга: серверы и ВМ, плюс флаги усечения по cap'у."""

    servers: list[ServerProbeTarget] = Field(default_factory=list)
    vms: list[VmProbeTarget] = Field(default_factory=list)
    servers_truncated: bool = Field(
        default=False,
        description="Список серверов обрезан cap'ом (есть непокрытый хвост).",
    )
    vms_truncated: bool = Field(
        default=False,
        description="Список ВМ обрезан cap'ом (есть непокрытый хвост).",
    )
