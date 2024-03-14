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
tests_list = {'PostgreSQL':      ['postgresql', 'psql parsec', 'psql kernels', 'psql vanilla', 'psql balance',
                                  'postgresql-sm', 'postgresql-aud-off', 'tantor vanilla', 'tantor kernels'],
              'Файловые системы':['XFS', 'EXT4', 'EXT4 parsec', 'NTFS', 'XFS parsec'],
              'Системные службы':['auditd-p', 'auditd-f', 'auditd-u', 'syslog-ng', 'RAM-overflow', 'SD-overflow'],
              'UnixBench':       ['unix', 'unix parsec'],
              'FreeIPA':         ['FreeIPA auth'],
              'Parsec':          ['parsec impact-fs', 'parsec impact-fs aud-off'],
              'Apache':          ['apache-rp']}

release_version = ['1.8.0.11',
                   '1.8.0.10',
                   '1.8.0.9',
                   '1.8.0.6',
                   '1.8.0.5', 
                   '1.8.0',
                   '1.7.5.UU.1.1',
                   '1.7.5.UU.1.7',
                   '1.7.5.UU.1',
                   '1.7.5',
                   '1.7.4.UU.1',
                   '1.7.4',
                   '1.7.3.UU.2',
                   '1.7.3.UU.1',
                   '1.7.3',
                   '1.7.2',
                   '1.7.1']

parent_page_list = {
    key:{value:f'STRESS_report {key} ⬝ {topic}' for topic in tests_list for value in tests_list[topic]}  
                     for key in release_version
                    }


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
    'postgresql benchmark kernels':'postgresql',
    'postgresql benchmark parsec':'postgresql',
    'postgresql benchmark vanilla':'postgresql',
    'postgresql benchmark smol':'postgresql',
    'postgresql benchmark audit-off':'postgresql',
    'tantor benchmark vanilla':'postgresql',
    'tantor benchmark kernels':'postgresql',
    'file system benchmark. OCFS2':'file_systems',
    'file system benchmark. NTFS':'file_systems',
    'file system benchmark. EXT3':'file_systems',
    'file system benchmark. EXT2':'file_systems',
    'file system benchmark. Fat32':'file_systems',
    'file system benchmark. EXT4 parsec':'file_systems',
    'file system benchmark. OCFS2 parsec':'file_systems',
    'file system benchmark. XFS parsec':'file_systems',
    'auditd benchmark. psaud':'auditd',
    'auditd benchmark. fileaud':'auditd',
    'auditd benchmark. useraud':'auditd',
    'syslog-ng benchmark':'syslog_ng',
    'linux_system_benchmark. UnixBench':'linux_system',
    'linux_system_benchmark. UnixBench parsec':'linux_system',
    'ram overflow':'overflow',
    'storage drive overflow':'overflow',
    'Parsec impact fs benchmark':'parsec',
    'Parsec impact fs benchmark audit-off':'parsec',
    'Apache_ReverseProxy':'apache2'
}



#################################################################################################################################################
#Tree_ID страницы тестового прогона
#################################################################################################################################################
cycle_tree_index = {
    'altlinux-5.10':'2982',
    'debian10':'2967',
    'debian10-5.15':'2974',
    'debian11-6.1':'2977',
    '1.7.5':'3032',
    '1.7.4':'2773',
    '1.7.3':'2774',
    '1.7.3.UU.1':'2745',
    '1.7.3.UU.2':'2808',
    '1.7.4.UU.1':'2936',
    '1.7.2':'2937',
    '1.7.1':'2947',
    '1.7.5.UU.1':'5538',
    '1.7.5.UU.1.1':'5539',
    '1.7.5.UU.1.7':'5970',
    '1.8.0':'5443',
    '1.8.0.5':'5444',
    '1.8.0.6':'5887',
    '1.8.0.9':'6044',
    '1.8.0.10':'6160',
    '1.8.0.11':'6256'
}



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
    'file system benchmark. Fat32':'Fat32',
    'file system benchmark. EXT4 parsec':'EXT4 parsec',
    'file system benchmark. XFS parsec':'XFS parsec',
    'file system benchmark. OCFS2 parsec':'OCFS2 parsec',
    'auditd benchmark. psaud':'auditd-p',
    'auditd benchmark. fileaud':'auditd-f',
    'auditd benchmark. useraud':'auditd-u',
    'syslog-ng benchmark':'syslog-ng',
    'linux_system_benchmark. UnixBench':'unix',
    'linux_system_benchmark. UnixBench parsec':'unix parsec',
    'storage drive overflow':'SD-overflow',
    'ram overflow':'RAM-overflow',
    'Apache_ReverseProxy':'apache-rp'
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



#################################################################################################################################################
#Основной перечень тестов
#################################################################################################################################################
group_tests = ['_LowServer group', '_MiddleServer group']
main_tests = ['XFS', 'EXT4', 'NTFS', 'EXT4 parsec', 'postgresql', 'postgresql-sm', 'psql parsec', 'auditd-p', 'auditd-u', 'tantor vanilla',
              'auditd-f', 'syslog-ng', 'unix', 'postgresql-aud-off', 'SD-overflow', 'RAM-overflow', 'XFS parsec', 'psql vanilla',
              'psql kernels', 'tantor kernels', 'unix parsec', 'psql balance', 'FreeIPA auth', 'parsec impact-fs', 'parsec impact-fs aud-off',
              'apache-rp']



#################################################################################################################################################
#Перечень тестов БРЕСТ
#################################################################################################################################################
brest_tests = ['apache-graph']



#################################################################################################################################################
#Доступные релизы (следует указывать при наличии снимка в Clonezilla)
#################################################################################################################################################
releases = ['1.7.1', '1.7.2', '1.7.3', '1.7.3.UU.1', '1.7.3.UU.2', '1.7.4', '1.7.4.UU.1', '1.7.5', '1.8.0.5', '1.7.5.UU.1.1', '1.8.0.6',
            '1.7.5.UU.1.7', '1.8.0.9', '1.8.0.10', '1.8.0.11']



#################################################################################################################################################
#Перечень доступных ядер
#################################################################################################################################################
kernels = ['5.10.0-1045-generic', '5.10.0-1057-generic', '5.10.142-1-generic', '5.15.0-33-generic', '5.15.0-33-lowlatency', 
           '5.10.176-1-generic', '5.15.0-70-generic', '5.15.0-70-lowlatency', '5.10.190-1-generic',
           '5.15.0-83-generic', '5.15.0-83-lowlatency', '6.1.50-1-generic']



#################################################################################################################################################
#Перечень стендов, отображаемых на разных страницах
#################################################################################################################################################
main_stands = ['stand1', 'stand2', 'stand3', 'stand4', 'stand5']
mobile_stands = ['stand1', 'stand2', 'stand3', 'stand4', 'stand5']
brest_stands = ['stand10', 'stand11', 'stand12']




#################################################################################################################################################
#Перечень настроек, используемых для создания тестовых прогонов
#################################################################################################################################################
repo_path = {
    'pkg_path_18testing':'http://qa111.devos.astralinux.ru/astra/testing/1.8-testing/installation/dists/1.8_x86-64/main/binary-amd64/Packages',
    'vers_path_18testing':'http://qa111.devos.astralinux.ru/astra/testing/1.8-testing/installation/dists/1.8_x86-64/Release',
    'pkg_path_17testing':'http://qa111.devos.astralinux.ru/astra/testing/1.7-testing/base-repository/dists/1.7_x86-64/main/binary-amd64/Packages',
    'vers_path_17testing':'http://qa111.devos.astralinux.ru/astra/testing/1.7-testing/base-repository/dists/1.7_x86-64/Release',
    'vers_path_175':'http://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository-5/dists/1.7_x86-64/Release',
    'pkg_path_175':'http://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository-5/dists/1.7_x86-64/main/binary-amd64/Packages',
    'vers_path_174UU1':'http://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository-4.1/dists/1.7_x86-64/Release',
    'pkg_path_174UU1':'http://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository-4.1/dists/1.7_x86-64/main/binary-amd64/Packages',
    'vers_path_174':'http://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository-4/dists/1.7_x86-64/Release',
    'pkg_path_174':'http://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository-4/dists/1.7_x86-64/main/binary-amd64/Packages',
    'vers_path_173UU2':'http://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository-3.2/dists/1.7_x86-64/Release',
    'pkg_path_173UU2':'http://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository-3.2/dists/1.7_x86-64/main/binary-amd64/Packages',
    'vers_path_173UU1':'http://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository-3.1/dists/1.7_x86-64/Release',
    'pkg_path_173UU1':'http://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository-3.1/dists/1.7_x86-64/main/binary-amd64/Packages',
    'vers_path_173':'http://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository-3/dists/1.7_x86-64/Release',
    'pkg_path_173':'http://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository-3/dists/1.7_x86-64/main/binary-amd64/Packages',
    'vers_path_172UU1':'http://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository-2.1/dists/1.7_x86-64/Release',
    'pkg_path_172UU1':'http://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository-2.1/dists/1.7_x86-64/main/binary-amd64/Packages',
    'vers_path_172':'http://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository-2/dists/1.7_x86-64/Release',
    'pkg_path_172':'http://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository-2/dists/1.7_x86-64/main/binary-amd64/Packages',
    'vers_path_171':'http://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository-1/dists/1.7_x86-64/Release',
    'pkg_path_171':'http://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository-1/dists/1.7_x86-64/main/binary-amd64/Packages'
}

rc_list = ['1.7.5.UU.1.1', '1.8.0.5', '1.8.0.6', '1.8.0.9', '1.8.0.10', '1.8.0.11', '1.7.5.UU.1.7',]
releases_list = ['1.7.1', '1.7.2', '1.7.3', '1.7.3.UU.1', '1.7.3.UU.2', '1.7.4', '1.7.4.UU.1', '1.7.5', '1.7.5.UU.1', '1.8.0']
STP_VERSION = sorted(list(set(rc_list + releases_list)))

testcase_orel_low_stand3 = ['EXT4', 'XFS', 'syslog-ng', 'unix'] #, 'NTFS']
testcase_smolensk_low_stand3 = ['EXT4 parsec', 'XFS parsec', 'auditd-f', 'auditd-p', 'auditd-u', 'unix parsec', 'parsec impact-fs',
                                'parsec impact-fs aud-off', 'apache-rp']
testcase_orel_middle_stand4 = ['postgresql-aud-off', 'postgresql', 'psql vanilla', 'psql kernels',
                               'psql balance', 'FreeIPA auth'] #'tantor vanilla', 'tantor kernels'
testcase_smolensk_middle_stand4 = ['postgresql-sm', 'psql parsec']

LowServer_group = ['EXT4', 'XFS', 'syslog-ng', 'unix', 'EXT4 parsec', 'XFS parsec', 'auditd-f', 'auditd-p', 'auditd-u', 'unix parsec',
                   'parsec impact-fs', 'parsec impact-fs aud-off', 'apache-rp']
MiddleServer_group = ['postgresql-aud-off', 'postgresql', 'psql vanilla', 'psql kernels', 'postgresql-sm', 'psql balance', 'FreeIPA auth',
                      'psql parsec']

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
    'psql balance':'BT-T13362',
    'FreeIPA auth':'BT-T13481',
    'parsec impact-fs':'BT-T13486',
    'parsec impact-fs aud-off':'BT-T13489',
    'apache-rp':'BT-T13621'
}



#################################################################################################################################################
#Перечень колонок zefir для СТП
#################################################################################################################################################
testname_columns = {
                    'file system benchmark. EXT4':'FS_EXT4', 'file system benchmark. XFS':'FS_XFS', 
                    'file system benchmark. OCFS2':'FS_OCFS2', 'file system benchmark. NTFS':'FS_NTFS',
                    'auditd benchmark. psaud':'Auditd_psaud', 'linux_system_benchmark. UnixBench':'UnixBench',
                    'file system benchmark. EXT3':'FS_EXT3', 'file system benchmark. EXT2':'FS_EXT2',
                    'file system benchmark. Fat32':'FS_Fat32', 'syslog-ng benchmark':'Syslog-NG', 'postgresql benchmark':'PostgreSQL',
                    'file system benchmark. EXT4 parsec':'FS_EXT4_parsec', 'auditd benchmark. fileaud':'Auditd_fileaud',
                    'auditd benchmark. useraud':'Auditd_useraud', 'file system benchmark. OCFS2 parsec':'FS_OCFS2_parsec',
                    'postgresql benchmark smol':'PostgreSQL_smol', 'postgresql benchmark audit-off':'PSQL_audit-off',
                    'storage drive overflow':'SD_overflow', 'ram overflow':'RAM_overflow', 'file system benchmark. XFS parsec':'FS_XFS_parsec',
                    'postgresql benchmark parsec':'PSQL_parsec', 'postgresql benchmark vanilla':'PSQL_vanilla',
                    'tantor benchmark vanilla':'Tantor_vanilla', 'postgresql benchmark kernels':'PSQL_kernels',
                    'tantor benchmark kernels':'Tantor_kernels', 'linux_system_benchmark. UnixBench parsec':'UnixBench_parsec',
                    'postgresql benchmark balance':'PSQL_balance', 'freeipa authentication test':'FreeIPA_auth',
                    'Parsec impact fs benchmark':'Parsec_impact-fs', 'Parsec impact fs benchmark audit-off':'Parsec_imp-fs_aud-off',
                    'Apache_ReverseProxy':'Apache_RP'
                    }


