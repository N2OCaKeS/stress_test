import sys
import pexpect

child = pexpect.spawn('sudo', ['drblsrv', '-i'], env={'LC_ALL': 'C.UTF-8'})

child.expect(r'Do you want to install the network installation boot images.*\?.*\[y/N\]')
child.logfile = sys.stdout.buffer
child.sendline('N')

child.expect(r'Do you want to use the serial console output on the client computer.*\?.*\[y/N\]')
child.sendline('N')
child.expect(r'Do you want to upgrade the operating system\?.*\[y/N\]', timeout=100)
child.sendline('N')
child.expect(r'\[1\]:.*kernel.*from this DRBL server.*', timeout=300)
child.sendline('1')
try:
    child.expect(pexpect.EOF, timeout=600)
except pexpect.TIMEOUT:
    child.close(force=True)