

stands_ip = {
    'stand1':'10.177.103.201',
    'stand2':'10.177.103.202',
    'stand3':'10.177.103.203',
    'stand4':'10.177.103.204'
}


modes = {
    'orel':'0',
    'smolensk':'2',
    '10':'10'
}

parent_page_list = {
    'debian':{'postgresql':'Debian ⬝ PostgreSQL',
              'postgresql-sm':'Debian ⬝ PostgreSQL'
             },
    '1.7.4.UU.1':{'postgresql':'1.7.4.UU.1 ⬝ PostgreSQL',
                  'postgresql-sm':'1.7.4.UU.1 ⬝ PostgreSQL',
                  'XFS':'1.7.4.UU.1 ⬝ Файловые системы',
                  'NTFS':'1.7.4.UU.1 ⬝ Файловые системы',
                  'EXT4':'1.7.4.UU.1 ⬝ Файловые системы',
                  'EXT4 parsec':'1.7.4.UU.1 ⬝ Файловые системы',
                  'auditd-p':'1.7.4.UU.1 ⬝ Системные службы',
                  'auditd-f':'1.7.4.UU.1 ⬝ Системные службы',
                  'auditd-u':'1.7.4.UU.1 ⬝ Системные службы',
                  'syslog-ng':'1.7.4.UU.1 ⬝ Системные службы'
                 },
    '1.7.4':{'postgresql':'PostgreSQL',
             'postgresql-sm':'PostgreSQL',
             'XFS':'Файловые системы',
             'NTFS':'Файловые системы',
             'EXT4':'Файловые системы',
             'EXT4 parsec':'Файловые системы',
             'auditd-p':'1.7.4 ⬝ Системные службы',
             'auditd-f':'1.7.4 ⬝ Системные службы',
             'auditd-u':'1.7.4 ⬝ Системные службы',
             'syslog-ng':'1.7.4 ⬝ Системные службы',
             'unix':'UnixBench'
            },
    '1.7.2':{'postgresql':'1.7.2 ⬝ PostgreSQL',
             'postgresql-sm':'1.7.2 ⬝ PostgreSQL',
             'auditd-p':'1.7.2 ⬝ Системные службы',
             'auditd-f':'1.7.2 ⬝ Системные службы',
             'auditd-u':'1.7.2 ⬝ Системные службы',
             'syslog-ng':'1.7.2 ⬝ Системные службы',
             'XFS':'1.7.2 ⬝ Файловые системы',
             'NTFS':'1.7.2 ⬝ Файловые системы',
             'EXT4':'1.7.2 ⬝ Файловые системы',
             'EXT4 parsec':'1.7.2 ⬝ Файловые системы'
            },
    '1.7.3':{'postgresql':'1.7.3 ⬝ PostgreSQL',
             'postgresql-sm':'1.7.3 ⬝ PostgreSQL',
             'auditd-p':'1.7.3 ⬝ Системные службы',
             'auditd-f':'1.7.3 ⬝ Системные службы',
             'auditd-u':'1.7.3 ⬝ Системные службы',
             'syslog-ng':'1.7.3 ⬝ Системные службы',
             'XFS':'1.7.3 ⬝ Файловые системы',
             'NTFS':'1.7.3 ⬝ Файловые системы',
             'EXT4':'1.7.3 ⬝ Файловые системы',
             'EXT4 parsec':'1.7.3 ⬝ Файловые системы'
            },
    '1.7.3.UU.2':{'XFS':'1.7.3.UU.2 ⬝ Файловые системы',
                  'NTFS':'1.7.3.UU.2 ⬝ Файловые системы',
                  'EXT4':'1.7.3.UU.2 ⬝ Файловые системы',
                  'EXT4 parsec':'1.7.3.UU.2 ⬝ Файловые системы',
                  'postgresql':'1.7.3.UU.2 ⬝ PostgreSQL',
                  'postgresql-sm':'1.7.3.UU.2 ⬝ PostgreSQL',
                  'auditd-p':'1.7.3.UU.2 ⬝ Системные службы',
                  'auditd-f':'1.7.3.UU.2 ⬝ Системные службы',
                  'auditd-u':'1.7.3.UU.2 ⬝ Системные службы',
                  'syslog-ng':'1.7.3.UU.2 ⬝ Системные службы'
                 },
    '1.7.3.UU.1':{'postgresql':'1.7.3 UU1 ⬝ PostgreSQL',
                  'postgresql-sm':'1.7.3 UU1 ⬝ PostgreSQL',
                  'XFS':'1.7.3 UU1 ⬝ Файловые системы',
                  'NTFS':'1.7.3 UU1 ⬝ Файловые системы',
                  'EXT4':'1.7.3 UU1 ⬝ Файловые системы',
                  'EXT4 parsec':'1.7.3 UU1 ⬝ Файловые системы',
                  'auditd-p':'1.7.3 UU1 ⬝ Системные службы',
                  'auditd-f':'1.7.3 UU1 ⬝ Системные службы',
                  'auditd-u':'1.7.3 UU1 ⬝ Системные службы',
                  'syslog-ng':'1.7.3 UU1 ⬝ Системные службы'
                 },
    '1.7.1':{'postgresql':'1.7.1 ⬝ PostgreSQL',
             'postgresql-sm':'1.7.1 ⬝ PostgreSQL',
             'XFS':'1.7.1 ⬝ Файловые системы',
             'NTFS':'1.7.1 ⬝ Файловые системы',
             'EXT4':'1.7.1 ⬝ Файловые системы',
             'EXT4 parsec':'1.7.1 ⬝ Файловые системы',
             'syslog-ng':'Системные службы',
             'auditd-p':'Системные службы',
             'auditd-f':'Системные службы',
             'auditd-u':'Системные службы'
            }
}

branches = {
    'file system benchmark. EXT4':'file_systems',
    'file system benchmark. XFS':'file_systems',
    'postgresql benchmark':'postgresql',
    'postgresql benchmark smol':'postgresql',
    'file system benchmark. OCFS2':'file_systems',
    'file system benchmark. NTFS':'file_systems',
    'file system benchmark. EXT3':'file_systems',
    'file system benchmark. EXT2':'file_systems',
    'file system benchmark. Fat32':'file_systems',
    'file system benchmark. EXT4 parsec':'file_systems',
    'file system benchmark. OCFS2 parsec':'file_systems',
    'auditd benchmark. psaud':'auditd',
    'auditd benchmark. fileaud':'auditd',
    'auditd benchmark. useraud':'auditd',
    'syslog-ng benchmark':'syslog_ng',
    'linux_system_benchmark. UnixBench':'linux_system'
}


cycle_tree_index = {
    'debian':'2967',
    '1.7.4':'2773',
    '1.7.3':'2774',
    '1.7.3.UU.1':'2745',
    '1.7.3.UU.2':'2808',
    '1.7.4.UU.1':'2936',
    '1.7.2':'2937',
    '1.7.1':'2947'
}

tests = {
    'file system benchmark. EXT4':'EXT4',
    'file system benchmark. XFS':'XFS',
    'postgresql benchmark':'postgresql',
    'postgresql benchmark smol':'postgresql-sm',
    'file system benchmark. OCFS2':'OCFS2',
    'file system benchmark. NTFS':'NTFS',
    'file system benchmark. EXT3':'EXT3',
    'file system benchmark. EXT2':'EXT2',
    'file system benchmark. Fat32':'Fat32',
    'file system benchmark. EXT4 parsec':'EXT4 parsec',
    'file system benchmark. OCFS2 parsec':'OCFS2 parsec',
    'auditd benchmark. psaud':'auditd-p',
    'auditd benchmark. fileaud':'auditd-f',
    'auditd benchmark. useraud':'auditd-u',
    'syslog-ng benchmark':'syslog-ng',
    'linux_system_benchmark. UnixBench':'unix'
}
