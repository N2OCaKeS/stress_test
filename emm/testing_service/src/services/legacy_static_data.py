"""Статичные справочники, перенесённые байт-в-байт из legacy `allta_app` (§12).

Ни у одного из этих словарей в легаси нет живого продюсера — `testname_columns`,
`known_bugs` и `annotations` жили как модульные константы в
`allta_image_conf.py`, `box-config.json` не перегенерировался никаким кодом.
В отличие от `astra_qa_stand_client.py`/`server_client.resolve_repository_urls`
(там легаси реально что-то вычислял или качал), здесь порт — это буквально
скопировать данные, поэтому они лежат тут одним модулем, а не превращены в
таблицы БД ради данных, которые никто в testing_service не редактирует.
"""

from __future__ import annotations

# Соответствие «сырое имя теста в Zephyr» → «короткая колонка СТП».
# Легаси: `allta_app/allta_image_conf.py:526-559`.
TESTNAME_COLUMNS: dict[str, str] = {
    "file system benchmark. EXT4": "FS_EXT4",
    "file system benchmark. XFS": "FS_XFS",
    "file system benchmark. OCFS2": "FS_OCFS2",
    "file system benchmark. NTFS": "FS_NTFS",
    "auditd benchmark. psaud": "Auditd_psaud",
    "linux_system_benchmark. UnixBench": "UnixBench",
    "file system benchmark. EXT3": "FS_EXT3",
    "file system benchmark. EXT2": "FS_EXT2",
    "file system benchmark. FAT": "FS_FAT",
    "syslog-ng benchmark": "Syslog-NG",
    "postgresql benchmark": "PostgreSQL",
    "file system benchmark. EXT4 parsec": "FS_EXT4_parsec",
    "auditd benchmark. fileaud": "Auditd_fileaud",
    "auditd benchmark. useraud": "Auditd_useraud",
    "file system benchmark. OCFS2 parsec": "FS_OCFS2_parsec",
    "postgresql benchmark smol": "PostgreSQL_smol",
    "postgresql benchmark audit-off": "PSQL_audit-off",
    "storage drive overflow": "SD_overflow",
    "ram overflow": "RAM_overflow",
    "file system benchmark. XFS parsec": "FS_XFS_parsec",
    "postgresql benchmark parsec": "PSQL_parsec",
    "postgresql benchmark vanilla": "PSQL_vanilla",
    "tantor benchmark vanilla": "Tantor_vanilla",
    "postgresql benchmark kernels": "PSQL_kernels",
    "tantor benchmark kernels": "Tantor_kernels",
    "linux_system_benchmark. UnixBench parsec": "UnixBench_parsec",
    "postgresql benchmark balance": "PSQL_balance",
    "freeipa authentication test": "FreeIPA_auth",
    "Parsec impact fs benchmark": "Parsec_impact-fs",
    "Parsec impact fs benchmark audit-off": "Parsec_imp-fs_aud-off",
    "Apache_ReverseProxy": "Apache_RP",
    "Apache_BenchPam": "Apache_BP",
    "Steal time": "Steal_time",
    "file system benchmark. EXFAT": "FS_EXFAT",
    "FIO benchmark": "FIO",
    "Virt UnixBench": "vUnixBench",
    "vPingPong": "vPingPong",
    "Steal time smolensk": "Steal_time-sm",
    "postgresql benchmark oom": "PSQL_OOM",
    "syslog-ng benchmark check-write-log": "Syslog-NG-cwl",
    "DIGSIG. Check digsig time": "DIGSIG-cdt",
    "docker web-application": "Docker-WA",
    "ceph benchmark": "FS_CEPH",
    "ceph fio benchmark": "FS_CEPH_fio",
    "freeipa create users test": "FreeIPA_c-users",
    "ceph parsec benchmark": "FS_CEPH_parsec",
    "astra openvpn client connections": "AOpenVPNcc",
    "dovecot benchmark": "Dovecot-IMAP",
    "exim benchmark": "Exim4-SMTP",
    "Large FIO benchmark": "FIO_large",
    "Network benchmark. Init_on_free": "InitOnFree",
    "Network benchmark. DHCP": "DHCP",
    "segmentation_fault": "SegFault",
    "postgresql benchmark olap": "PSQL_OLAP-hq",
    "freeipa plugin test": "FreeIPA_plugin",
    "xfs memory leak": "XFS_mem_leak",
    "Raw spin lock benchmark": "Raw_spin-lock",
    "postgresql benchmark info-sys": "PSQL_info-sys",
    "postgresql benchmark info-sys-orel": "PSQL_info-sys_orel",
    "astraeventsd benchmark": "Astraevents",
    "astraeventsd benchmark smolensk": "Astraevents_sm",
    "Apache_Balance": "Apache_Balance",
    "OS usage": "OS_usage",
}

# Известные баги, привязанные к категории теста.
# Легаси: `allta_app/allta_image_conf.py:620-649`.
KNOWN_BUGS: dict[str, dict[str, str]] = {
    "PostgreSQL": {
        "BT-51261": "https://jira.astralinux.ru/browse/BT-51261",
        "BT-37797": "https://jira.astralinux.ru/browse/BT-37797",
        "BT-40316": "https://jira.astralinux.ru/browse/BT-40316",
        "BT-35869": "https://jira.astralinux.ru/browse/BT-35869",
        "BT-48408": "https://jira.astralinux.ru/browse/BT-48408",
        "BT-61532": "https://jira.astralinux.ru/browse/BT-61532",
        "BT-76604": "https://jira.astralinux.ru/browse/BT-76604",
        "BT-96667": "https://jira.astralinux.ru/browse/BT-96667",
    },
    "Файловые системы": {
        "BT-38366": "https://jira.astralinux.ru/browse/BT-38366",
        "BT-54712": "https://jira.astralinux.ru/browse/BT-54712",
        "BT-99593": "https://jira.astralinux.ru/browse/BT-99593",
    },
    "Parsec": {
        "BT-61530": "https://jira.astralinux.ru/browse/BT-61530",
        "BT-52579": "https://jira.astralinux.ru/browse/BT-52579",
        "BT-69978": "https://jira.astralinux.ru/browse/BT-69978",
        "BT-92428": "https://jira.astralinux.ru/browse/BT-92428",
    },
    "Apache": {
        "BT-64331": "https://jira.astralinux.ru/browse/BT-64331",
    },
    "FreeIPA": {
        "BT-66518": "https://jira.astralinux.ru/browse/BT-66518",
        "BT-91580": "https://jira.astralinux.ru/browse/BT-91580",
    },
}

# Пояснительные аннотации к артефактам производительности по категориям.
# Легаси: `allta_app/allta_image_conf.py:656-697`.
ANNOTATIONS: dict[str, str] = {
    "Apache": (
        "\nУвеличение рейтинга x2 в 1.7.8 связано с переходом на новые "
        "сервера. Для удобства сравнения оставлены старые результаты.\n"
    ),
    "Docker": (
        "\nНебольшое увеличение рейтинга в 1.7.8 связано с переходом на "
        "новые сервера. Для удобства сравнения оставлены старые результаты.\n"
    ),
    "FreeIPA": (
        "\nНебольшое увеличение рейтинга в 1.7.8 связано с переходом на "
        "новые сервера. Для удобства сравнения оставлены старые результаты.\n"
    ),
    "Parsec": (
        "\nНебольшое увеличение рейтинга в 1.7.8 связано с переходом на "
        "новые сервера. Для удобства сравнения оставлены старые результаты.\n"
    ),
    "PostgreSQL": (
        "\nПадение производительности в 1.7.6 было вызвано ошибкой "
        "обновления правил аудита при astra-update -ATr. BT-61530.\n"
        "Падение производительности в 1.8.0 связано с блокировками. "
        "BT-51261.\n"
    ),
    "Qemu/KVM/Libvirt": (
        "\nУвеличение рейтинга в 1.7.8 связано с переходом на новые "
        "сервера. Для удобства сравнения оставлены старые результаты.\n"
    ),
    "UnixBench": (
        "\nПадение производительности в 1.7.6 было вызвано ошибкой "
        "обновления правил аудита при astra-update -ATr. BT-61530.\n"
    ),
    "Системные службы": (
        "\nSyslog-NG: 1.8.0-1.8.1.UU.2 - была ошибка в подсчете рейтинга, "
        "при необходимости переделать.\n"
        "Auditd-files: 1.7.6-1.7.6.UU.2 - была ошибка в подсчете рейтинга, "
        "при необходимости переделать.\n"
        "Небольшое увеличение рейтинга в 1.7.8 связано с переходом на "
        "новые сервера. Для удобства сравнения оставлены старые "
        "результаты.\n"
    ),
    "Файловые системы": (
        "\nУвеличение рейтинга в 1.7.8 связано с переходом на новые "
        "сервера. Для удобства сравнения оставлены старые результаты.\n"
    ),
}

# Адрес FTP в `BOX_CONFIG` ниже, как он записан в легаси `box-config.json`.
# Маршрут подменяет этот префикс значением глобальной переменной `FTP_URL`
# — сам адрес живёт в БД.
LEGACY_FTP_BASE = "ftp://10.177.103.10"

# Легаси `vagrant_box`: версия → [имя образа, ftp-URL]. Не имеет emm-аналога —
# провижининг через vagrant-боксы в testing_service не используется, маршрут
# остаётся справочным. Легаси: `allta_app/box-config.json`.
BOX_CONFIG: dict[str, list[dict[str, list[str]]]] = {
    "vagrant_box": [
        {
            "1.8.0.s": [
                "smolensk-vanilla-gui/1.8.0.15",
                "ftp://10.177.103.10/boxes/box/smolensk-vanilla-gui-1.8.0-gmg15.0.0-virtualbox.box",
            ],
        },
        {
            "1.8.0.v": [
                "voronezh-vanilla-gui/1.8.0.15",
                "ftp://10.177.103.10/boxes/voronezh-vanilla-gui-1.8.0.json",
            ],
        },
        {
            "1.8.0.o": [
                "orel-vanilla-gui/1.8.0.15",
                "ftp://10.177.103.10/boxes/box/orel-vanilla-gui-1.8.0-gmg15.0.0-virtualbox.box",
            ],
        },
        {
            "1.8.1.s": [
                "smolensk-vanilla-gui/1.8.1.6",
                "ftp://10.177.103.10/boxes/box/1.8.1.s.box",
            ],
        },
        {
            "1.8.1.v": [
                "voronezh-vanilla-gui/1.8.1.6",
                "ftp://10.177.103.10/boxes/box/1.8.1.v.box",
            ],
        },
        {
            "1.8.1.o": [
                "orel-vanilla-gui/1.8.1.6",
                "ftp://10.177.103.10/boxes/box/1.8.1.o.box",
            ],
        },
        {
            "1.7.5.s": [
                "smolensk-vanilla-gui/1.7.5",
                "ftp://10.177.103.10/boxes/box/1.7.5.s.box",
            ],
        },
        {
            "1.7.5.v": [
                "voronezh-vanilla-gui/1.7.5",
                "ftp://10.177.103.10/boxes/box/1.7.5.v.box",
            ],
        },
        {
            "1.7.5.o": [
                "orel-vanilla-gui/1.7.5",
                "ftp://10.177.103.10/boxes/box/1.7.5.o.box",
            ],
        },
        {
            "1.7.1.s": [
                "smolensk-vanilla-gui/1.7.1",
                "ftp://10.177.103.10/boxes/box/smolensk-vanilla-gui-1.7.1-gmg9.0.0-virtualbox.box",
            ],
        },
        {
            "1.7.1.v": [
                "voronezh-vanilla-gui/1.7.1",
                "ftp://10.177.103.10/boxes/voronezh-vanilla-gui-1.7.1.json",
            ],
        },
        {
            "1.7.1.o": [
                "orel-vanilla-gui/1.7.1",
                "ftp://10.177.103.10/boxes/box/orel-vanilla-gui-1.7.1-gmg1.0.0-virtualbox.box",
            ],
        },
    ],
}

# Легенда стендов, приклеивавшаяся легаси к СТП-странице (`zefir.py:369-370`).
# Замороженное описание парка железа на момент последнего обновления легаси —
# сознательно не генерируется из `test_stand` (см. `stp_matrix.py`, D2/D3
# решение волны миграции): состав и грейды машин в легаси правились руками и
# нигде не были синхронизированы с реальным `test_stand`.
# Легаси: `allta_app/templates/stand.html`.
STAND_DESCRIPTION_HTML: str = """<br />
<br />
<h1>Описание стендов нагрузочного тестирования</h1>
<table border="1" class="dataframe">
  <thead>
    <tr style="text-align: center;">
      <th>№</th>
      <th>IP</th>
      <th>MAC</th>
      <th>CPU</th>
      <th>RAM</th>
      <th>Storage</th>
      <th>Инв. №</th>
      <th>Грейд</th>
    </tr>
  </thead>
  <tbody>
    <tr style="background-color:#f2f7fb;">
      <td>1</td>
      <td>10.177.103.201</td>
      <td>-</td>
      <td>vCPU (16 cores)</td>
      <td>128Gb</td>
      <td>100Gb</td>
      <td>-</td>
      <td>VM Test WorkStation</td>
    </tr>
    <tr>
      <td>2</td>
      <td>10.177.103.202</td>
      <td>-</td>
      <td>vCPU (16 cores)</td>
      <td>128Gb</td>
      <td>100Gb</td>
      <td>-</td>
      <td>VM Test WorkStation</td>
    </tr>
    <tr style="background-color:#f2f7fb;">
      <td>3</td>
      <td>10.177.103.204</td>
      <td>98:f2:b3:f3:87:14</td>
      <td>Intel(R) Xeon(R) Silver 4110 CPU @ 2.10GHz</td>
      <td>128Gb</td>
      <td>nvme0n1 3.2Tb - системный\\SAS SSD 3.8Tb</td>
      <td>150</td>
      <td>LowServer</td>
    </tr>
    <tr>
      <td>4</td>
      <td>10.177.103.203</td>
      <td>94:57:a5:6a:5e:50</td>
      <td>Intel(R) Xeon(R) CPU E5-2697 v3 @ 2.60GHz</td>
      <td>256Gb</td>
      <td>nvme0n1 3.2Tb - системный\\SAS SSD 3.8Tb</td>
      <td>151</td>
      <td>MiddleServer</td>
    </tr>
    <tr style="background-color:#f2f7fb;">
      <td>5</td>
      <td>10.177.103.205</td>
      <td>52:54:00:d1:24:b7</td>
      <td>Intel(R) Xeon(R) Gold 5320 CPU @ 2.20GHz</td>
      <td>1024Gb</td>
      <td>nvme0n1 3.2Tb - системный\\SAS SSD 3.8Tb</td>
      <td>-</td>
      <td>HighServer</td>
    </tr>
    <tr>
      <td>6</td>
      <td>10.177.103.101</td>
      <td>-</td>
      <td>vCPU (16 cores)</td>
      <td>128Gb</td>
      <td>100Gb</td>
      <td>-</td>
      <td>VM TestStation</td>
    </tr>
    <tr style="background-color:#f2f7fb;">
      <td>7</td>
      <td>10.177.103.102</td>
      <td>-</td>
      <td>vCPU (16 cores)</td>
      <td>128Gb</td>
      <td>100Gb</td>
      <td>-</td>
      <td>VM TestStation</td>
    </tr>
    <tr>
      <td>8</td>
      <td>10.177.103.103</td>
      <td>-</td>
      <td>vCPU (16 cores)</td>
      <td>128Gb</td>
      <td>100Gb</td>
      <td>-</td>
      <td>VM TestStation</td>
    </tr>
    <tr style="background-color:#f2f7fb;">
      <td>9</td>
      <td>10.177.103.104</td>
      <td>-</td>
      <td>vCPU (16 cores)</td>
      <td>128Gb</td>
      <td>100Gb</td>
      <td>-</td>
      <td>VM TestStation</td>
    </tr>
    <tr>
      <td>10</td>
      <td>10.177.103.206</td>
      <td>-</td>
      <td>Intel(R) Xeon(R) Silver 4210 CPU @ 2.2GHz</td>
      <td>128Gb</td>
      <td>nvme0n1 3.2Tb - системный\\SAS SSD 3.8Tb</td>
      <td>-</td>
      <td>LowServer</td>
    </tr>
    <tr style="background-color:#f2f7fb;">
      <td>11</td>
      <td>10.177.103.207</td>
      <td>-</td>
      <td>Intel(R) Xeon(R) Silver 4210 CPU @ 2.2GHz</td>
      <td>128Gb</td>
      <td>nvme0n1 3.2Tb - системный\\SAS SSD 3.8Tb</td>
      <td>-</td>
      <td>LowServer</td>
    </tr>
    <tr>
      <td>12</td>
      <td>10.177.103.208</td>
      <td>-</td>
      <td>Intel(R) Xeon(R) Silver 4210 CPU @ 2.2GHz</td>
      <td>128Gb</td>
      <td>nvme0n1 3.2Tb - системный\\SAS SSD 3.8Tb</td>
      <td>-</td>
      <td>LowServer</td>
    </tr>
    <tr style="background-color:#f2f7fb;">
      <td>13</td>
      <td>10.177.103.209</td>
      <td>-</td>
      <td>Intel(R) Xeon(R) Silver 4210 CPU @ 2.2GHz</td>
      <td>128Gb</td>
      <td>nvme0n1 3.2Tb - системный\\SAS SSD 3.8Tb</td>
      <td>-</td>
      <td>LowServer</td>
    </tr>
    <tr>
      <td>14</td>
      <td>10.177.103.210</td>
      <td>-</td>
      <td>Intel(R) Xeon(R) CPU E5-2697 v3 @ 2.60GHz</td>
      <td>512Gb</td>
      <td>nvme0n1 3.2Tb - системный\\SAS SSD 3.8Tb</td>
      <td>-</td>
      <td>MiddleServer</td>
    </tr>
  </tbody>
</table>
"""
