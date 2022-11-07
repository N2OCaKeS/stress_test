SCRIPT_DIR = '/media/sf_git/stress_test/auditd_benchmark'

LOG_FILENAME = 'aub_log'
REPORT_FILENAME = 'aub_report.txt'

LOG_DIR = '{}/log'.format(SCRIPT_DIR)
REPORT_DIR = '{}/report'.format(SCRIPT_DIR)
TEMPLATE_DIR = '{}/templates'.format(SCRIPT_DIR)

LOG = '{}/{}'.format(LOG_DIR, LOG_FILENAME)
REPORT = '{}/{}'.format(REPORT_DIR, REPORT_FILENAME)

LATENCY_REPORT_PSAUD = '{}/aub_ps_report_latency.txt'.format(REPORT_DIR)
LATENCY_REPORT_USAUD = '{}/aub_us_report_latency.txt'.format(REPORT_DIR)
LATENCY_REPORT_FLAUD = '{}/aub_fl_report_latency.txt'.format(REPORT_DIR)

LOSSES_REPORT_PSAUD = '{}/aub_ps_report_losses.txt'.format(REPORT_DIR)
LOSSES_REPORT_USAUD = '{}/aub_us_report_losses.txt'.format(REPORT_DIR)
LOSSES_REPORT_FLAUD = '{}/aub_fl_report_losses.txt'.format(REPORT_DIR)

MAIN_USER='u'
TEST_USER='tester'

PSAUD_PROC_BODYS = {# 'open': ('cat /etc/passwd', ''),
              # 'create': ('touch /tmp/file0', ''),
              # 'exec': ('/bin/true', ''),
              'remove': ('rm /tmp/file0', ''),
              # 'chmod': ('chmod 700 /tmp/file0', ''),
              # 'chown': ('chown :users /tmp/file0', ''),
              # 'mount': ('mount --bind /tmp/dir0 /mnt/', ''),
              # 'module': ('modprobe 8021q', ''),
              # 'uid': ('sudo -u {} /bin/true'.format(MAIN_USER), ''),
              # 'gid': ('sudo -u {} /bin/true'.format(MAIN_USER), ''),
              # 'acl': ('setfacl -m u:{}:rx /tmp/dir0'.format(MAIN_USER), ''),
              # 'mac': ('pdpl-file 0:63:0:ccnri /dir0', ''),
              # 'cap': ('usercaps -l 0x1 u', ''),
              # 'chroot': ('chroot /', ''),
              # 'rename': ('mv /tmp/file0 /tmp/file1', ''),
              # 'net': ('ping -c 1 localhost', '')
                    }


USERAUD_PROC_BODYS = { 'open': ('cat /etc/passwd',''),
                       'create': ('touch /tmp/file1',''),
                       'exec': ('/bin/true',''),
                       'delete': ('rm /tmp/file1',''),
                       'chmod': ('chmod 700 /tmp/file1',''),
                       'chown': ('chown :users /tmp/file1',''),
                       'mount': ('mount --bind /tmp/dir1 /mnt/',''),
                       'module': ('/sbin/modprobe evbug',''),
                       'uid': ('',''),
                       'gid': ('',''),
                       'audit': ('/usr/sbin/setfaud -m o:o:o /root',''),
                       'acl': ('setfacl -m u:{}:rx /tmp/dir1'.format(TEST_USER), ''),
                       'mac': ('/usr/sbin/pdpl-file 2:0:0 /home/{}/test_mac'.format(TEST_USER),''),
                       'cap': ('pscaps 0 0x1',''),
                       # 'chroot': ('usercaps -l 0x1 u',''),
                       'rename': ('mv /tmp/file1 /tmp/file2',''),
                       'net': ('ping -c 1 localhost','')}

FILEAUD_PROC_BODYS = { 'open': ('cat ', ''),  # +
                       'create': ('touch ', ''),  # +
                       'exec': ('', ''),  # + только при запуске черезу шебанг :(
                       'delete': ('rm -f ', ''),  # +
                       'chmod': ('chmod 777 ', ''),  # +
                       'chown': ('chown u:u ', ''),  # +
                       'audit': ('setfaud -m u:0:+exec ', ''),  # +
                       'acl': ('setfacl -m u:u:rw ', ''),  # +
                       #'mac': ('pdpl-file 2:0:0 ', ''),
                       'modify': ("echo '1' >> ", '')  # +
                        }


PS_LOWER_LIMIT = 1
PS_UPPER_LIMIT = 2
PS_STEP = 1

DEFAULT_PS_LIFETIME = 10
DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY = 0.1
