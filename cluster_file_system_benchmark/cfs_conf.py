# astra qa stand
HOSTS = { 'sudcm': { 'ip': '10.0.0.21',
                     'full_name': 'sudcm.rtfm.rbt',
                     'short_name': 'sudcm',
                     'port': 2021
                     },
          'sufs': { 'ip': '10.0.0.22',
                    'full_name': 'sufs.rtfm.rbt',
                    'short_name': 'sufs',
                    'port': 2022
                    },
          'susrv': { 'ip': '10.0.0.23',
                     'full_name': 'susrv.rtfm.rbt',
                     'short_name': 'susrv',
                     'port': 2023
                     },
          'sudcs': { 'ip': '10.0.0.24',
                     'full_name': 'sudcs.rtfm.rbt',
                     'short_name': 'sudcs',
                     'port': 2024
                     },
          'suac': { 'ip': '10.0.0.25',
                    'full_name': 'suac.rtfm.rbt',
                    'short_name': 'suac',
                    'port': 2025
                    },
          'fidcm': { 'ip': '10.0.20.20',
                    'full_name': 'sudcm.ipa.rbt',
                    'short_name': 'sudcm',
                    'port': 2026
                    },
          'fidcr1': { 'ip': '10.0.20.21',
                    'full_name': 'sudcr1.ipa.rbt',
                    'short_name': 'sudcr1',
                    'port': 2027
                    },
          'fidcr2': { 'ip': '10.0.20.22',
                    'full_name': 'sudcr2.ipa.rbt',
                    'short_name': 'sudcr2',
                    'port': 2028
                    },
          'fisrv': { 'ip': '10.0.20.23',
                    'full_name': 'susrv.ipa.rbt',
                    'short_name': 'susrv',
                    'port': 2029
                    },
          'fiac': { 'ip': '10.0.20.30',
                    'full_name': 'suac.ipa.rbt',
                    'short_name': 'suac',
                    'port': 2030
                    },
          }

# physical stand
# ...

SCRIPT_DIR = '/media/sf_git/stress_test/cluster_file_system_benchmark'
#SCRIPT_DIR = '/home/rkuznetsov/git/stress_test/cluster_file_system_benchmark'
LOG_FILENAME = 'cfs_log'
LOG_PATH ='{}/{}'.format(SCRIPT_DIR, LOG_FILENAME)
REPORT_FILENAME = 'cfs_report.txt'
REPORT_PATH = '{}/report'.format(SCRIPT_DIR)
TEMPLATE_PATH = '{}/templates'.format(SCRIPT_DIR)

# Количество Inode
INODE_COUNT = '-N 255'
#
STORAGE_NAME = 'sdb'
#
STORAGE_MOUNT_DIR = '/mnt'
#
MACHINE_POSTFIX = 'osse'
#
SNAPSHOT_NAME = '1.7.1.o'
#
PORT = '7777'
#
USER = 'u'
#
PASSWORD = '1'

START_BORDER_FOR_DATA = 1000
STEP_FOR_DATA = 1000
END_BORDER_FOR_DATA = 100000
TIMEOUT = 3600
NUMBER_OF_TEST_FILES = 5000

FILES = 10
FILES_STEP = 10
FILES_LIMIT = 100

SIZE = 1024
SIZE_STEP = 1024
SIZE_LIMIT = 10240