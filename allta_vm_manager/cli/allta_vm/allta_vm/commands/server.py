from allta import SystemCommands
import json

class Server():

    def __install_deps():
        base_commands = "sudo apt-get update && sudo apt-get install -y "
        deps = f"build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev  \
                libreadline-dev libffi-dev libsqlite3-dev wget libbz2-dev libffi-dev strace \
                python3-requests sshpass tar wget bridge-utils "
        version_os = SystemCommands.check_output_command("cat /etc/astra_version")
        if version_os.startswith("1.8"):
            deps = deps + "linux-tools-6.1*-generic linux-tools-6.6*-generic "
        if version_os.startswith("1.7"):
            deps = deps + f"linux-tools-5.10*-generic linux-tools-5.15*-generic linux-tools-common-5.15* \
                            linux-tools-5.15*-lowlatency libssl1.1 psmisc"
            
        SystemCommands.cmd_with_returncode(base_commands + deps)

    # def __python():
        
    #     path = "/home/u/python"
    #     python = "Python-3.12.1"
    #     # Create Path
    #     SystemCommands.cmd_with_returncode(f"sudo mkdir {path}")

    #     # Get Python
    #     SystemCommands.cmd_with_returncode(f"cd {path} && sudo wget -P /home/u/python ftp://10.177.103.10/python/*")

    #     # Unpack Python
    #     SystemCommands.cmd_with_returncode(f"cd {path} && sudo tar -xf {python}.tar.xz")

    #     # Install Python 
    #     SystemCommands.cmd_with_returncode(f"cd {path}/{python} && ./configure --enable-optimizations && make -j 6 && sudo make altinstall")

    #     # Create venv
    #     SystemCommands.cmd_with_returncode(f"cd {path}/{python} && python3.12 -m venv venv")
    #     pass
    
    def __net(phy_if, ip):
        bridge = "br0"
        phy_if = phy_if

        # Create backup net settings
        SystemCommands.cmd_with_returncode('sudo cp /etc/network/interfaces /etc/network/interfaces.bak')

        # Schedule file

        schedule = f"""sudo tee /etc/network/interfaces > /dev/null <<EOF
auto lo
iface lo inet loopback

auto {bridge}
iface {bridge} inet static
    address {ip}
    netmask 255.255.255.0
    gateway 10.177.103.254
    dns-nameservers 10.177.128.198 10.177.180.246 10.177.181.142
    bridge_ports {phy_if}
    bridge_stp off
    bridge_fd 0
    bridge_maxwait 0

iface {phy_if} inet manual
EOF

""" 
        # Rework interface
        SystemCommands.cmd_with_returncode(schedule)

        # Restart net
        # commad = f"sudo systemctl restart networking"
        command = f"sudo ifdown {phy_if} || true && sudo ifdown {bridge} || true && sudo ifup {bridge}"
        SystemCommands.cmd_with_returncode(command)

    def server_init(phy_if, ip):
        Server.__install_deps()
        Server.__net(phy_if=phy_if, ip=ip)