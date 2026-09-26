"""Порт построения легаси-`dates` и команд запуска allta_app.

Эталон для golden-тестов паритета (`tests/test_legacy_golden_parity.py`).
Модули `emm/allta_app_full` не импортируются: `allta_image_conf.py` ходит в
сеть при импорте, `backup_image.py` разбирает `sys.argv` и сразу работает со
стендом. Вместо этого:

* словари (`TEST_FLAGS`, `AUDITD_FLAGS`, `tests`, `branches`, `tests_list`,
  `stands_ip`, `VENV_PATH`) читаются из исходников как литералы (AST);
* argparse `backup_image.py:34-261` собирается из его же `add_argument(...)`
  (`scripts/check_branch_argparse.parser_from_source`);
* код, который строит строку, перенесён **дословно** — функции
  `build_command_args` (`allta_back.py:154-191`), `parent_page_list`
  (`allta_image_conf.py:122-127`) и тело `_run_backup_image` (сегменты
  `backup_image.py` с номерами строк). Внешние действия (SSH, SFTP, запись
  файлов, IPMI, статусы) подменены записью в `LegacyRun`: так строки
  `dates`, команды на стенде и на хосте ALLTA получаются ровно те, что
  собрал бы легаси. Дословность проверяет
  `test_legacy_golden_parity.py::TestPortIsVerbatim` — сравнением AST
  порта и легаси-исходника.

Чистые функции для тестов: `legacy_dates` (строка `dates`, как её пишет
`backup_image.py:426-427`), `legacy_dates_written` (все строки `dates`, с
которыми запускался конечный скрипт: у `psql/tantor kernels` их четыре —
`db_kernel_changer`) и `legacy_launch_commands` (команды на стенде и на хосте
ALLTA по порядку).
"""

from __future__ import annotations

import argparse
import ast
import shlex
import warnings
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from scripts.check_branch_argparse import parser_from_source

LEGACY_DIR = Path(__file__).resolve().parents[3] / "allta_app_full"
BACKUP_IMAGE = LEGACY_DIR / "backup_image.py"
ALLTA_BACK = LEGACY_DIR / "allta_back.py"
IMAGE_CONF = LEGACY_DIR / "allta_image_conf.py"


# ── исходники легаси ─────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def legacy_ast(path: Path) -> ast.Module:
    with warnings.catch_warnings():
        # `'\('` в sed-выражениях `backup_image.py:716,897`.
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@lru_cache(maxsize=None)
def _literals(path: Path) -> dict[str, Any]:
    """Верхнеуровневые `NAME = <литерал>` (и `NAME: T = <литерал>`) модуля."""
    values: dict[str, Any] = {}
    for node in legacy_ast(path).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            name, value = node.target.id, node.value
        else:
            continue
        try:
            values[name] = ast.literal_eval(value)
        except ValueError:
            continue
    return values


TEST_FLAGS: dict[str, str] = _literals(ALLTA_BACK)["TEST_FLAGS"]          # allta_back.py:26-106
AUDITD_FLAGS: dict[str, str] = _literals(ALLTA_BACK)["AUDITD_FLAGS"]      # allta_back.py:109-113
tests: dict[str, str] = _literals(IMAGE_CONF)["tests"]                    # allta_image_conf.py:237-305
branches: dict[str, str] = _literals(IMAGE_CONF)["branches"]              # allta_image_conf.py:158-222
tests_list: dict[str, list[str]] = _literals(IMAGE_CONF)["tests_list"]    # allta_image_conf.py:109-119
stands_ip: dict[str, str] = _literals(IMAGE_CONF)["stands_ip"]            # allta_image_conf.py:34-49
VENV_PATH: str = _literals(IMAGE_CONF)["VENV_PATH"]                       # allta_image_conf.py:16


def short_name_of(tcase: str) -> str:
    """`args.TEST` легаси: `tests[<имя тест-кейса>]` (`allta_back.py:495`)."""
    return tests[tcase]


def legacy_parent_page(release: str, test: str) -> str:
    """`parent_page_list()[release][test]` (`allta_back.py:524,541`) для одной версии.

    `get_allta_conf()['release_version']` — список версий из
    `allta_conf.json`; здесь он из одной версии, остальное — дословно.
    """
    def get_allta_conf():
        return {"release_version": [release]}

    # ── allta_image_conf.py:122-127 ──
    def parent_page_list():
        parent_page_list = {
            key:{value:f'STRESS_report {key} ⬝ {topic}' for topic in tests_list for value in tests_list[topic]}
                            for key in get_allta_conf()['release_version']
                            }
        return parent_page_list

    return parent_page_list()[release][test]


# ── allta_back.py:154-191 ────────────────────────────────────────────────────

def build_command_args(test_name: str,
                       stand: str,
                       release: str,
                       kernel: str,
                       mode: str,
                       tcas: str,
                       branch: str,
                       cti: str,
                       pp: str,
                       testnum: str,
                       tes: str,
                   ) -> str:
    """
    Формирует строку аргументов для backup_image.py
    """
    sn = f'-sn {str(stand).replace("stand", "").lstrip()}'
    rs = f'-rs {release}'
    test_arg = f'-test "{test_name}"'
    mode_arg = f'-mode {mode}'
    kn_arg = f'-kn {kernel}'
    stand_arg = f'-stand {stand}'
    tcyc = f'-tcyc {release}_{mode}_{kernel}_{stand}'
    tcas_arg = f'-tcas "{tcas}"'
    branch_arg = f'-branch {branch}'
    cti_arg = f'-cti {cti}'
    pp_arg = f'-pp "{pp}"'
    testnum_arg = f'-testnum {testnum}'
    tes_arg = f'-tes {tes}'

    base_args = f'{sn} {rs} {test_arg} {mode_arg} {kn_arg} {stand_arg} {tcyc} {tcas_arg} {branch_arg} {cti_arg} {pp_arg} {testnum_arg} {tes_arg}'

    # Специальные флаги
    if test_name in AUDITD_FLAGS:
        return f'{AUDITD_FLAGS[test_name]} {base_args}'
    extra_flags = TEST_FLAGS.get(test_name, '')
    if extra_flags:
        return f'{extra_flags} {base_args}'
    return base_args


@lru_cache(maxsize=None)
def _backup_image_parser():
    parsed = parser_from_source(BACKUP_IMAGE)
    assert not parsed.warnings, parsed.warnings
    return parsed


def backup_image_args(cmd_args: str) -> argparse.Namespace:
    """`./backup_image.py {cmd_args}` через `shell=True` (`allta_back.py:243-244`)."""
    return _backup_image_parser().parse(shlex.split(cmd_args))


# ── backup_image.py с записью внешних действий ───────────────────────────────

@dataclass(frozen=True)
class LegacyCredentials:
    """`tokens` легаси (`/home/u/tokens.json`, `allta_image_conf.py`)."""

    username: str
    conf_token: str
    jira_token: str
    git_token: str = "git-token"
    srv_pass: str = "srv-pass"

    def tokens(self) -> dict[str, str]:
        return {
            "conf_token": self.conf_token, "username": self.username, "jira_token": self.jira_token,
            "git_token": self.git_token, "srv_pass": self.srv_pass,
        }


@dataclass(frozen=True)
class LegacyEvent:
    """Команда легаси: `stand` — по SSH на стенде, `host` — на хосте ALLTA."""

    where: str
    command: str
    files: dict[str, str]


@dataclass
class LegacyRun:
    dates: str = ""
    dates_name: str = ""
    events: list[LegacyEvent] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    dates_seen: list[str] = field(default_factory=list)

    def stand(self, command: str) -> None:
        self.events.append(LegacyEvent("stand", command, dict(self.files)))

    def host(self, command: str) -> None:
        self.events.append(LegacyEvent("host", command, dict(self.files)))

    def open(self, path: str, mode: str = "r"):
        run = self

        class _File:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def write(self, text: str) -> None:
                run.files[path] = text

        assert mode == "w", mode
        return _File()


def _noop(*_args, **_kwargs) -> None:
    return None


def _run_backup_image(args: argparse.Namespace, tokens: dict[str, str]) -> LegacyRun:
    """`backup_image.py` после разбора аргументов, до и во время `remote_test_run()`.

    Подготовка стенда (restore образа, ядро, режим — `TestRunProvision`) и
    проверки `read_status()`/`available_astra_services_checker()`
    (`backup_image.py:973-983,1044-1049`) не портированы: на строку `dates` и
    команды они не влияют, считается, что подготовка прошла.
    """
    run = LegacyRun()
    open = run.open                     # noqa: A001 — файлы allta_app пишутся в память
    send_remote_command = run.stand     # backup_image.py:734 — SSH на стенд
    comm_and_log = run.host             # libs/liballta.py:177 — shell на хосте ALLTA
    create_remote_file = _noop          # backup_image.py:722 — SFTP (снимок файлов — в событии)
    write_status = _noop                # backup_image.py:486
    socket_available = _noop            # backup_image.py:554
    backup_vm_snapshot = _noop          # libs/liballta.py
    run_provision = SimpleNamespace(provision=_noop)

    def GrubCommand():                  # backup_image.py:453 — SSH на стенд
        return SimpleNamespace(ex_command=run.stand)

    # ── backup_image.py:267-279 (`args = parser.parse_args()` — параметр) ──
    __conf_token = tokens['conf_token']
    __username = tokens['username']
    __jira_token = tokens['jira_token']
    __git_token = tokens['git_token']
    __srv_pass = tokens['srv_pass']
    success = f'Success {args.STAND} {args.TEST}'
    in_prog = f'In progress {args.STAND} {args.TEST}'
    fail = f'Fail {args.STAND} {args.TEST}'
    done = f'Done {args.STAND} {args.TEST}'
    stand_ip = stands_ip[args.STAND]
    user = 'u'
    password = __srv_pass
    port = 22

    # ── backup_image.py:288-298 (280-287 — IPMI и Clonezilla, на dates не влияют) ──
    branch = args.BRANCH
    parent_page = args.PARP
    systems = ['debian10', 'debian10-5.15', 'altlinux-5.10']
    dates_name = f'dates_{args.STAND}.conf'
    testenv_status = f'testenv_{args.STAND}.conf'
    username = f'--username {__username}'
    token = f'--token {__conf_token}'
    confluence_space = "--confluence-space 'DEVQA'"
    confluence_parent_page = f'--confluence-parent-page "{parent_page}"'
    confluence_new_page = f'--confluence-new-page "{args.TEST}_{args.RELEASE}_{args.MODE}_{args.KERNEL}_{args.STAND}"'
    gitclone_conf_body = "git clone -c http.extraHeader='Authorization: {}' https://git.astralinux.ru/scm/qa/stress_test.git"

    # ── backup_image.py:299-331 ──
    if args.TEST == 'EXT4 parsec' or args.TEST == 'XFS parsec':
        fs = f'-fs {args.TEST.split()[0].lower()}'
    else:
        fs = f'-fs {args.TEST.lower().split()[0]}'
    sn = f'-sn {args.ST}'
    fti = f'-fti {args.CTI}'
    tcyc = f'-tcyc {args.TCYCLE}'
    tcas = f'-tcas "{args.TCASE}"'
    ba = f'-ba "{__jira_token}"'
    tcv = f'-tcv {args.RELEASE}'
    balance_vbox = f"-vbox {args.TCYCLE.split('_')[0]}"
    pack_sql = '--package postgresql-11'
    psql_version = '--package postgresql-'
    tantor_pkg = '--package tantor-se-server-15'
    testlist = f'--testlist {args.AUDIT}'
    psql_aud_off = '-psql_aud off'
    psql_parsec = '-parsec parsec'
    psql_vanilla = '-psql_van pv'
    tantor_vanilla = '-tantor_van tv'
    lvirt_test = f'-testname {args.LVIRT}'
    network_test = f'-testname {args.NETWORK}'
    kernel_test = f'-testname {args.KERNELTEST}'
    astraevents_test = f'-testname {args.ASTRAEVENTS}'
    apache_test = f'-testname {args.APACHE}'
    ovf = f'-ovf {args.OVF}'
    ovf_ram_dates = f'{username} {token} {fti} {tcyc} {tcas} {ba} {tcv} -check drop'
    ovf_sd_dates = f'{username} {token} {fti} {tcyc} {tcas} {ba} {tcv} -check reboot'
    freeipa_test = f'-tt {args.FREEIPA}'
    vpn = f'--test {args.VPN}'
    mail = f'-tt {args.MAIL}'
    psql_olap = f'-olap {args.PSQL_OLAP}'
    psql_info_sys = f'-tt {args.PSQL_BALANCE}'

    # ── backup_image.py:333-424 — все ветки if/elif ──
    if args.PSQL:
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              -db {sn} {fti} {tcyc} {tcas} {ba} {tcv} -c {pack_sql}'
    elif args.PSQL_VANILLA:
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              -db {sn} {fti} {tcyc} {tcas} {ba} {tcv} -c {pack_sql} {psql_vanilla}'
    elif args.TANTOR_VANILLA:
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              -db {sn} {fti} {tcyc} {tcas} {ba} {tcv} -c {tantor_pkg} {tantor_vanilla}'
    elif args.PSQL_BALANCE in ("info-sys", "info-sys-orel"):
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv} {balance_vbox} {psql_info_sys}'
    elif args.PSQL_BALANCE or args.PSQL_OOM:
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv} {balance_vbox}'
    elif args.PSQL_PARSEC:
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              -db {sn} {fti} {tcyc} {tcas} {ba} {tcv} -c {pack_sql} {psql_parsec}'
    elif args.AUDIT_OFF:
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              -db {sn} {fti} {tcyc} {tcas} {ba} {tcv} -c {pack_sql} {psql_aud_off}'
    elif args.OVF:
        dates = f'{username} {token} {fti} {tcyc} {tcas} {ba} {tcv} {ovf}'
    elif args.AUDIT:
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {testlist} {fti} {tcyc} {tcas} {ba} {tcv}'
    elif args.TEST == 'EXT4 parsec' or args.TEST == 'XFS parsec':
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {fs} {sn} {fti} {tcyc} {tcas} {ba} {tcv} --parsec'
    elif args.TEST == 'OCFS2':
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {fs} {sn} {fti} {tcyc} {tcas} {ba} {tcv} -vbox {args.RELEASE} -kernel {args.KERNEL} --libvirt'
    elif args.TEST == 'CEPH':
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {fs} {sn} {fti} {tcyc} {tcas} {ba} {tcv} -vbox {args.RELEASE} -kernel {args.KERNEL}'
    elif args.TEST == 'CEPH fio':
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {fs} {sn} {fti} {tcyc} {tcas} {ba} {tcv} -vbox {args.RELEASE} -kernel {args.KERNEL} --test-set fio'
    elif args.TEST == 'CEPH parsec':
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {fs} {sn} {fti} {tcyc} {tcas} {ba} {tcv} -vbox {args.RELEASE} -kernel {args.KERNEL} --parsec'
    elif args.TEST == 'syslog-ng-cwl':
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv} -cwl'
    elif args.TEST == 'syslog-ng' or args.TEST == 'unix':
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv}'
    elif args.TEST == 'unix parsec':
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv} -p parsec'
    elif args.FREEIPA:
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv} {freeipa_test}'
    elif args.PARSEC_IMPACT or args.PARSEC_IMPACT_AO:
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv}'
    elif args.TEST == 'digsig-cdt':
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv} -ds'
    elif args.TEST == 'raw-spin-lock':
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv} -rsl'
    elif args.TEST == 'docker-wa':
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv} -wa'
    elif args.APACHE:
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv} {apache_test}'
    elif args.LVIRT:
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv} {balance_vbox} {lvirt_test}'
    elif args.VPN:
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv} {vpn}'
    elif args.MAIL:
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv} {mail}'
    elif args.NETWORK:
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv} {balance_vbox} {network_test}'
    elif args.KERNELTEST:
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv} {balance_vbox} {kernel_test}'
    elif args.ASTRAEVENTS:
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv} {balance_vbox} {astraevents_test}'
    elif args.PSQL_OLAP:
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              -db {sn} {fti} {tcyc} {tcas} {ba} {tcv} -c {pack_sql} {psql_olap}'
    else:
        dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {fs} {sn} {fti} {tcyc} {tcas} {ba} {tcv}'

    # ── backup_image.py:426-430 ──
    with open(f'/home/u/git/stress_test/allta_app/{dates_name}', 'w') as w:
        w.write(dates)

    with open(f'/home/u/git/stress_test/allta_app/{testenv_status}', 'w') as w:
        w.write(args.TESTENV)

    # ── backup_image.py:895-939 ──
    def db_kernel_changer(cpu_count, database, position=None):
        grub = GrubCommand()
        set_count = f'''sudo sed -i 's/\\(GRUB_CMDLINE_LINUX_DEFAULT=.*\\)"/\\1 maxcpus={cpu_count}"/' /etc/default/grub'''
        update = 'sudo update-grub'
        test_args = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
                   {sn} {fti} {tcyc} {tcas} {ba} {tcv} -q {cpu_count}'
        begin_args = test_args + ' -sf begin'
        end_args = test_args + ' -sf end'

        if position == 'begin':
            if database == 'tantor':
                dates = begin_args + f' {tantor_pkg} -db tantor'
            elif database == 'psql':
                dates = begin_args + f' {psql_version}'
        elif position == 'end':
            if database == 'tantor':
                dates = end_args + f' {tantor_pkg} -db tantor'
            elif database == 'psql':
                dates = end_args + f' {psql_version}'
        else:
            if database == 'tantor':
                dates = test_args + f' {tantor_pkg} -db tantor'
            elif database == 'psql':
                dates = test_args + f' {psql_version}'

        with open(f'/home/u/git/stress_test/allta_app/{dates_name}', 'w') as w:
            w.write(dates)

        grub.ex_command(set_count)
        grub.ex_command(update)
        comm_and_log('sshpass -v -p ' + password + ' ssh -o StrictHostKeyChecking=no \
                -o UserKnownHostsFile=/dev/null u@' + stand_ip + ' sudo reboot')
        socket_available()
        create_remote_file(f'/home/u/git/stress_test/allta_app/{dates_name}', f'/home/u/{dates_name}')
        create_remote_file(f'/home/u/git/stress_test/allta_app/{testenv_status}', f'/home/u/{testenv_status}')

        if position == 'begin':
            create_remote_file('/home/u/git/stress_test/allta_app/starter.sh', '/home/u/starter.sh')
            send_remote_command(f'sudo bash /home/u/starter.sh {branch} "{__git_token}" {dates_name} {args.RELEASE} kernel')
        else:
            if database == 'tantor':
                send_remote_command('sudo systemctl restart tantor-se-server-15.service')
                send_remote_command(f'cd /home/u/git/stress_test/{branch}/ && sudo {VENV_PATH} run.py -n {dates_name} -kn kernel')
            else:
                send_remote_command(f'cd /home/u/git/stress_test/{branch}/ && sudo {VENV_PATH} run.py -n {dates_name} -kn kernel')

        write_status(done)

    # ── backup_image.py:943-964 ──
    def freeipa_authentication_test():
        git_path = '/home/u/freeipa_test/gitipa'
        all_path = '/home/u/freeipa_test/gitipa/stress_test/freeipa'
        clients_ip = '10.177.103.201'
        kernel = '5.15.0-83-generic'

        backup_vm_snapshot('stand1', '1.7.5.9')
        write_status(success)
        run_provision.bootorder = False
        run_provision.clonezilla = False
        run_provision.stand_ip = clients_ip
        run_provision.kernel = kernel
        run_provision.modes = False
        run_provision.ipmi = False
        run_provision.provision()

        comm_and_log(f'cd {git_path} && {VENV_PATH} git_clone.py')
        comm_and_log(f'cd {git_path}/stress_test && git checkout freeipa')
        comm_and_log(f'cd {all_path} && {VENV_PATH} ipa_run.py {dates}')

        write_status(done)

    # ── backup_image.py:997-1041 ──
    def remote_test_run():
        if args.DB_KERNELS == 'psql' or args.DB_KERNELS == 'tantor':
            db_kernel_changer(8, args.DB_KERNELS, position='begin')
            db_kernel_changer(16, args.DB_KERNELS)
            db_kernel_changer(24, args.DB_KERNELS)
            db_kernel_changer(32, args.DB_KERNELS, position='end')
        elif args.PSQL_BALANCE:
            send_remote_command(f'sudo bash /home/u/starter.sh {branch} "{__git_token}" {dates_name} {args.RELEASE} balance') #{balance_host_release}')
        elif args.PSQL_OOM:
            send_remote_command(f'sudo bash /home/u/starter.sh {branch} "{__git_token}" {dates_name} {args.RELEASE} oom')
        elif args.FREEIPA:
            freeipa_authentication_test()
        else:
            send_remote_command(f'sudo bash /home/u/starter.sh {branch} "{__git_token}" {dates_name} {args.RELEASE}')
            write_status(done)

    # ── backup_image.py:1044-1047 (подготовка прошла, сервисы доступны) ──
    remote_test_run()

    run.dates, run.dates_name = dates, dates_name
    # С какими `dates` запускался конечный скрипт: файл `dates_name` на момент
    # команды (его только что залили на стенд), либо строка целиком в
    # команде (`ipa_run.py {dates}` на хосте ALLTA).
    dates_path = f'/home/u/git/stress_test/allta_app/{dates_name}'
    for event in run.events:
        if dates_name in event.command:
            run.dates_seen.append(event.files[dates_path])
        elif dates in event.command:
            run.dates_seen.append(dates)
    return run


# ── API для тестов ───────────────────────────────────────────────────────────

def legacy_run(
    *, test: str, release: str, mode: str, kernel: str, stand: str, tcase: str, cti: str,
    parent_page: str, creds: LegacyCredentials, testnum: str = "1", tes: str = "off",
) -> LegacyRun:
    """Один тест прогона: `allta_back.execute_test` → `backup_image.py`.

    `test` — короткое имя (`tests[tcase]`), `release` — имя версии ОС,
    `stand` — `stand3`…, `tcase` — имя тест-кейса Zephyr (полное имя теста),
    `cti` — id папки Zephyr (`cycle_tree_index()`), `parent_page` —
    `legacy_parent_page(release, test)`. Ветка — `branches[tcase]`, как в
    `allta_back.py:522`.
    """
    cmd_args = build_command_args(
        test_name=test, stand=stand, release=release, kernel=kernel, mode=mode, tcas=tcase,
        branch=branches[tcase], cti=cti, pp=parent_page, testnum=testnum, tes=tes,
    )
    return _run_backup_image(backup_image_args(cmd_args), creds.tokens())


def legacy_dates(**kwargs) -> str:
    """Строка `dates`, которую `backup_image.py:426-427` пишет в `dates_<стенд>.conf`."""
    return legacy_run(**kwargs).dates


def legacy_dates_written(**kwargs) -> list[str]:
    """Все строки `dates`, с которыми запускался конечный скрипт, по порядку."""
    return legacy_run(**kwargs).dates_seen


def legacy_launch_commands(**kwargs) -> list[tuple[str, str]]:
    """Команды легаси по порядку: (`stand` | `host`, команда)."""
    return [(e.where, e.command) for e in legacy_run(**kwargs).events]


def argv_pairs(argv: list[str]) -> Counter:
    """Мультимножество пар «флаг → значение»: argparse не зависит от порядка флагов."""
    pairs: Counter = Counter()
    i = 0
    while i < len(argv):
        flag = argv[i]
        if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            pairs[(flag, argv[i + 1])] += 1
            i += 2
        else:
            pairs[(flag, None)] += 1
            i += 1
    return pairs
