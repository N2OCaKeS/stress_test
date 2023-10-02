#################################################################################################################################################
#Перечень IP используемых серверов
#################################################################################################################################################
stands_ip = {
    'stand1':'10.177.103.201',
    'stand2':'10.177.103.202',
    'stand3':'10.177.103.203',
    'stand4':'10.177.103.204',
    'stand10':'10.177.102.249', #KD
    'stand11':'10.177.102.200', #фронт бреста
    'stand12':'10.177.102.233'  #сервер для нагрузки
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
parent_page_list = {
    'debian10':{'postgresql':'Debian ⬝ PostgreSQL',
             },
    'debian10-5.15':{'postgresql':'Debian ⬝ PostgreSQL',
             },
    'debian11-6.1':{'postgresql':'Debian ⬝ PostgreSQL',
             },
    'altlinux-5.10':{'postgresql':'Debian ⬝ PostgreSQL',
             },
    '1.7.5.5':{'postgresql':'1.7.5.5 ⬝ PostgreSQL',
             'postgresql-sm':'1.7.5.5 ⬝ PostgreSQL',
             'postgresql-aud-off':'1.7.5.5 ⬝ PostgreSQL',
             'XFS':'',
             'EXT4':'',
             'EXT4 parsec':'',
             'NTFS':'',
             'XFS parsec':'1.7.5.5 ⬝ Файловые системы',
             'auditd-p':'',
             'auditd-f':'',
             'auditd-u':'',
             'syslog-ng':'',
             'RAM-overflow':'',
             'SD-overflow':'',
             'unix':''
            },
    '1.7.5.4':{'postgresql':'1.7.5.4 ⬝ PostgreSQL',
             'postgresql-sm':'1.7.5.4 ⬝ PostgreSQL',
             'postgresql-aud-off':'1.7.5.4 ⬝ PostgreSQL',
             'XFS':'1.7.5.4 ⬝ Файловые системы',
             'EXT4':'1.7.5.4 ⬝ Файловые системы',
             'EXT4 parsec':'1.7.5.4 ⬝ Файловые системы',
             'NTFS':'1.7.5.4 ⬝ Файловые системы',
             'XFS parsec':'1.7.5.4 ⬝ Файловые системы',
             'auditd-p':'',
             'auditd-f':'',
             'auditd-u':'',
             'syslog-ng':'',
             'RAM-overflow':'',
             'SD-overflow':'',
             'unix':''
            },
    '1.7.5':{'postgresql':'1.7.5 ⬝ PostgreSQL',
             'postgresql-sm':'1.7.5 ⬝ PostgreSQL',
             'postgresql-aud-off':'1.7.5 ⬝ PostgreSQL',
             'XFS':'1.7.5 ⬝ Файловые системы',
             'EXT4':'1.7.5 ⬝ Файловые системы',
             'EXT4 parsec':'1.7.5 ⬝ Файловые системы',
             'NTFS':'1.7.5 ⬝ Файловые системы',
             'auditd-p':'1.7.5 ⬝ Системные службы',
             'auditd-f':'1.7.5 ⬝ Системные службы',
             'auditd-u':'1.7.5 ⬝ Системные службы',
             'syslog-ng':'1.7.5 ⬝ Системные службы',
             'RAM-overflow':'1.7.5 ⬝ Системные службы',
             'SD-overflow':'1.7.5 ⬝ Системные службы',
             'unix':'1.7.5 ⬝ UnixBench'
            },
    '1.7.4.UU.1':{'postgresql':'1.7.4.UU.1 ⬝ PostgreSQL',
                  'postgresql-sm':'1.7.4.UU.1 ⬝ PostgreSQL',
                  'postgresql-aud-off':'1.7.4.UU.1 ⬝ PostgreSQL',
                  'XFS':'1.7.4.UU.1 ⬝ Файловые системы',
                  'NTFS':'1.7.4.UU.1 ⬝ Файловые системы',
                  'EXT4':'1.7.4.UU.1 ⬝ Файловые системы',
                  'EXT4 parsec':'1.7.4.UU.1 ⬝ Файловые системы',
                  'auditd-p':'1.7.4.UU.1 ⬝ Системные службы',
                  'auditd-f':'1.7.4.UU.1 ⬝ Системные службы',
                  'auditd-u':'1.7.4.UU.1 ⬝ Системные службы',
                  'syslog-ng':'1.7.4.UU.1 ⬝ Системные службы',
                  'RAM-overflow':'1.7.4.UU.1 ⬝ Системные службы',
                  'SD-overflow':'1.7.4.UU.1 ⬝ Системные службы',
                  'unix':'1.7.4.UU.1 ⬝ UnixBench'
                 },
    '1.7.4':{'postgresql':'PostgreSQL',
             'postgresql-sm':'PostgreSQL',
             'postgresql-aud-off':'PostgreSQL',
             'XFS':'Файловые системы',
             'NTFS':'Файловые системы',
             'EXT4':'Файловые системы',
             'EXT4 parsec':'Файловые системы',
             'auditd-p':'1.7.4 ⬝ Системные службы',
             'auditd-f':'1.7.4 ⬝ Системные службы',
             'auditd-u':'1.7.4 ⬝ Системные службы',
             'syslog-ng':'1.7.4 ⬝ Системные службы',
             'RAM-overflow':'1.7.4 ⬝ Системные службы',
             'SD-overflow':'1.7.4 ⬝ Системные службы',
             'unix':'UnixBench'
            },
    '1.7.2':{'postgresql':'1.7.2 ⬝ PostgreSQL',
             'postgresql-sm':'1.7.2 ⬝ PostgreSQL',
             'postgresql-aud-off':'1.7.2 ⬝ PostgreSQL',
             'auditd-p':'1.7.2 ⬝ Системные службы',
             'auditd-f':'1.7.2 ⬝ Системные службы',
             'auditd-u':'1.7.2 ⬝ Системные службы',
             'syslog-ng':'1.7.2 ⬝ Системные службы',
             'XFS':'1.7.2 ⬝ Файловые системы',
             'NTFS':'1.7.2 ⬝ Файловые системы',
             'EXT4':'1.7.2 ⬝ Файловые системы',
             'EXT4 parsec':'1.7.2 ⬝ Файловые системы',
             'RAM-overflow':'1.7.2 ⬝ Системные службы',
             'SD-overflow':'1.7.2 ⬝ Системные службы',
             'unix':'1.7.2 ⬝ UnixBench'
            },
    '1.7.3':{'postgresql':'1.7.3 ⬝ PostgreSQL',
             'postgresql-sm':'1.7.3 ⬝ PostgreSQL',
             'postgresql-aud-off':'1.7.3 ⬝ PostgreSQL',
             'auditd-p':'1.7.3 ⬝ Системные службы',
             'auditd-f':'1.7.3 ⬝ Системные службы',
             'auditd-u':'1.7.3 ⬝ Системные службы',
             'syslog-ng':'1.7.3 ⬝ Системные службы',
             'XFS':'1.7.3 ⬝ Файловые системы',
             'NTFS':'1.7.3 ⬝ Файловые системы',
             'EXT4':'1.7.3 ⬝ Файловые системы',
             'EXT4 parsec':'1.7.3 ⬝ Файловые системы',
             'RAM-overflow':'1.7.3 ⬝ Системные службы',
             'SD-overflow':'1.7.3 ⬝ Системные службы',
             'unix':'1.7.3 ⬝ UnixBench'
            },
    '1.7.3.UU.2':{'XFS':'1.7.3.UU.2 ⬝ Файловые системы',
                  'NTFS':'1.7.3.UU.2 ⬝ Файловые системы',
                  'EXT4':'1.7.3.UU.2 ⬝ Файловые системы',
                  'EXT4 parsec':'1.7.3.UU.2 ⬝ Файловые системы',
                  'postgresql':'1.7.3.UU.2 ⬝ PostgreSQL',
                  'postgresql-sm':'1.7.3.UU.2 ⬝ PostgreSQL',
                  'postgresql-aud-off':'1.7.3.UU.2 ⬝ PostgreSQL',
                  'auditd-p':'1.7.3.UU.2 ⬝ Системные службы',
                  'auditd-f':'1.7.3.UU.2 ⬝ Системные службы',
                  'auditd-u':'1.7.3.UU.2 ⬝ Системные службы',
                  'syslog-ng':'1.7.3.UU.2 ⬝ Системные службы',
                  'RAM-overflow':'1.7.3.UU.2 ⬝ Системные службы',
                  'SD-overflow':'1.7.3.UU.2 ⬝ Системные службы',
                  'unix':'1.7.3.UU.2 ⬝ UnixBench'
                 },
    '1.7.3.UU.1':{'postgresql':'1.7.3 UU1 ⬝ PostgreSQL',
                  'postgresql-sm':'1.7.3 UU1 ⬝ PostgreSQL',
                  'postgresql-aud-off':'1.7.3 UU1 ⬝ PostgreSQL',
                  'XFS':'1.7.3 UU1 ⬝ Файловые системы',
                  'NTFS':'1.7.3 UU1 ⬝ Файловые системы',
                  'EXT4':'1.7.3 UU1 ⬝ Файловые системы',
                  'EXT4 parsec':'1.7.3 UU1 ⬝ Файловые системы',
                  'auditd-p':'1.7.3 UU1 ⬝ Системные службы',
                  'auditd-f':'1.7.3 UU1 ⬝ Системные службы',
                  'auditd-u':'1.7.3 UU1 ⬝ Системные службы',
                  'syslog-ng':'1.7.3 UU1 ⬝ Системные службы',
                  'RAM-overflow':'1.7.3 UU1 ⬝ Системные службы',
                  'SD-overflow':'1.7.3 UU1 ⬝ Системные службы',
                  'unix':'1.7.3.UU.1 ⬝ UnixBench'
                 },
    '1.7.1':{'postgresql':'1.7.1 ⬝ PostgreSQL',
             'postgresql-sm':'1.7.1 ⬝ PostgreSQL',
             'postgresql-aud-off':'1.7.1 ⬝ PostgreSQL',
             'XFS':'1.7.1 ⬝ Файловые системы',
             'NTFS':'1.7.1 ⬝ Файловые системы',
             'EXT4':'1.7.1 ⬝ Файловые системы',
             'EXT4 parsec':'1.7.1 ⬝ Файловые системы',
             'syslog-ng':'Системные службы',
             'auditd-p':'Системные службы',
             'auditd-f':'Системные службы',
             'auditd-u':'Системные службы',
             'RAM-overflow':'Системные службы',
             'SD-overflow':'Системные службы',
             'unix':'1.7.1 ⬝ UnixBench'
            }
}



#################################################################################################################################################
#Ветки проектов в git
#################################################################################################################################################
branches = {
    'file system benchmark. EXT4':'file_systems',
    'file system benchmark. XFS':'file_systems',
    'postgresql benchmark':'postgresql',
    'postgresql benchmark smol':'postgresql',
    'postgresql benchmark audit-off':'postgresql',
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
    'ram overflow':'overflow',
    'storage drive overflow':'overflow'
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
    '1.7.5.4':'3056',
    '1.7.5.5':'3760'
}



#################################################################################################################################################
#Конвертируемый перечень тестов
#################################################################################################################################################
tests = {
    'file system benchmark. EXT4':'EXT4',
    'file system benchmark. XFS':'XFS',
    'postgresql benchmark':'postgresql',
    'postgresql benchmark smol':'postgresql-sm',
    'postgresql benchmark audit-off':'postgresql-aud-off',
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
    'storage drive overflow':'SD-overflow',
    'ram overflow':'RAM-overflow'
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
main_tests = ['XFS', 'EXT4', 'NTFS', 'EXT4 parsec', 'postgresql', 'postgresql-sm', 'auditd-p', 'auditd-u', 
              'auditd-f', 'syslog-ng', 'unix', 'postgresql-aud-off', 'SD-overflow', 'RAM-overflow', 'XFS parsec']



#################################################################################################################################################
#Перечень тестов БРЕСТ
#################################################################################################################################################
brest_tests = ['apache-graph']



#################################################################################################################################################
#Доступные релизы (следует указывать при наличии снимка в Clonezilla)
#################################################################################################################################################
releases = ['1.7.1', '1.7.2', '1.7.3', '1.7.3.UU.1', '1.7.3.UU.2', '1.7.4', '1.7.4.UU.1', '1.7.5', '1.7.5.4', '1.7.5.5']



#################################################################################################################################################
#Перечень доступных ядер
#################################################################################################################################################
kernels = ['5.10.0-1057-generic', '5.10.142-1-generic', '5.15.0-33-generic', '5.15.0-33-lowlatency', 
           '5.10.176-1-generic', '5.15.0-70-generic', '5.15.0-70-lowlatency', '5.10.190-1-generic',
           '5.15.0-83-generic', '5.15.0-83-lowlatency','6.1.50-1-generic']



#################################################################################################################################################
#Перечень стендов, отображаемых на разных страницах
#################################################################################################################################################
main_stands = ['stand1', 'stand2', 'stand3', 'stand4']
mobile_stands = ['stand1', 'stand2', 'stand3', 'stand4']
brest_stands = ['stand10', 'stand11', 'stand12']




#################################################################################################################################################
#Перечень настроек, используемых для создания тестовых прогонов
#################################################################################################################################################
testing17_pkg = 'http://qa111.devos.astralinux.ru/astra/testing/1.7-testing/base-repository/dists/1.7_x86-64/main/binary-amd64/Packages'
testing17_vers = 'http://qa111.devos.astralinux.ru/astra/testing/1.7-testing/base-repository/dists/1.7_x86-64/Release'
rc_list = ['1.7.5.4', '1.7.5.5']
releases_list = ['1.7.1', '1.7.2', '1.7.3', '1.7.3.UU.1', '1.7.3.UU.2', '1.7.4', '1.7.4.UU.1', '1.7.5']
testcase_orel = ['EXT4', 'NTFS', 'XFS', 'postgresql-aud-off', 'postgresql', 'syslog-ng', 'unix']
testcase_orel_stand2 = ['EXT4', 'NTFS', 'XFS', 'syslog-ng', 'unix', 'RAM-overflow', 'SD-overflow']
testcase_smolensk = ['postgresql-sm', 'EXT4 parsec', 'XFS parsec', 'auditd-f', 'auditd-p', 'auditd-u']
testcase_smolensk_stand2 = ['EXT4 parsec', 'XFS parsec', 'auditd-f', 'auditd-p', 'auditd-u']
test_run_stands = [f'stand{x}' for x in range(1, 5, 1)]
test_run_modes = ['orel', 'smolensk']
tests_case_zefir_key = {
    'postgresql':'BT-T7555',
    'postgresql-aud-off':'BT-T9169',
    'postgresql-sm':'BT-T9106',
    'EXT4':'BT-T7562',
    'EXT4 parsec':'BT-T8822',
    'NTFS':'BT-T7564',
    'XFS':'BT-T8069',
    'XFS parsec':'BT-T9488',
    'unix':'BT-T8737',
    'RAM-overflow':'BT-T9170',
    'SD-overflow':'BT-T9172',
    'auditd-f':'BT-T8215',
    'auditd-p':'BT-T8213',
    'auditd-u':'BT-T8214',
    'syslog-ng':'BT-T8119'
}

