SCRIPT_DIR = '/media/sf_git/stress_test/auditd_benchmark'

LOG_FILENAME = 'aub_log'
REPORT_FILENAME = 'aub_report.txt'

LOG_DIR = '{}/log'.format(SCRIPT_DIR)
REPORT_DIR = '{}/report'.format(SCRIPT_DIR)
TEMPLATE_DIR = '{}/templates'.format(SCRIPT_DIR)

LOG = '{}/{}'.format(LOG_DIR, LOG_FILENAME)
REPORT = '{}/{}'.format(REPORT_DIR, REPORT_FILENAME)

TEST_USER='u'
PROC_BODYS = {'open': ('cat /etc/passwd', ''),
              'create': ('touch /tmp/file1', ''),
              'exec': ('/bin/true', ''),
              'remove': ('rm /tmp/file1', ''),
              'chmod': ('chmod 700 /tmp/file1', ''),
              'chown': ('chown :users /tmp/file1', ''),
              'mount': ('mount --bind /tmp/dir1 /mnt/', ''),
              'module': ('modprobe 8021q', ''),
              'uid': ('sudo -u {} /bin/true'.format(TEST_USER), ''),
              'gid': ('sudo -u {} /bin/true'.format(TEST_USER), ''),
              'acl': ('setfacl -m u:{}:rx /tmp/dir1'.format(TEST_USER), ''),
              'mac': ('pdpl-file 0:63:0:ccnri /dir1', ''),
              'cap': ('usercaps -l 0x1 u', ''),
              'chroot': ('chroot /', ''),
              'rename': ('mv /tmp/file1 /tmp/file2', ''),
              'net': ('ping -c 1 localhost', '')}