#!/home/u/python/Python-3.12.1/venv/bin/python3.12

import requests
import json
import argparse
import datetime
import traceback
import sys
import ast
import subprocess
import os
import threading
import signal

from typing import Optional

from allta_image_conf import branches, cycle_tree_index, tests, parent_page_list, JIRA_URL, tokens
from libs.libconfluence import SendCommentToConfluence
from libs.liballta import busy_status_control, TestTimeWatchdog



# ---------------------------------------------------------------------------
# Конфигурация флагов
# ---------------------------------------------------------------------------
TEST_FLAGS: dict[str, str] = {
    # -ps (dest='PSQL')
    'postgresql': '-ps psql',
    'postgresql-sm': '-ps psql',
    
    # -psql-parsec (dest='PSQL_PARSEC')
    'psql parsec': '-psql-parsec parsec',
    
    # -psql-vanilla (dest='PSQL_VANILLA')
    'psql vanilla': '-psql-vanilla pv',
    
    # -psql-bl (dest='PSQL_BALANCE', choices=['balance', 'info-sys'])
    'psql balance': '-psql-bl balance',
    'psql info-sys': '-psql-bl info-sys',
    

    # -psql-oom (dest='PSQL_OOM')
    'psql oom': '-psql-oom oom',
    
    # -db-kernels (dest='DB_KERNELS', choices=['psql', 'tantor'])
    'psql kernels': '-db-kernels psql',
    'tantor kernels': '-db-kernels tantor',
    
    # -tantor-vanilla (dest='TANTOR_VANILLA')
    'tantor vanilla': '-tantor-vanilla tv',
    
    # -psql_aud (dest='AUDIT_OFF')
    'postgresql-aud-off': '-psql_aud off',
    
    # -ovf (dest='OVF')
    'RAM-overflow': '-ovf ram',
    'SD-overflow': '-ovf sd',
    
    # -ipa (dest='FREEIPA', choices=['auth', 'create-users', 'plugin'])
    'FreeIPA auth': '-ipa auth',
    'FreeIPA c-users': '-ipa create-users',
    'FreeIPA plugin': '-ipa plugin',
    
    # -parsec-impact (dest='PARSEC_IMPACT')
    'parsec impact-fs': '-parsec-impact impact',
    
    # -parsec-impact-ao (dest='PARSEC_IMPACT_AO')
    'parsec impact-fs aud-off': '-parsec-impact-ao audit-off',
    
    # -apache (dest='APACHE')
    'apache-rp': '-apache rp',
    'apache-bp': '-apache apache_pam',
    
    # -lvirt (dest='LVIRT')
    'steal time': '-lvirt stealtime',
    'steal time-sm': '-lvirt stealtime_sm',
    'FIO': '-lvirt fio',
    'FIO large': '-lvirt fio_large',
    'vUnixBench': '-lvirt unixbench',
    'vPingPong': '-lvirt pingpong',
    
    # -vpn (dest='VPN')
    'AOpenVPNcc': '-vpn aovpncc',
    
    # -mail (dest='MAIL', choices=['imap', 'smtp'])
    'Dovecot-IMAP': '-mail imap',
    'Exim4-SMTP': '-mail smtp',
    
    # -network (dest='NETWORK', choices=['iof', 'dhcp'])
    'InitOnFree': '-network iof',
    'DHCP': '-network dhcp',
    
    # -kernel (dest='KERNELTEST')
    'SegFault': '-kernel segfault',
    'XFS mem leak': '-kernel xfs_memory_leak',
    
    # -olap (dest='PSQL_OLAP')
    'PSQL OLAP-hq': '-olap heavy_queries',

    # -astraevents (dest='ASTRAEVENTS')
    'astraevents': '-astraevents astraevents',
    'astraevents-sm': '-astraevents astraevents',
}

# Специальные флаги для auditd 
AUDITD_FLAGS: dict[str, str] = {
    'auditd-p': '-aud psaud',
    'auditd-f': '-aud fileaud',
    'auditd-u': '-aud useraud',
}


# Индивидуальные таймауты для тестов (в часах)
# Если теста нет в словаре — используется timeout_hours 
TEST_TIMEOUTS: dict[str, int] = {
    'syslog-ng-cwl': 36,  # 36 часов
}

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------
def parse_kernel_arg(kernel_raw: Optional[str]) -> list[str]:
    """
    Безопасный парсинг аргумента ядра
    """
    if not kernel_raw:
        return []
    try:
        parsed = ast.literal_eval(kernel_raw)
        if isinstance(parsed, list):
            return [str(k).strip() for k in parsed]
        return [str(parsed).strip()]
    except (ValueError, SyntaxError):
        return [kernel_raw.strip("[]' \"")]


def parse_tests_arg(tests_raw: str) -> list[str]:
    """
    Безопасный парсинг списка тестов
    """
    try:
        parsed = ast.literal_eval(tests_raw)
        if isinstance(parsed, list):
            return [str(t).strip() for t in parsed]
        return [str(parsed).strip()]
    except (ValueError, SyntaxError):
        return [tests_raw.strip("[]' \"")]


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


def execute_test(test_name: str,
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
                tt_watchdog: TestTimeWatchdog,
                start_time: datetime.datetime,
                timeout_hours: int = 12,
            ) -> None:
    """
    Запуск одного теста с логированием и замером времени.
    Если тест выполняется дольше timeout_hours — принудительно завершается.
    Для отдельных тестов можно указать индивидуальный таймаут в TEST_TIMEOUTS.
    """
    # Индивидуальный таймаут для теста, если задан
    effective_timeout = TEST_TIMEOUTS.get(test_name, timeout_hours)

    save_all_output(f'Тест: {test_name}\n')
    print(f'Тест: \033[92m{test_name}\033[0m')
    save_all_output(f'Ядро: {kernel}\n')
    print(f'Ядро: \033[92m{kernel}\033[0m')
    save_all_output(f'Выполняется... (таймаут: {effective_timeout} ч.)\n')
    print(f'Выполняется... (таймаут: {effective_timeout} ч.)')

    # Запись в conf-файл
    with open(f'conf/col3_body_{stand}.conf', 'w') as w:
        w.write(f'{release} | {kernel} | {test_name}')
    if test_name in ('FreeIPA auth', 'FreeIPA c-users'):
        with open('conf/col3_body_stand1.conf', 'w') as w:
            w.write(f'{release} | {kernel} | {test_name}')

    cmd_args = build_command_args(test_name=test_name,
                                  stand=stand,
                                  release=release,
                                  kernel=kernel,
                                  mode=mode,
                                  tcas=tcas,
                                  branch=branch,
                                  cti=cti,
                                  pp=pp,
                                  testnum=testnum,
                                  tes=tes,)

    command = f'./backup_image.py {cmd_args}'
    process = subprocess.Popen(command, shell=True, preexec_fn=os.setsid)

    # Поток для ожидания завершения процесса
    def wait_for_process():
        process.wait()

    waiter = threading.Thread(target=wait_for_process)
    waiter.start()
    waiter.join(timeout=effective_timeout * 3600)

    timed_out = False
    if waiter.is_alive():
        # Таймаут истёк — убиваем процесс
        timed_out = True
        save_all_output(f'[WATCHDOG] Тест {test_name} превысил лимит в {effective_timeout} ч. Завершаю принудительно...\n')
        print(f'[WATCHDOG] Тест \033[91m{test_name}\033[0m превысил лимит в {effective_timeout} ч. Завершаю принудительно...')

        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            process.wait()
        except ProcessLookupError:
            pass  # Процесс уже завершился

    end_time = datetime.datetime.now().replace(microsecond=0)

    if timed_out:
        save_all_output(f'Тест {test_name} завершён принудительно (timeout)\n')
        print(f'Тест \033[91m{test_name}\033[0m завершён принудительно (timeout)')
    else:
        save_all_output('Выполнен\n')
        print('Выполнен')

    save_all_output(f'Время завершения: {end_time}\n')
    print(f'Время завершения: {end_time}')
    elapsed = end_time - start_time
    save_all_output(f'Затрачено времени: {elapsed}\n')
    print(f'Затрачено времени: {elapsed}')
    tt_watchdog.transfer_test_time(test_name=test_name, time=str(elapsed))
    tt_watchdog.create_html()


# ---------------------------------------------------------------------------
# Парсинг аргументов
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser()
parser.add_argument('-rs', '--release', action='store', required=True, help='release num', dest='RELEASE')
parser.add_argument('-st', action='store', required=True, help='stand', dest='STAND')
parser.add_argument('-kn', action='store', required=False, help='kernel', dest='KERNEL')
parser.add_argument('-ts', action='store', required=True, help='test name(s)', dest='TESTS')
parser.add_argument('-te', action='store', required=True, help='test env status', dest='TESTENV')

args = parser.parse_args()
__conf_token = tokens['conf_token']
__username = tokens['username']
__jira_token = tokens['jira_token']

# Отладочный вывод
print(f"[DEBUG] Raw args.tests: {args.TESTS}")
print(f"[DEBUG] Raw args.kernel: {args.KERNEL}")
print(f"[DEBUG] Type of args.tests: {type(args.TESTS)}")
print(f"[DEBUG] Type of args.kernel: {type(args.KERNEL)}")
print(f"[DEBUG] Raw args.release: {args.RELEASE}")

# Парсинг
test_kernels = parse_kernel_arg(args.KERNEL)
__test_list = parse_tests_arg(args.TESTS)

if args.RELEASE:
    if args.RELEASE.startswith('[') and args.RELEASE.endswith(']'):
        __pt_version = args.RELEASE.strip('[]').replace("'", "").strip()
    else:
        __pt_version = args.RELEASE
else:
    __pt_version = ''
print(f"[DEBUG] Raw clean.release: {__pt_version}")

# Загрузка конфигурации
with open('allta_conf.json', 'r') as r:
    allta_conf = json.load(r)
rc_number = allta_conf['build_rc_relation'].get(__pt_version, '')
print(f"[DEBUG] Raw rc_number: {rc_number}")

__stand = args.STAND
bot_file = f'/home/u/git/stress_test/allta_app/telegrambot/results_{args.STAND}.txt'
total_start_time = datetime.datetime.now().replace(microsecond=0)

conf = SendCommentToConfluence(
    rc_name=__pt_version,
    rc_number=rc_number,
    username=__username,
    token=__conf_token,
)

# Версия для watchdog
if str(__pt_version).startswith('1.7'):
    update_version = '1.7'
elif str(__pt_version).startswith('1.8'):
    update_version = '1.8'
else:
    update_version = ''

tt_watchdog = TestTimeWatchdog(upd_version=update_version, stand=str(__stand))

# Формирование filter_url
check_len_version = __pt_version.split('.')
if len(check_len_version) == 4 and check_len_version[3] != 'UU':
    release_version = '.'.join(check_len_version[:3])
    rc_version = __pt_version
    filter_url = f"'%2Fstress_test%2F{release_version}%2F{rc_version}%2F**'"
elif len(check_len_version) == 6 and check_len_version[3] == 'UU':
    release_version = '.'.join(check_len_version[:5])
    rc_version = __pt_version
    filter_url = f"'%2Fstress_test%2F{release_version}%2F{rc_version}%2F**'"
else:
    filter_url = f"%27%2Fstress_test%27,%27%2Fstress_test%2F{__pt_version}%27"

# ---------------------------------------------------------------------------
# Запрос к Jira
# ---------------------------------------------------------------------------
matrix_url = f'''https://{JIRA_URL}/rest/tests/1.0/reports/testresults/matrix/testrun?displayUnit=COUNT&epicJQL=&jql=&
                period=MONTH&projectId=11200&scorecardOption=EXECUTION_RESULTS&tql=testResult.projectId+IN+(11200)+AND+testRun.
                folderName+IN+({filter_url})&traceabilityCustomTreeDisplayOption=
                CONDENSED&traceabilityMatrixOption=COVERAGE_TEST_CASES&traceabilityReportOption=COVERAGE_TEST_CASES&traceability
                TreeOption=COVERAGE_TEST_CASES
                '''
headers = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:102.0) Gecko/20100101 Firefox/102.0',
    'authority': JIRA_URL,
    'Authorization': __jira_token,
    'accept': 'application/json, text/plain, */*',
}

response = requests.get(matrix_url, headers=headers)
matrix = response.json()


def dates(index: int) -> list:
    """Конвертация данных matrix в плоский список."""
    name = [matrix[index]['testRuns'][x]['testRun']['name'].replace('_', ' ') for x in range(len(matrix[index]['testRuns']))]
    split_name = [x.split(' ') for x in name]
    test_case_name = matrix[index]['testCase']['name']
    status = [matrix[index]['testRuns'][x]['status']['i18nKey'].split('.')[2] for x in range(len(matrix[index]['testRuns']))]
    return [v for i in range(len(split_name)) for v in (split_name[i], test_case_name, status[i])]


dates_list_raw = [v for i in range(len(matrix)) for v in dates(i)]
dates_list = sorted([dates_list_raw[x:x + 3] for x in range(0, len(dates_list_raw), 3)])


# ---------------------------------------------------------------------------
# Функции логирования 
# ---------------------------------------------------------------------------
def save_status_output(output: str) -> None:
    with open(f'conf/status_output_{args.STAND}.log', 'a') as w:
        w.write(output)


def save_all_output(output: str) -> None:
    with open(f'conf/all_output_{args.STAND}.log', 'a') as w:
        w.write(output)


def bot_results(output: str) -> None:
    with open(bot_file, 'a') as bf:
        bf.write(output)


def calc_all_statistics() -> None:
    url = 'http://allta.devos.astralinux.ru:7777/all-statistics'
    data = {'username': __username, 'token': __conf_token}
    headers = {'Content-Type': 'application/json'}
    post = requests.post(url=url, data=json.dumps(data), headers=headers)
    if post.status_code == 200:
        print('Recalculate all statistics successfully done')
    else:
        print('Recalculate all statistics FAIL')
        print(f'Status code: {post.status_code}')
        print(f'Error: {post.text}')


# Очистка лог-файлов
with open(f'conf/status_output_{args.STAND}.log', 'w') as w:
    w.write('')
with open(f'conf/all_output_{args.STAND}.log', 'w') as w:
    w.write('')


# ---------------------------------------------------------------------------
# Вывод параметров запуска
# ---------------------------------------------------------------------------
header_kernel_part = f'| \033[93mВыбрано ядро: {args.KERNEL} \033[0m\n' if args.KERNEL else ''
header_plain_kernel_part = f'| Выбрано ядро: {args.KERNEL}\n' if args.KERNEL else ''

colored_header = f'''
 ----------------------------------------------------------------------------------------------------
 ----------------------------------------------------------------------------------------------------
| \033[43mПараметры запуска:\033[0m
| \033[93mВыбран релиз: {__pt_version} \033[0m 
| \033[93mВыбран стенд: {__stand} \033[0m
{header_kernel_part}| \033[93mВыбраны тесты: {__test_list} \033[0m                                                                    
 ----------------------------------------------------------------------------------------------------
 ----------------------------------------------------------------------------------------------------
'''

plain_header = f'''
 -----------------------------------
 -----------------------------------
| Параметры запуска:
| Выбран релиз: {__pt_version} 
| Выбран стенд: {__stand} 
{header_plain_kernel_part}| Выбраны тесты: {__test_list}                                                                    
 -----------------------------------
 -----------------------------------
'''

print(colored_header)
save_all_output(plain_header)
save_status_output(plain_header)

bot_head = f'''
 --------------------------------
| Параметры запуска:
| Выбран релиз: {__pt_version}
| Выбран стенд: {__stand}                                                                         
{header_plain_kernel_part}| Выбраны тесты: {__test_list}                                                                   
 --------------------------------
'''


# ---------------------------------------------------------------------------
# Основной цикл
# ---------------------------------------------------------------------------
try:
    for i, entry in enumerate(dates_list):
        start_time = datetime.datetime.now().replace(microsecond=0)
        busy_status_control(__stand, 'TestRunner')
        save_all_output('---------------\n')
        print('-----' * 20)
        save_all_output(f'Итерация № {i + 1}\n')
        print(f'Итерация № {i + 1}')
        progress = int((i + 1) * 100 / len(dates_list))
        save_all_output(f'Прогресс выполнения - {progress}%\n')
        print(f'Прогресс выполнения - {progress}%')
        save_all_output(f'Время запуска: {start_time}\n')
        print(f'Время запуска: {start_time}\n')

        run_data = entry[0]  # ['1.7.4', 'orel', '5.15.0-70-generic', 'stand1']
        tcas = entry[1]       # 'file system benchmark. XFS'
        test_mapped = tests[tcas]  # 'XFS', 'EXT4' и т.д.

        if run_data[3] != __stand:
            save_all_output(f'Cтенд: {run_data[3]} игнорируется\n')
            print(f'Cтенд: {run_data[3]} игнорируется')
            continue

        print(f'Cтенд: \033[92m{run_data[3]}\033[0m')
        save_all_output(f'Cтенд: {run_data[3]}\n')

        if test_mapped not in __test_list:
            save_all_output(f'Тест: {test_mapped} игнорируется\n')
            print(f'Тест: \033[91m{test_mapped}\033[0m игнорируется')
            continue

        if test_kernels:
            # Фильтрация по ядрам
            for kernel in test_kernels:
                if run_data[2] != kernel:
                    continue
                execute_test(
                    test_name=test_mapped,
                    stand=__stand,
                    release=__pt_version,
                    kernel=kernel,
                    mode=run_data[1],
                    tcas=tcas,
                    branch=branches[tcas],
                    cti=cycle_tree_index()[run_data[0]],
                    pp=parent_page_list()[__pt_version][test_mapped],
                    testnum=str(i + 1),
                    tes=args.TESTENV,
                    tt_watchdog=tt_watchdog,
                    start_time=start_time,
                )
        else:
            # Без фильтрации
            execute_test(
                test_name=test_mapped,
                stand=__stand,
                release=__pt_version,
                kernel=run_data[2],
                mode=run_data[1],
                tcas=tcas,
                branch=branches[tcas],
                cti=cycle_tree_index()[run_data[0]],
                pp=parent_page_list()[__pt_version][test_mapped],
                testnum=str(i + 1),
                tes=args.TESTENV,
                tt_watchdog=tt_watchdog,
                start_time=start_time,
            )

    calc_all_statistics()
    total_end_time = datetime.datetime.now().replace(microsecond=0)
    save_all_output('\nDONE\n')
    print('\n\033[95mDone\033[0m\n')
    with open(f'conf/work_status_{args.STAND}.conf', 'w') as wr:
        wr.write('Готово')
    bot_results('Прогон завершен')
    bot_results(bot_head)
    bot_results(f'Затрачено времени: {total_end_time - total_start_time}')
    conf.send_comment()
    busy_status_control(__stand, 'testrun done')

except Exception as e:
    error_message = f'Error Type: {type(e).__name__}\nMessage: {str(e)}\nTraceback:\n'
    error_message += ''.join(traceback.format_tb(e.__traceback__))
    print(error_message, file=sys.stderr)
    with open(f'conf/work_status_{args.STAND}.conf', 'w') as wr:
        wr.write('Готово')
    bot_results('Прогон завершен исключением')
    bot_results(bot_head)
    total_end_time = datetime.datetime.now().replace(microsecond=0)
    bot_results(f'Затрачено времени: {total_end_time - total_start_time}')
    busy_status_control(__stand, 'testrun fail')
