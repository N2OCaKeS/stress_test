
ADMIN='u'

PROC_BODYS = {'open': ('cat /etc/passwd', 'cat happy_future 2> /dev/null'),
              'create': ('touch /tmp/file1', 'touch /file1 2> /dev/null'),
              'exec': ('/bin/true', '/bin/ls 2> /dev/null'),
              'remove': ('rm /tmp/file1', 'rm /tmp/file1 2> /dev/null'),
              'chmod': ('chmod 700 /tmp/file1', 'chmod 700 /tmp/file1 2> /dev/null'),
              'chown': ('chown :users /tmp/file1', 'chown :users /tmp/file1 2> /dev/null'),
              'mount': ('mount --bind /tmp/dir1 /mnt/', 'sudo mount dir /mnt/ 2> /dev/null'),
              'module': ('modprobe 8021q', 'insmod /home/'+ADMIN+'/superdebug.ko 2> /dev/null'),
              'uid': ('sudo -u ' + ADMIN + ' /bin/true', 'setpriv --reuid 0 bash 2> /dev/null'),
              'gid': ('sudo -u ' + ADMIN + ' /bin/true', 'setpriv --regid 0 --groups 0 bash 2> /dev/null'),
              'acl': ('setfacl -m u:' + ADMIN + ':rx /tmp/dir1', 'setfacl -m u:'+ADMIN+':rx /tmp/dir1 2> /dev/null'),
              'mac': ('pdpl-file 0:63:0:ccnri /dir1', 'pdpl-file 5:63:0:ccnri /tmp/dir1 2> /dev/null'),
              'cap': ('usercaps -l 0x1 u', 'pscaps $(echo $$) 0x1 2> /dev/null'),
              'chroot': ('chroot /', '/usr/sbin/chroot /tmp/ 2> /dev/null'),
              'rename': ('mv /tmp/file1 /tmp/file2', 'mv /tmp/file1 /tmp/file2 2> /dev/null'),
              'net': ('ping -c 1 localhost', 'ping6 -c 1 localhost')}