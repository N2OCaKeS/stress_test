COMPONENTS_INSTALL = ['ssh', 'git', 'parted', 'sysstat', 'resolvconf', 'wget']

CLONE_GIT_REPO = "mkdir /home/u/git; cd /home/u/git && git clone -c http.extraHeader='Authorization: Bearer BBDC-NzMzODEwODg1MzE1OjhBlCOIYgAAghiwzrUlhVaUCVsU' https://git.astralinux.ru/scm/qa/stress_test.git"

COMMAND_WGET_GITCLONE_FILE = "cd /home/u/git && wget ftp://10.177.5.111/stress_reports/stress_test_config/git_clone.py && sudo chmod +x git_clone.py"

COMMAND_WGET_QAINIT_FILE = "cd /home/u && wget ftp://10.177.5.111/qa-init && sudo chmod +x qa-init"

NETWORK_SETTINGS_TEMPLATE = """
# This file describes the network interfaces available on your system
# and how to activate them. For more information, see interfaces(5).

source /etc/network/interfaces.d/*

# The loopback network interface
auto lo
iface lo inet loopback

auto {interface}
iface {interface} inet static
        address {ip_address}
        netmask 255.255.255.0
        gateway 10.177.103.254
        dns-nameserver 10.177.128.198

dns-nameservers 10.177.128.198
"""