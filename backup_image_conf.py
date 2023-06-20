

stands_ip = {
    'stand1':'10.177.103.201',
    'stand2':'10.177.103.202',
    'stand3':'10.177.103.203',
    'stand4':'10.177.103.204'
}


modes = {
    'orel':'0',
    'smolensk':'2'
}

parent_page_list = {
    '1.7.4':{'postgresql':'PostgreSQL',
             'XFS':'Файловые системы',
             'NTFS':'Файловые системы',
             'EXT4':'Файловые системы',
             'EXT4 parsec':'Файловые системы'
            },
    '1.7.3':{'postgresql':'1.7.3 ⬝ PostgreSQL'
            },
    '1.7.3.UU.2':{'XFS':'1.7.3.UU.2 ⬝ Файловые системы',
                  'NTFS':'1.7.3.UU.2 ⬝ Файловые системы',
                  'EXT4':'1.7.3.UU.2 ⬝ Файловые системы',
                  'EXT4 parsec':'1.7.3.UU.2 ⬝ Файловые системы',
                  'postgresql':'1.7.3.UU.2 ⬝ PostgreSQL',
                  'postgresql_sm':'1.7.3.UU.2 ⬝ PostgreSQL',
                  'auditd_p':'1.7.3.UU.2 ⬝ Системные службы',
                  'auditd_f':'1.7.3.UU.2 ⬝ Системные службы',
                  'auditd_u':'1.7.3.UU.2 ⬝ Системные службы',
                  'syslog_ng':'1.7.3.UU.2 ⬝ Системные службы'
                 },
    '1.7.3.UU.1':{'postgresql':'1.7.3 UU1 ⬝ PostgreSQL',
                  'XFS':'1.7.3 UU1 ⬝ Файловые системы',
                  'NTFS':'1.7.3 UU1 ⬝ Файловые системы',
                  'EXT4':'1.7.3 UU1 ⬝ Файловые системы',
                  'EXT4 parsec':'1.7.3 UU1 ⬝ Файловые системы'
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
    'syslog-ng benchmark':'syslog_ng'
}


cycle_tree_index = {
    '1.7.4':'2773',
    '1.7.3':'2774',
    '1.7.3.UU.1':'2745',
    '1.7.3.UU.2':'2808',
    '1.7.4.UU.1':'2936'
}

tests = {
    'file system benchmark. EXT4':'EXT4',
    'file system benchmark. XFS':'XFS',
    'postgresql benchmark':'postgresql',
    'postgresql benchmark smol':'postgresql_sm',
    'file system benchmark. OCFS2':'OCFS2',
    'file system benchmark. NTFS':'NTFS',
    'file system benchmark. EXT3':'EXT3',
    'file system benchmark. EXT2':'EXT2',
    'file system benchmark. Fat32':'Fat32',
    'file system benchmark. EXT4 parsec':'EXT4 parsec',
    'file system benchmark. OCFS2 parsec':'OCFS2 parsec',
    'auditd benchmark. psaud':'auditd_p',
    'auditd benchmark. fileaud':'auditd_f',
    'auditd benchmark. useraud':'auditd_u',
    'syslog-ng benchmark':'syslog-ng'
}

