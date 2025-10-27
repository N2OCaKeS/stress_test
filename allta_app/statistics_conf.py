statistics_conf = {
    "Apache": {
        "set_of_test_types": ['apache-rp']
    },
    "FreeIPA": {
        "set_of_test_types": ['FreeIPA auth']
    },
    "Parsec": {
        "set_of_test_types": ['parsec impact-fs', 'parsec impact-fs aud-off'],
        "comparison_list": [["parsec impact-fs", "parsec impact-fs aud-off"]]
    },
    "PostgreSQL": {
        "set_of_test_types": ['postgresql', 'postgresql-sm', 'postgresql-aud-off', 'psql parsec', 'psql vanilla', 'tantor vanilla', 'psql balance'],
        "comparison_list": [['postgresql', 'postgresql-sm'], ['postgresql', 'postgresql-aud-off'], ['postgresql', 'psql parsec'], ['postgresql', 'psql vanilla']],
        "comparison_kernel_list": ['postgresql']
    },
    "Qemu/KVM/Libvirt": {
        "set_of_test_types": ["FIO", "vPingPong", "vUnixBench", "steal time", "steal time-sm"],
        "comparison_list": [["steal time", "steal time-sm"]]
    },
    "UnixBench": {
        "set_of_test_types": ['unix', 'unix parsec'],
        "comparison_list": [["unix", "unix parsec"]]
    },
    "Системные службы": {
        "set_of_test_types": ['auditd-p', 'auditd-f', 'auditd-u', 'syslog-ng']
    },
    "Файловые системы": {
        "set_of_test_types": ['EXFAT', 'EXT2', 'EXT4', 'EXT4 parsec', 'FAT', 'NTFS', 'XFS', 'XFS parsec', 'OCFS2', 'CEPH', 'CEPH fio'],
        "comparison_list": [['EXT4', 'XFS'], ['EXT4', 'EXT4 parsec']]
    }
}