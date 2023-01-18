"""
    Основная рабочая директория
"""
#SCRIPT_DIR = '/media/sf_git/stress_test/auditd_benchmark'
SCRIPT_DIR = '/home/u/git/stress_test/auditd_benchmark'

"""
    Названия основных файлов
"""
LOG_FILENAME = 'aub_log'
REPORT_FILENAME = 'aub_report.txt'
INFO_FILENAME = 'aub_info.txt'

"""
    Названия директорий
"""
LOG_DIR = '{}/log'.format(SCRIPT_DIR)
REPORT_DIR = '{}/report'.format(SCRIPT_DIR)
TEMPLATE_DIR = '{}/templates'.format(SCRIPT_DIR)

"""
    Полный путь до файлов
"""
LOG = '{}/{}'.format(LOG_DIR, LOG_FILENAME)
REPORT = '{}/{}'.format(REPORT_DIR, REPORT_FILENAME)

LATENCY_REPORT_PSAUD = '{}/aub_ps_report_latency.txt'.format(REPORT_DIR)
LATENCY_REPORT_USAUD = '{}/aub_us_report_latency.txt'.format(REPORT_DIR)
LATENCY_REPORT_FLAUD = '{}/aub_fl_report_latency.txt'.format(REPORT_DIR)

LOSSES_REPORT_PSAUD = '{}/aub_ps_report_losses.txt'.format(REPORT_DIR)
LOSSES_REPORT_USAUD = '{}/aub_us_report_losses.txt'.format(REPORT_DIR)
LOSSES_REPORT_FLAUD = '{}/aub_fl_report_losses.txt'.format(REPORT_DIR)

"""
    Системные пользователи
"""
MAIN_USER='u'
TEST_USER='tester'

"""
    Команды инициаторы событий для аудита процессов:
        'event_name' : ('positive command', 'negative command')
"""
PSAUD_PROC_BODYS = {
    'open': ('cat', ''),
    'create': ('touch',''),
    'exec': ('/bin/true',''),
    'delete': ('rm',''),
    'chmod': ('chmod 700', ''),
    'chown': ('chown :users',''),
    'mount': ('mount --bind',''),
    'module': ('/sbin/modprobe evbug',''),
    'uid': ('sudo -u {} /bin/true'.format(MAIN_USER), ''),
    'gid': ('sudo -u {} /bin/true'.format(MAIN_USER), ''),
    'acl': ('setfacl -m u:{}:rx'.format(MAIN_USER), ''),
    'mac': ('pdpl-file 0:63:0:ccnri', ''),
    'cap': ('usercaps -l 0x1 u', ''),
#    'chroot': ('(chroot /) &',''),
    'rename': ('mv',''),
    'net': ('ping -c 1 localhost','')
}

"""
    Команды инициаторы событий для аудита пользователей:
        'event_name' : ('positive command', 'negative command')
"""
USERAUD_PROC_BODYS = {
    'open': ('cat', ''),
    'create': ('touch',''),
    'exec': ('/bin/true',''),
    'delete': ('rm',''),
    'chmod': ('chmod 700', ''),
    'chown': ('chown :users',''),
    'mount': ('mount --bind',''),
    'module': ('/sbin/modprobe evbug',''),
    'uid': ('usermod -u 2005 {}'.format(TEST_USER), ''),
    'gid': ('usermod -g 2006 {}'.format(TEST_USER), ''),
    'audit': ('/usr/sbin/setfaud -m o:o:o',''),
    'acl': ('setfacl -m u:{}:rx'.format(TEST_USER), ''),
    'mac': ('/usr/sbin/pdpl-file 2:0:0',''),
    'cap': ('pscaps 0 0x1',''),
#    'chroot': ('(chroot /) &',''),
    'rename': ('mv',''),
    'net': ('ping -c 1 localhost','')
}

"""
    Команды инициаторы событий для аудита файлов:
        'event_name' : ('positive command', 'negative command')
"""
FILEAUD_PROC_BODYS = {
    'open': ('cat ', ''),
    'create': ('touch ', ''),
    'exec': ('', ''),
    'delete': ('rm -f ', ''),
    'chmod': ('chmod 777 ', ''),
    'chown': ('chown u:u ', ''),
    'audit': ('setfaud -m u:0:+exec ', ''),
    'acl': ('setfacl -m u:u:rw ', ''),
    'mac': ('/usr/sbin/pdpl-file 2:0:0 ', ''),
    'modify': ("echo '1' >> ", '')
}

"""
    Количество процессов-генераторов
"""
PS_LOWER_LIMIT = 10  #10
PS_UPPER_LIMIT = 60  #100
PS_STEP = 10  #10
"""
    Время жизни одного процессов-генераторов
"""
DEFAULT_PS_LIFETIME = 10 #100
"""
    Задержка повторной инициализации события
"""
DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY = 0.25

"""
    Описание основных графиков
"""
GRAPH_DESCRIPTIONS = {
    'aub_ps_total_eps_latency_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      'Сравнительный график зависимости задержки появления сообщений в логах аудита от количества событий в секунду, поступаемых на auditd. Сравнение по конкретным событиям.'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количества событий в секунду, поступаемых на auditd;</li>'
                                      '    <li><b>OY</b>: Задержка от момента генерации события до момента его появления в логах (сек);</li>'
                                      '</ul></p>',
    'aub_ps_total_eps_completed_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      'Сравнительный график зависимости количества отслеженных событий в процентах от количества событий в секунду, поступаемых на auditd. Сравнение по конкретным событиям.'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количества событий в секунду, поступаемых на auditd;</li>'
                                      '    <li><b>OY</b>: Отношение количества отслеженных событий к количеству сгенерированных (%);</li>'
                                      '</ul></p>',

    'aub_us_total_eps_latency_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      'Сравнительный график зависимости задержки появления сообщений в логах аудита от количества событий в секунду, поступаемых на auditd. Сравнение по конкретным событиям.'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количества событий в секунду, поступаемых на auditd;</li>'
                                      '    <li><b>OY</b>: Задержка от момента генерации события до момента его появления в логах (сек);</li>'
                                      '</ul></p>',
    'aub_us_total_eps_completed_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      'Сравнительный график зависимости количества отслеженных событий в процентах от количества событий в секунду, поступаемых на auditd. Сравнение по конкретным событиям.'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количества событий в секунду, поступаемых на auditd;</li>'
                                      '    <li><b>OY</b>: Отношение количества отслеженных событий к количеству сгенерированных (%);</li>'
                                      '</ul></p>',

    'aub_fl_total_eps_latency_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      'Сравнительный график зависимости задержки появления сообщений в логах аудита от количества событий в секунду, поступаемых на auditd. Сравнение по конкретным событиям.'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количества событий в секунду, поступаемых на auditd;</li>'
                                      '    <li><b>OY</b>: Задержка от момента генерации события до момента его появления в логах (сек);</li>'
                                      '</ul></p>',
    'aub_fl_total_eps_completed_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      'Сравнительный график зависимости количества отслеженных событий в процентах от количества событий в секунду, поступаемых на auditd. Сравнение по конкретным событиям.'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количества событий в секунду, поступаемых на auditd;</li>'
                                      '    <li><b>OY</b>: Отношение количества отслеженных событий к количеству сгенерированных (%);</li>'
                                      '</ul></p>',
}
