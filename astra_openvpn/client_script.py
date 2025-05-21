from allta import SystemCommands

s = SystemCommands()
for i in range(100):
    s.cmd(f"sudo astra-openvpn-server client tester{i}")
