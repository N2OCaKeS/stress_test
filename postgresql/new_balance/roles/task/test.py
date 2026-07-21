import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from allta import SystemCommands

from new_balance.roles.vm_info import (
    DOMAIN,
    DOMAIN_ADMIN_PASSWORD,
    DOMAIN_ADMIN_USER,
    PASSWORD,
    PGPOOL_CONFIG_PATH,
    PGPOOL_HOSTNAME,
    PGPOOL_IP,
    PGPOOL_PCP_USER,
    POSTGRES_PORT,
    USERNAME,
    VERSION_PG,
    VMS_DATES,
    VMS_GROUPS,
    PROVIDER,
)

_PCP_PASS = "/var/lib/postgresql/.pcppass"
_PCP_BIN  = "/usr/sbin"

# Путь к лог-файлу теста / Path to test log file
_LOG_FILE = Path(__file__).resolve().parents[3] / "test.log"

_SEP_OK    = "*" * 66
_SEP_FATAL = "#" * 66


def _lbdb1(cmd):
    # SSH на lbdb1 и запуск команды от root / SSH to lbdb1 and run command as root
    return f"sshpass -p '1' ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null u@lbdb1 '{cmd}'"


def _pcp(cmd):
    # systemd-run нужен для получения PARSEC_CAP_PRIV_SOCK — без него SSH не может подключиться к сокету pgpool
    # systemd-run is needed to get PARSEC_CAP_PRIV_SOCK — without it SSH cannot connect to pgpool socket
    inner = f"PCPPASSFILE={_PCP_PASS} {_PCP_BIN}/{cmd}"
    return _lbdb1(f'sudo systemd-run --wait --pipe sh -c "{inner}"')


def _pcp_attach(node):
    # pcp_attach_node требует LC_ALL=C / pcp_attach_node needs LC_ALL=C to avoid locale errors
    # systemd-run нужен для PARSEC_CAP_PRIV_SOCK / systemd-run needed for PARSEC_CAP_PRIV_SOCK
    inner = f"LC_ALL=C LANG=C PCPPASSFILE={_PCP_PASS} {_PCP_BIN}/pcp_attach_node -w -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} {node}"
    return _lbdb1(f'sudo systemd-run --wait --pipe sh -c "{inner}"')


def run_pcp(task_name, cmd):
    """Запускает pcp команду, пишет вывод на экран и в лог-файл в стиле задач.
    Run pcp command, print output to screen and log file in task style.
    Возвращает returncode / Returns returncode."""
    now = datetime.now().strftime("%H:%M:%S %d-%m-%Y")
    result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace").strip()
    status = "OK" if result.returncode == 0 else "FATAL"
    sep = _SEP_OK if status == "OK" else _SEP_FATAL

    entry = (
        f"\n\n{sep}\n"
        f"TASK [{task_name}: lbdb1]\n"
        f"[ {now} ]\n"
        f"STATUS [{status}]\n"
        f"COMMAND: {cmd}\n\n"
        f"CONCLUSION: {output}\n"
        f"{sep}\n"
    )

    print(entry)
    with open(_LOG_FILE, "a") as f:
        f.write(entry)

    return result.returncode


def check_pcp():
    """Проверяет доступность PCP по нескольким адресам перед стартом теста.
    Checks PCP availability on multiple addresses before starting the test.
    Завершает процесс если PCP недоступен / Exits if PCP is unavailable."""

    lbdb1_ip = VMS_DATES.get("lbdb1", {}).get("ip_bridge", "")

    # Список адресов для проверки / List of addresses to check
    hosts = [
        ("domain",    PGPOOL_HOSTNAME),
        ("vip",       PGPOOL_IP),
        ("127.0.0.1", "127.0.0.1"),
    ]
    # Добавляем IP lbdb1 если известен / Add lbdb1 IP if known
    if lbdb1_ip:
        hosts.insert(2, ("lbdb1 ip", lbdb1_ip))

    for label, host in hosts:
        rc = run_pcp(
            f"pcp check [{label}] {host}",
            _pcp(f"pcp_node_count -w -h {host} -U {PGPOOL_PCP_USER}")
        )
        if rc == 0:
            # Первая успешная проверка — дальше не идём / First successful check — stop here
            return

    msg = "FATAL: PCP недоступен ни по одному адресу. Тест остановлен. / PCP unavailable on all addresses. Test aborted."
    print(msg)
    with open(_LOG_FILE, "a") as f:
        f.write(f"\n{_SEP_FATAL}\n{msg}\n{_SEP_FATAL}\n")
    sys.exit(1)


def check_db():
    """Проверяет доступность БД через балансировщик pgpool.
    Checks DB availability through pgpool load balancer.
    Завершает процесс если БД недоступна / Exits if DB is unavailable."""
    rc = run_pcp(
        f"db check via balancer [{PGPOOL_HOSTNAME}:{POSTGRES_PORT}]",
        _lbdb1(f'sudo -u postgres psql -h {PGPOOL_HOSTNAME} -p {POSTGRES_PORT} -d test -c "SELECT 1;"')
    )
    if rc != 0:
        msg = "FATAL: БД недоступна через балансировщик. Тест остановлен. / DB unavailable through load balancer. Test aborted."
        print(msg)
        with open(_LOG_FILE, "a") as f:
            f.write(f"\n{_SEP_FATAL}\n{msg}\n{_SEP_FATAL}\n")
        sys.exit(1)


class Test:
    def __init__(self):
        self.provider = PROVIDER
        self._task_dir = Path(__file__).resolve().parent
        self._results_dir = Path("/home/u")

    def test(self):
        provider = self.provider

        # Ждём стабилизации pgpool после настройки / Wait for pgpool to stabilize after setup
        print("Waiting 7m for pgpool to stabilize...")
        for i in range(7):
            print(f"{i+1}/7 minutes...")
            time.sleep(60)

        # Проверка доступности PCP перед стартом / Check PCP availability before start
        check_pcp()
        # Проверка доступности БД через балансировщик / Check DB availability through load balancer
        check_db()

        # Копируем clients.py на database3 / Copy clients.py to database3
        provider.scp(
            scp_settings={
                "database3": {
                    "mode": "push",
                    "path_host": str(self._task_dir / "template" / "clients.py"),
                    "path_vm": "/tmp/clients.py",
                }
            },
            vms_dates=VMS_DATES,
            username=USERNAME,
            password=PASSWORD,
        )

        # Фаза 1: запуск теста в фоне / Phase 1: start test in background
        provider.execute(
            commands={
                "database3": {
                    "start test": {
                        "command": "sudo chmod 777 /tmp/clients.py && sudo systemd-run --no-block --unit=clients-test --working-directory=/home/u python3 /tmp/clients.py",
                        "signal set": "",
                        "signal get": "",
                    },
                }
            },
            vms_dates=VMS_DATES,
            vms_groups=VMS_GROUPS,
            username=USERNAME,
            password=PASSWORD,
        )

        # Фаза 2: отключаем auto_failback и веса db1, db2 / Phase 2: disable auto_failback and weights db1, db2
        provider.execute(
            commands={
                "g_load_balancer": {
                    "disable autofailback": {
                        "command": f'sudo sed -i "s@auto_failback = on@auto_failback = off@g" {PGPOOL_CONFIG_PATH}',
                        "signal set": "disable auto failback",
                        "signal get": "",
                    },
                    "disable load balancing db1": {
                        "command": f'sudo sed -i "s@backend_weight1 = 1@backend_weight1 = 0@g" {PGPOOL_CONFIG_PATH}',
                        "signal set": "disable load balancing db1",
                        "signal get": ["disable auto failback"],
                    },
                    "disable load balancing db2": {
                        "command": f'sudo sed -i "s@backend_weight2 = 1@backend_weight2 = 0@g" {PGPOOL_CONFIG_PATH}',
                        "signal set": "disable load balancing db2",
                        "signal get": ["disable load balancing db1"],
                    },
                }
            },
            vms_dates=VMS_DATES,
            vms_groups=VMS_GROUPS,
            username=USERNAME,
            password=PASSWORD,
        )

        # Фаза 3: pcp reload + detach db1, db2 / Phase 3: pcp reload + detach db1, db2
        run_pcp("reload conf 1", _pcp(f"pcp_reload_config -w -h {PGPOOL_HOSTNAME} -U {PGPOOL_PCP_USER} --scope=cluster"))
        run_pcp("detach db1",    _pcp(f"pcp_detach_node -w -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} 1"))
        run_pcp("detach db2",    _pcp(f"pcp_detach_node -w -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} 2"))

        # Фаза 4: stop/update/start реплик / Phase 4: stop/update/start replicas
        provider.execute(
            commands={
                "g_replica": {
                    "stop db": {
                        "command": f"sudo systemctl stop postgresql@{VERSION_PG}-contrprimer",
                        "signal set": "standby stop",
                        "signal get": "",
                    },
                    "update postgres": {
                        "command": "sudo apt-get reinstall postgresql -y",
                        "signal set": "standby reinstall",
                        "signal get": ["standby stop"],
                    },
                    "start db": {
                        "command": f"sudo systemctl start postgresql@{VERSION_PG}-contrprimer",
                        "signal set": "standby start",
                        "signal get": ["standby reinstall"],
                    },
                }
            },
            vms_dates=VMS_DATES,
            vms_groups=VMS_GROUPS,
            username=USERNAME,
            password=PASSWORD,
        )

        # Фаза 5: включаем веса db1, db2 / Phase 5: enable weights db1, db2
        provider.execute(
            commands={
                "g_load_balancer": {
                    "enable load balancing db1": {
                        "command": f'sudo sed -i "s@backend_weight1 = 0@backend_weight1 = 1@g" {PGPOOL_CONFIG_PATH}',
                        "signal set": "enable load balancing db1",
                        "signal get": "",
                    },
                    "enable load balancing db2": {
                        "command": f'sudo sed -i "s@backend_weight2 = 0@backend_weight2 = 1@g" {PGPOOL_CONFIG_PATH}',
                        "signal set": "enable load balancing db2",
                        "signal get": ["enable load balancing db1"],
                    },
                }
            },
            vms_dates=VMS_DATES,
            vms_groups=VMS_GROUPS,
            username=USERNAME,
            password=PASSWORD,
        )

        # Фаза 6: pcp reload + attach db1,db2 + promote + ожидание / Phase 6: pcp reload + attach db1,db2 + promote + wait
        run_pcp("reload conf 2",      _pcp(f"pcp_reload_config -w -h {PGPOOL_HOSTNAME} -U {PGPOOL_PCP_USER} --scope=cluster"))
        run_pcp("attach db1",         _pcp_attach(1))
        run_pcp("attach db2",         _pcp_attach(2))
        run_pcp("promote new master", _pcp(f"pcp_promote_node -w -v --switchover -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} 1"))
        time.sleep(20)

        # Фаза 7: отключаем вес db0 / Phase 7: disable weight db0
        provider.execute(
            commands={
                "g_load_balancer": {
                    "disable load balancing db0": {
                        "command": f'sudo sed -i "s@backend_weight0 = 1@backend_weight0 = 0@g" {PGPOOL_CONFIG_PATH}',
                        "signal set": "disable load balancing db0",
                        "signal get": "",
                    },
                }
            },
            vms_dates=VMS_DATES,
            vms_groups=VMS_GROUPS,
            username=USERNAME,
            password=PASSWORD,
        )

        # Фаза 8: pcp reload + detach db0 / Phase 8: pcp reload + detach db0
        run_pcp("reload conf 3", _pcp(f"pcp_reload_config -w -h {PGPOOL_HOSTNAME} -U {PGPOOL_PCP_USER} --scope=cluster"))
        run_pcp("detach db0",    _pcp(f"pcp_detach_node -w -U {PGPOOL_PCP_USER} -h {PGPOOL_HOSTNAME} 0"))

        # Фаза 9: stop/update/start database1 / Phase 9: stop/update/start database1
        provider.execute(
            commands={
                "database1": {
                    "stop db": {
                        "command": f"sudo systemctl stop postgresql@{VERSION_PG}-contrprimer",
                        "signal set": "old master stop",
                        "signal get": "",
                    },
                    "update postgres": {
                        "command": "sudo apt-get reinstall postgresql -y",
                        "signal set": "old master reinstall",
                        "signal get": ["old master stop"],
                    },
                    "start db": {
                        "command": f"sudo systemctl start postgresql@{VERSION_PG}-contrprimer",
                        "signal set": "old master start",
                        "signal get": ["old master reinstall"],
                    },
                }
            },
            vms_dates=VMS_DATES,
            vms_groups=VMS_GROUPS,
            username=USERNAME,
            password=PASSWORD,
        )

        # Фаза 10: включаем вес db0 + autofailback / Phase 10: enable weight db0 + autofailback
        provider.execute(
            commands={
                "g_load_balancer": {
                    "enable load balancing db0": {
                        "command": f'sudo sed -i "s@backend_weight0 = 0@backend_weight0 = 1@g" {PGPOOL_CONFIG_PATH}',
                        "signal set": "enable load balancing db0",
                        "signal get": "",
                    },
                    "enable autofailback": {
                        "command": f'sudo sed -i "s@auto_failback = off@auto_failback = on@g" {PGPOOL_CONFIG_PATH}',
                        "signal set": "enable auto failback",
                        "signal get": ["enable load balancing db0"],
                    },
                }
            },
            vms_dates=VMS_DATES,
            vms_groups=VMS_GROUPS,
            username=USERNAME,
            password=PASSWORD,
        )

        # Фаза 11: pcp attach db0 + итоговый reload / Phase 11: pcp attach db0 + final reload
        run_pcp("attach db0",    _pcp_attach(0))
        run_pcp("reload conf 4", _pcp(f"pcp_reload_config -w -h {PGPOOL_HOSTNAME} -U {PGPOOL_PCP_USER} --scope=cluster"))

        # Ждём завершения clients-test.service перед сбором результатов / Wait for clients-test.service to finish before collecting results
        provider.execute(
            commands={
                "database3": {
                    "wait clients": {
                        "command": "while sudo systemctl is-active clients-test.service --quiet 2>/dev/null; do sleep 5; done",
                        "signal set": "",
                        "signal get": "",
                    },
                }
            },
            vms_dates=VMS_DATES,
            vms_groups=VMS_GROUPS,
            username=USERNAME,
            password=PASSWORD,
        )

        # Собираем результаты / Collect results
        provider.scp(
            scp_settings={
                "database3": [
                    {
                        "mode": "pull",
                        "path_host": "results_balance.txt",
                        "path_vm": str(self._results_dir / "results_balance.txt"),
                    },
                    {
                        "mode": "pull",
                        "path_host": "available_packages.txt",
                        "path_vm": str(self._results_dir / "available_packages.txt"),
                    },
                    {
                        "mode": "pull",
                        "path_host": "psb_info.txt",
                        "path_vm": str(self._results_dir / "psb_info.txt"),
                    },
                ]
            },
            vms_dates=VMS_DATES,
            username=USERNAME,
            password=PASSWORD,
        )


class InfoSysLoadTest:
    """Нагрузочный тест info-sys: сценарий 1 из roles/task/test.md — чистая нагрузка
    (без привязки к МРД-уровню конкретной строки). Гоняет mrd_load_generator.py с
    ВМ `loader` на `web1` (Apache AstraMode + Flask protopack) по пути `/` на
    уровне МРД 1, забирает JSON со статистикой (RPS/latency), 5 шагов от 500 до
    2500 запросов (MAX_ITERATIONS в mrd_load_generator.py = 5).

    """

    LEVELS = (1,)
    DOMAIN_USER = "user_level3"
    LEVEL3_PASSWORD = "Level3TestMac2026!"
    WORKERS = 40
    N_USERS = WORKERS
    R_START, R_END, R_STEP = 500, 2500, 500
    CCACHE_PREFIX = "/tmp/mrd_load_ccache"

    def __init__(self):
        self.provider = PROVIDER
        self._results_dir = Path("/home/u")

    def run(self):
        """Прогоняет нагрузку на каждом уровне из LEVELS, пишет JSON per-level
        в текущую директорию хоста (mrd_load_level{N}_results.json)."""
        provider = self.provider
        web1_ip  = VMS_DATES["web1"]["ip_bridge"]
        hostname = f"web1.{DOMAIN}"

        provider.scp(
            scp_settings={
                "loader": [
                    {
                        "mode": "push",
                        "path_host": "./new_balance/roles/web/testing/mrd_load_generator.py",
                        "path_vm": "/tmp/mrd_load_generator.py",
                    }
                ]
            },
            vms_dates=VMS_DATES,
            username=USERNAME,
            password=PASSWORD,
        )

        users   = [f"{self.DOMAIN_USER}_{i}" for i in range(self.N_USERS)]
        ccaches = [f"{self.CCACHE_PREFIX}_{i}" for i in range(self.N_USERS)]

        create_users_cmd = f"yes {DOMAIN_ADMIN_PASSWORD} | kinit {DOMAIN_ADMIN_USER}"
        for u in users:
            create_users_cmd += (
                f" && (ipa user-show {u} > /dev/null 2>&1 || "
                f'yes {self.LEVEL3_PASSWORD}| ipa user-add {u} '
                f'--first={u} --last={u} --macmin=0 --macmax=3 --miclevel=63 --password '
                f'--password-expiration="2099-12-31Z")'
            )

        kinit_cmd = " && ".join(
            f"yes {self.LEVEL3_PASSWORD} | sudo kinit -c FILE:{cc} {u}"
            for u, cc in zip(users, ccaches)
        )

        provider.execute(
            commands={
                "loader": {
                    "install load generator deps": {
                        "command": "sudo apt-get install -y libpdp-dev",
                        "signal set": "",
                        "signal get": "",
                    },
                    "create level3 users": {
                        "command": create_users_cmd,
                        "signal set": "level3 users created",
                        "signal get": "",
                    },
                    "kinit users": {
                        "command": kinit_cmd,
                        "signal set": "",
                        "signal get": ["level3 users created"],
                    },
                }
            },
            vms_dates=VMS_DATES,
            vms_groups=VMS_GROUPS,
            username=USERNAME,
            password=PASSWORD,
        )

        ccache_list_arg = ",".join(ccaches)

        for level in self.LEVELS:
            out_dir = f"/home/u/mrd_load_level{level}"
            provider.execute(
                commands={
                    "loader": {
                        f"run load level {level}": {
                            "command": (
                                f"sudo execaps -c 0x804 -- python3 /tmp/mrd_load_generator.py "
                                f"-H {web1_ip} -n {hostname} -u / -l {level} -w {self.WORKERS} "
                                f"--r-start {self.R_START} --r-end {self.R_END} --r-step {self.R_STEP} "
                                f"--ccache-list {ccache_list_arg} "
                                f"--output-dir {out_dir}"
                            ),
                            "signal set": "",
                            "signal get": "",
                        },
                    }
                },
                vms_dates=VMS_DATES,
                vms_groups=VMS_GROUPS,
                username=USERNAME,
                password=PASSWORD,
            )

            provider.scp(
                scp_settings={
                    "loader": [
                        {
                            "mode": "pull",
                            "path_host": f"mrd_load_level{level}_results.json",
                            "path_vm": f"{out_dir}/results.json",
                        }
                    ]
                },
                vms_dates=VMS_DATES,
                username=USERNAME,
                password=PASSWORD,
            )

        provider.execute(
            commands={
                "web1": {
                    "write astra version": {
                        "command": (
                            "echo \"$(cat /etc/astra_version)"
                            "($(grep -oE 'orel|smolensk|voronezh' /etc/astra_license 2>/dev/null | head -1))\" "
                            "| sudo tee /home/u/psb_info.txt > /dev/null"
                        ),
                        "signal set": "psb_info av",
                        "signal get": "",
                    },
                    "write kernel version": {
                        "command": "uname -r | sudo tee -a /home/u/psb_info.txt > /dev/null",
                        "signal set": "psb_info kernel",
                        "signal get": ["psb_info av"],
                    },
                    "write apache2 version": {
                        "command": (
                            "dpkg-query -W -f='${Version}\\n' apache2 "
                            "| sudo tee -a /home/u/psb_info.txt > /dev/null"
                        ),
                        "signal set": "psb_info apache",
                        "signal get": ["psb_info kernel"],
                    },
                    "fix psb_info owner": {
                        "command": "sudo chown u:u /home/u/psb_info.txt",
                        "signal set": "",
                        "signal get": ["psb_info apache"],
                    },
                }
            },
            vms_dates=VMS_DATES,
            vms_groups=VMS_GROUPS,
            username=USERNAME,
            password=PASSWORD,
        )

        provider.scp(
            scp_settings={
                "web1": [
                    {
                        "mode": "pull",
                        "path_host": "psb_info.txt",
                        "path_vm": str(self._results_dir / "psb_info.txt"),
                    }
                ]
            },
            vms_dates=VMS_DATES,
            username=USERNAME,
            password=PASSWORD,
        )
