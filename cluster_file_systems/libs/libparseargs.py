import argparse

def parse_args():
    
    DESCRIPTION = ""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument('-u', '--username',
                        action='store',
                        required=True,
                        help='confluence user',
                        dest='USER')

    parser.add_argument('-t', '--token',
                        action='store',
                        required=False,
                        default=None,
                        help='confluence access token',
                        dest='TOKEN')

    parser.add_argument('-cs', '--confluence-space',
                        action='store',
                        required=True,
                        help='confluence space',
                        dest='SPACE')

    parser.add_argument('-cpp', '--confluence-parent-page',
                        action='store',
                        required=True,
                        help='confluence parent page',
                        dest='PPAGE')

    parser.add_argument('-cnp', '--confluence-new-page',
                        action='store',
                        required=True,
                        help='confluence new page',
                        dest='NPAGE')

    parser.add_argument('-sn', '--stand-num',
                        action='store',
                        choices=['1',
                                '2',
                                '3',
                                '4',
                                '5',
                                '6',
                                '7',
                                '8',
                                '9',
                                '10',
                                '11',
                                '12',
                                '13'],
                        required=True,
                        help='stand num',
                        dest='STAND')

    parser.add_argument('-fti', '--folder-tree-id',
                        action='store',
                        required=True,
                        help='folder-tree-id',
                        dest='FTI')

    parser.add_argument('-tcyc', '--test-cycle-name',
                        action='store',
                        required=True,
                        help='test-cycle-name',
                        dest='TCYC')

    parser.add_argument('-tcas', '--test-case-name',
                        action='store',
                        required=True,
                        help='test-case-name',
                        dest='TCAS')

    parser.add_argument('-ba', '--basic-auth',
                        action='store',
                        required=True,
                        help='basic-auth',
                        dest='BA')

    parser.add_argument('-tcv', '--test-cycle-version',
                        action='store',
                        required=True,
                        help='test-cycle-version',
                        dest='TCV')
    
    parser.add_argument('--disk-size',
                        action='store',
                        required=False,
                        type=str,
                        default='25',
                        help='size of vdi disk',
                        dest='DISK_SIZE')

    parser.add_argument('--test-set',
                        action='store',
                        choices=['base_load',
                                 'timeout',
                                 'multithreaded',
                                 'big_files',
                                 'fs_mark_count',
                                 'fs_mark_size',
                                 'fio'],
                        default='fs_mark_count',
                        required=False,
                        dest='TS')
    
    parser.add_argument('-vbox', 
                        action='store',
                        required=True,
                        help='vbox name',
                        dest='VBOX')

    parser.add_argument('-kernel', 
                        action='store',
                        required=True,
                        help='vbox name',
                        dest='KERNEL')
    
    parser.add_argument('--multithreading',
                        action='store_true',
                        required=False,
                        help='get data from config',
                        dest='MULTITHREADING')

    parser.add_argument('--parsec',
                        action='store_true',
                        required=False,
                        help='',
                        dest='PARSEC')
    
    parser.add_argument('-fs',
                        action='store',
                        choices=['ocfs2', 'gfs2'],
                        required=False,
                        help='filesystem',
                        dest='FS')
    
    parser.add_argument('--libvirt',
                        action='store_true',
                        required=False,
                        help='virtualization type',
                        dest='LIBVIRT')
    
    parser.add_argument('--cfs',
                        action='store',
                        choices=['ceph','ocfs2'],
                        default='ocfs2',
                        required=False,
                        dest='CFS')

    return parser.parse_args()