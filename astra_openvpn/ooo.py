from allta import SystemCommands

sys_cls = SystemCommands()
#test_cls = Ovpn20k(vm_count=2)

test = sys_cls.check_output_command("ip a | grep 'inet 192.168.121'").split()[1].split("/")[0]
ip = sys_cls.check_output_command("cat /etc/hosts").split()[5] # ip testvm1

sys_cls.cmd(f'sed -i -E \"s|^[[:space:]]*remote\\b.*|remote 192.168.121.33 1194|\" /home/vagrant/openvpn/clients_keys/tester7/client.ovpn')


print(sys_cls.check_output_command("ip -4 addr show dev tun5 | grep inet").split()[1].split("/")[0])
print(ip)
