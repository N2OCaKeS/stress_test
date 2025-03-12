import json

def get_allta_conf():
    with open('./allta_conf.json', 'r') as r:
        return json.load(r)

#################################################################################################################################################
#Venv
#################################################################################################################################################
VENV_PATH = '/home/u/python/Python-3.12.1/venv/bin/python3.12'



#################################################################################################################################################
#Перечень IP используемых серверов
#################################################################################################################################################
stands_ip = {
    'stand1':'10.177.103.201',
    'stand2':'10.177.103.202',
    'stand3':'10.177.103.204',
    'stand4':'10.177.103.203',
    'stand5':'10.177.103.205',
    #'stand10':'10.177.102.249', #KD
    #'stand11':'10.177.102.200', #фронт бреста
    #'stand12':'10.177.102.233'  #сервер для нагрузки
}



#################################################################################################################################################
#Atlassian URLs
#################################################################################################################################################
JIRA_URL = 'jira.astralinux.ru'
CONFLUENCE_URL = 'life.astralinux.ru'



#################################################################################################################################################
#Режимы защищенности
#################################################################################################################################################
modes = {
    'orel':'0',
    'voronezh':'1',
    'smolensk':'2',
}



#################################################################################################################################################
#Симлинки на страницы с местами хранения результатов
#################################################################################################################################################
def parent_page_list():
    tests_list = {'PostgreSQL':      ['postgresql', 'psql parsec', 'psql kernels', 'psql vanilla', 'psql balance',
                                    'postgresql-sm', 'postgresql-aud-off', 'tantor vanilla', 'tantor kernels', 'psql oom'],
                'Файловые системы':['XFS', 'EXT2', 'EXT3', 'EXT4', 'EXT4 parsec', 'NTFS', 'XFS parsec', 'FAT', 'EXFAT', 'OCFS2'],
                'Системные службы':['auditd-p', 'auditd-f', 'auditd-u', 'syslog-ng', 'RAM-overflow', 'SD-overflow', 'syslog-ng-cwl'],
                'UnixBench':       ['unix', 'unix parsec'],
                'FreeIPA':         ['FreeIPA auth'],
                'Parsec':          ['parsec impact-fs', 'parsec impact-fs aud-off'],
                'Apache':          ['apache-rp'],
                'Qemu/KVM/Libvirt':['steal time', 'steal time-sm', 'FIO', 'vUnixBench', 'vPingPong']}

    parent_page_list = {
        key:{value:f'STRESS_report {key} ⬝ {topic}' for topic in tests_list for value in tests_list[topic]}  
                        for key in get_allta_conf()['release_version']
                        }
    return parent_page_list


# # # Example:
# {'1.8.0.1': {'postgresql': 'STRESS 1.8.0.1 ⬝ PostgreSQL', 
#              'psql parsec': 'STRESS 1.8.0.1 ⬝ PostgreSQL', 
#              'psql kernels': 'STRESS 1.8.0.1 ⬝ PostgreSQL', 
#              'psql vanilla': 'STRESS 1.8.0.1 ⬝ PostgreSQL', 
#              'postgresql-sm': 'STRESS 1.8.0.1 ⬝ PostgreSQL', 
#              'postgresql-aud-off': 'STRESS 1.8.0.1 ⬝ PostgreSQL', 
#              'tantor vanilla': 'STRESS 1.8.0.1 ⬝ PostgreSQL', 
#              'tantor kernels': 'STRESS 1.8.0.1 ⬝ PostgreSQL', 
#              'XFS': 'STRESS 1.8.0.1 ⬝ Файловые системы', 
#              'EXT4': 'STRESS 1.8.0.1 ⬝ Файловые системы', 
#              'EXT4 parsec': 'STRESS 1.8.0.1 ⬝ Файловые системы', 
#              'NTFS': 'STRESS 1.8.0.1 ⬝ Файловые системы', 
#              'XFS parsec': 'STRESS 1.8.0.1 ⬝ Файловые системы', 
#              'auditd-p': 'STRESS 1.8.0.1 ⬝ Системные службы', 
#              'auditd-f': 'STRESS 1.8.0.1 ⬝ Системные службы', 
#              'auditd-u': 'STRESS 1.8.0.1 ⬝ Системные службы', 
#              'syslog-ng': 'STRESS 1.8.0.1 ⬝ Системные службы', 
#              'RAM-overflow': 'STRESS 1.8.0.1 ⬝ Системные службы', 
#              'SD-overflow': 'STRESS 1.8.0.1 ⬝ Системные службы', 
#              'unix': 'STRESS 1.8.0.1 ⬝ UnixBench', 
#              'unix parsec': 'STRESS 1.8.0.1 ⬝ UnixBench'}}



#################################################################################################################################################
#Ветки проектов в git
#################################################################################################################################################
branches = {
    'freeipa authentication test':'freeipa',
    'file system benchmark. EXT4':'file_systems',
    'file system benchmark. XFS':'file_systems',
    'postgresql benchmark':'postgresql',
    'postgresql benchmark balance':'postgresql',
    'postgresql benchmark oom':'postgresql',
    'postgresql benchmark kernels':'postgresql',
    'postgresql benchmark parsec':'postgresql',
    'postgresql benchmark vanilla':'postgresql',
    'postgresql benchmark smol':'postgresql',
    'postgresql benchmark audit-off':'postgresql',
    'tantor benchmark vanilla':'postgresql',
    'tantor benchmark kernels':'postgresql',
    'file system benchmark. OCFS2':'cluster_file_systems',
    'file system benchmark. NTFS':'file_systems',
    'file system benchmark. EXT3':'file_systems',
    'file system benchmark. EXT2':'file_systems',
    'file system benchmark. FAT':'file_systems',
    'file system benchmark. EXFAT':'file_systems',
    'file system benchmark. EXT4 parsec':'file_systems',
    'file system benchmark. OCFS2 parsec':'cluster_file_systems',
    'file system benchmark. XFS parsec':'file_systems',
    'auditd benchmark. psaud':'auditd',
    'auditd benchmark. fileaud':'auditd',
    'auditd benchmark. useraud':'auditd',
    'syslog-ng benchmark':'syslog_ng',
    'syslog-ng benchmark check-write-log':'syslog_ng',
    'linux_system_benchmark. UnixBench':'linux_system',
    'linux_system_benchmark. UnixBench parsec':'linux_system',
    'ram overflow':'overflow',
    'storage drive overflow':'overflow',
    'Parsec impact fs benchmark':'parsec',
    'Parsec impact fs benchmark audit-off':'parsec',
    'Apache_ReverseProxy':'apache2',
    'Steal time':'virt',
    'Steal time smolensk':'virt',
    'FIO benchmark':'virt',
    'Virt UnixBench':'virt',
    'vPingPong':'virt'
}



#################################################################################################################################################
#Tree_ID страницы тестового прогона
#################################################################################################################################################
def cycle_tree_index():
    return get_allta_conf()['cycle_tree_index']



#################################################################################################################################################
#Конвертируемый перечень тестов
#################################################################################################################################################
tests = {
    'Parsec impact fs benchmark audit-off':'parsec impact-fs aud-off',
    'Parsec impact fs benchmark':'parsec impact-fs',
    'freeipa authentication test':'FreeIPA auth',
    'file system benchmark. EXT4':'EXT4',
    'file system benchmark. XFS':'XFS',
    'postgresql benchmark':'postgresql',
    'postgresql benchmark balance':'psql balance',
    'postgresql benchmark oom':'psql oom',
    'postgresql benchmark kernels':'psql kernels',
    'postgresql benchmark parsec':'psql parsec',
    'postgresql benchmark vanilla':'psql vanilla',
    'postgresql benchmark smol':'postgresql-sm',
    'postgresql benchmark audit-off':'postgresql-aud-off',
    'tantor benchmark vanilla':'tantor vanilla',
    'tantor benchmark kernels':'tantor kernels',
    'file system benchmark. OCFS2':'OCFS2',
    'file system benchmark. NTFS':'NTFS',
    'file system benchmark. EXT3':'EXT3',
    'file system benchmark. EXT2':'EXT2',
    'file system benchmark. FAT':'FAT',
    'file system benchmark. EXFAT':'EXFAT',
    'file system benchmark. EXT4 parsec':'EXT4 parsec',
    'file system benchmark. XFS parsec':'XFS parsec',
    'file system benchmark. OCFS2 parsec':'OCFS2 parsec',
    'auditd benchmark. psaud':'auditd-p',
    'auditd benchmark. fileaud':'auditd-f',
    'auditd benchmark. useraud':'auditd-u',
    'syslog-ng benchmark':'syslog-ng',
    'syslog-ng benchmark check-write-log':'syslog-ng-cwl',
    'linux_system_benchmark. UnixBench':'unix',
    'linux_system_benchmark. UnixBench parsec':'unix parsec',
    'storage drive overflow':'SD-overflow',
    'ram overflow':'RAM-overflow',
    'Apache_ReverseProxy':'apache-rp',
    'Steal time':'steal time',
    'Steal time smolensk':'steal time-sm',
    'FIO benchmark':'FIO',
    'Virt UnixBench':'vUnixBench',
    'vPingPong':'vPingPong'
}



#################################################################################################################################################
#Настройки для внутренней СУБД
#################################################################################################################################################
psyc = {
        'host':'127.0.0.1',
        'database':'bendiks',
        'user':'postgres',
        'password':'1'
}    

psyc_conf = {
            'host':'127.0.0.1',
            'database':'b_config',
            'user':'postgres',
            'password':'1'
} 



#################################################################################################################################################
#Основной перечень тестов
#################################################################################################################################################
group_tests = ['_LowServer group', '_MiddleServer group']
main_tests = ['XFS', 'EXT4', 'NTFS', 'EXT4 parsec', 'postgresql', 'postgresql-sm', 'psql parsec', 'auditd-p', 'auditd-u', 'tantor vanilla',
              'auditd-f', 'syslog-ng', 'unix', 'postgresql-aud-off', 'SD-overflow', 'RAM-overflow', 'XFS parsec', 'psql vanilla', 'syslog-ng-cwl',
              'psql kernels', 'tantor kernels', 'unix parsec', 'psql balance', 'FreeIPA auth', 'parsec impact-fs', 'parsec impact-fs aud-off',
              'apache-rp', 'steal time', 'EXT2', 'EXT3', 'FAT', 'EXFAT', 'FIO', 'vUnixBench', 'vPingPong', 'OCFS2', 'steal time-sm', 'psql oom']



#################################################################################################################################################
#Перечень тестов БРЕСТ
#################################################################################################################################################
brest_tests = ['apache-graph']



#################################################################################################################################################
#Доступные релизы (следует указывать при наличии снимка в Clonezilla)
#################################################################################################################################################
def releases():
    return get_allta_conf()['releases']



#################################################################################################################################################
#Перечень доступных ядер
#################################################################################################################################################
def kernels():
    return get_allta_conf()['kernels']



#################################################################################################################################################
#Перечень стендов, отображаемых на разных страницах
#################################################################################################################################################
main_stands = ['stand1', 'stand2', 'stand3', 'stand4', 'stand5']
mobile_stands = ['stand1', 'stand2', 'stand3', 'stand4', 'stand5']
brest_stands = ['stand10', 'stand11', 'stand12']




#################################################################################################################################################
#Перечень настроек, используемых для создания тестовых прогонов
#################################################################################################################################################
def repo_path():
    return get_allta_conf()['repo_path']

def rc_list():
    return get_allta_conf()['rc_list']

def releases_list():
    return get_allta_conf()['releases_list']

def stp_version():
    return sorted(list(set(rc_list() + releases_list())))

testcase_orel_low_stand3 = ['EXT4', 'XFS', 'syslog-ng', 'unix', 'EXT2', 'EXT3', 'FAT', 'EXFAT', 'NTFS']
testcase_smolensk_low_stand3 = ['EXT4 parsec', 'XFS parsec', 'auditd-f', 'auditd-p', 'auditd-u', 'unix parsec', 'parsec impact-fs',
                                'parsec impact-fs aud-off', 'apache-rp']
testcase_orel_middle_stand4 = ['postgresql-aud-off', 'postgresql', 'psql vanilla', 'psql kernels', 'OCFS2', 'syslog-ng-cwl',
                               'psql balance', 'FreeIPA auth', 'steal time', 'FIO', 'vUnixBench', 'vPingPong'] #'tantor vanilla', 'tantor kernels'
testcase_smolensk_middle_stand4 = ['postgresql-sm', 'psql parsec', 'steal time-sm', 'psql oom']

LowServer_group = ['EXT4', 'XFS', 'syslog-ng', 'unix', 'EXT4 parsec', 'XFS parsec', 'auditd-f', 'auditd-p', 'auditd-u', 'unix parsec',
                   'parsec impact-fs', 'parsec impact-fs aud-off', 'apache-rp', 'EXT2', 'EXT3', 'FAT', 'EXFAT', 'NTFS']
MiddleServer_group = ['postgresql-aud-off', 'postgresql', 'psql vanilla', 'psql kernels', 'postgresql-sm', 'FreeIPA auth', 'syslog-ng-cwl',
                      'psql parsec', 'steal time', 'FIO', 'vUnixBench', 'vPingPong', 'OCFS2', 'steal time-sm', 'psql oom'] #'psql balance',


#testcase_orel = ['EXT4', 'NTFS', 'XFS', 'postgresql-aud-off', 'postgresql', 'psql vanilla', 'syslog-ng', 'unix', 'tantor vanilla']
#testcase_orel_stand2 = ['EXT4', 'NTFS', 'XFS', 'syslog-ng', 'unix', 'RAM-overflow', 'SD-overflow']
#testcase_smolensk = ['postgresql-sm', 'psql parsec', 'EXT4 parsec', 'XFS parsec', 'auditd-f', 'auditd-p', 'auditd-u']
#testcase_smolensk_stand2 = ['EXT4 parsec', 'XFS parsec', 'auditd-f', 'auditd-p', 'auditd-u']
test_run_stands = [f'stand{x}' for x in range(3, 6, 1)]
test_run_modes = ['orel', 'smolensk']
tests_case_zefir_key = {
    'postgresql':'BT-T7555',
    'psql kernels':'BT-T13230',
    'postgresql-aud-off':'BT-T9169',
    'postgresql-sm':'BT-T9106',
    'psql parsec':'BT-T9638',
    'psql vanilla':'BT-T9673',
    'tantor vanilla':'BT-T9695',
    'tantor kernels':'BT-T13243',
    'EXT4':'BT-T7562',
    'EXT4 parsec':'BT-T8822',
    'NTFS':'BT-T7564',
    'XFS':'BT-T8069',
    'XFS parsec':'BT-T9488',
    'unix':'BT-T8737',
    'unix parsec':'BT-T13267',
    'RAM-overflow':'BT-T9170',
    'SD-overflow':'BT-T9172',
    'auditd-f':'BT-T8215',
    'auditd-p':'BT-T8213',
    'auditd-u':'BT-T8214',
    'syslog-ng':'BT-T8119',
    'syslog-ng-cwl':'BT-T16383',
    'psql balance':'BT-T13362',
    'FreeIPA auth':'BT-T13481',
    'parsec impact-fs':'BT-T13486',
    'parsec impact-fs aud-off':'BT-T13489',
    'apache-rp':'BT-T13621',
    'steal time':'BT-T13735',
    'EXT2':'BT-T7560',
    'EXT3':'BT-T7561',
    'FAT':'BT-T7563',
    'EXFAT':'BT-T13736',
    'FIO':'BT-T13864',
    'vUnixBench':'BT-T14097',
    'vPingPong':'BT-T14145',
    'OCFS2':'BT-T7848',
    'steal time-sm':'BT-T15186',
    'psql oom':'BT-T16134'
}



#################################################################################################################################################
#Перечень колонок zefir для СТП
#################################################################################################################################################
testname_columns = {
                    'file system benchmark. EXT4':'FS_EXT4', 'file system benchmark. XFS':'FS_XFS', 
                    'file system benchmark. OCFS2':'FS_OCFS2', 'file system benchmark. NTFS':'FS_NTFS',
                    'auditd benchmark. psaud':'Auditd_psaud', 'linux_system_benchmark. UnixBench':'UnixBench',
                    'file system benchmark. EXT3':'FS_EXT3', 'file system benchmark. EXT2':'FS_EXT2',
                    'file system benchmark. FAT':'FS_FAT', 'syslog-ng benchmark':'Syslog-NG', 'postgresql benchmark':'PostgreSQL',
                    'file system benchmark. EXT4 parsec':'FS_EXT4_parsec', 'auditd benchmark. fileaud':'Auditd_fileaud',
                    'auditd benchmark. useraud':'Auditd_useraud', 'file system benchmark. OCFS2 parsec':'FS_OCFS2_parsec',
                    'postgresql benchmark smol':'PostgreSQL_smol', 'postgresql benchmark audit-off':'PSQL_audit-off',
                    'storage drive overflow':'SD_overflow', 'ram overflow':'RAM_overflow', 'file system benchmark. XFS parsec':'FS_XFS_parsec',
                    'postgresql benchmark parsec':'PSQL_parsec', 'postgresql benchmark vanilla':'PSQL_vanilla',
                    'tantor benchmark vanilla':'Tantor_vanilla', 'postgresql benchmark kernels':'PSQL_kernels',
                    'tantor benchmark kernels':'Tantor_kernels', 'linux_system_benchmark. UnixBench parsec':'UnixBench_parsec',
                    'postgresql benchmark balance':'PSQL_balance', 'freeipa authentication test':'FreeIPA_auth',
                    'Parsec impact fs benchmark':'Parsec_impact-fs', 'Parsec impact fs benchmark audit-off':'Parsec_imp-fs_aud-off',
                    'Apache_ReverseProxy':'Apache_RP', 'Steal time':'Steal_time', 'file system benchmark. EXFAT':'FS_EXFAT',
                    'FIO benchmark':'FIO', 'Virt UnixBench':'vUnixBench', 'vPingPong':'vPingPong', 'Steal time smolensk':'Steal_time-sm',
                    'postgresql benchmark oom':'PSQL_OOM', 'syslog-ng benchmark check-write-log':'Syslog-NG-cwl'
                    }



#################################################################################################################################################
#Перечень соответствий релизов и build_version для генерации путей репозиториев
#################################################################################################################################################
def releases_dict():
    return get_allta_conf()['releases_dict']



#################################################################################################################################################
#Перечень ядер, используемых для отображения в списке ядер
#################################################################################################################################################
startswith_kernel_list = ['6.1', '6.6', '6.12', '5.15', '5.10']



#################################################################################################################################################
#Перечень снимков clonezilla 
#################################################################################################################################################
def cz_comm():
    return get_allta_conf()['cz_comm']



#################################################################################################################################################
#ALLTA version
#################################################################################################################################################
def allta_version():
    with open("ChangeLog", "r") as r:
        return r.readline().split(" ")[-1]
    


#################################################################################################################################################
#Найденные баги
#################################################################################################################################################
known_bugs = {
    "PostgreSQL": {
        "BT-51261": "https://jira.astralinux.ru/browse/BT-51261",
        "BT-37797": "https://jira.astralinux.ru/browse/BT-37797",
        "BT-40316": "https://jira.astralinux.ru/browse/BT-40316",
        "BT-35869": "https://jira.astralinux.ru/browse/BT-35869",
        "BT-48408": "https://jira.astralinux.ru/browse/BT-48408",
        "BT-61532": "https://jira.astralinux.ru/browse/BT-61532"
    },
    "Файловые системы": {
        "BT-38366": "https://jira.astralinux.ru/browse/BT-38366",
        "BT-54712": "https://jira.astralinux.ru/browse/BT-54712"
    },
    "Parsec": {
        "BT-61530": "https://jira.astralinux.ru/browse/BT-61530",
        "BT-52579": "https://jira.astralinux.ru/browse/BT-52579",
        "BT-69978": "https://jira.astralinux.ru/browse/BT-69978"
    },
    "Apache": {
        "BT-64331": "https://jira.astralinux.ru/browse/BT-64331"
    },
    "FreeIPA": {
        "BT-66518": "https://jira.astralinux.ru/browse/BT-66518"
    }
}



#################################################################################################################################################
#Аннотации к артефактам производительности
#################################################################################################################################################
unixbench_annotations = """
Падение производительности в 1.7.6 было вызвано ошибкой обновления правил аудита при astra-update -ATr. BT-61530.
"""
psql_annotations = """
Падение производительности в 1.7.6 было вызвано ошибкой обновления правил аудита при astra-update -ATr. BT-61530.
Падение производительности в 1.8.0 связано с блокировками. BT-51261.
"""

annotations = {
    "UnixBench": unixbench_annotations,
    "PostgreSQL": psql_annotations
}