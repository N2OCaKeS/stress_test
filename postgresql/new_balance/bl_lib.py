from allta import Libvirt, SystemCommands
from new_balance.roles.database.db import DatabaseVM
from new_balance.roles.domain.domain import DomainVM
from new_balance.roles.load_balancer.load_balancer import LoadBalancer
from new_balance.roles.task.pre_configure import PreConfigure
from new_balance.roles.task.test import Test
from new_balance.roles.vm_info import VERSION_OS, VMS, VMS_DATES, PROVIDER


def balance(rc, sec_mode="s"):
    provider = PROVIDER
    new_vms_data = {}
    if isinstance(provider, Libvirt):
        provider.prepare()
        SystemCommands.cmd(
            'sudo sed -i \'s|#cgroup_controllers = \\[ "cpu", "devices", "memory", "blkio", "cpuset", "cpuacct" \\]|cgroup_controllers = [ "cpu", "devices", "memory" ]|\' /etc/libvirt/qemu.conf && sudo systemctl restart libvirtd'
        )

        if VERSION_OS == "1.7":
            new_vms_data = provider.build(box = f"1.7.5.{sec_mode}", rc=rc, vms=VMS, vms_dates=VMS_DATES)
        elif VERSION_OS == "1.8":
            new_vms_data =  provider.build(box = f"1.8.1.{sec_mode}", rc=rc, vms=VMS, vms_dates=VMS_DATES)

    VMS_DATES.update(new_vms_data)  # обновляем оригинальный словарь in-place, чтобы все модули увидели новые IP / update original dict in-place so all modules see new IPs
    provider.check(vms=VMS, vms_dates=VMS_DATES)

    configure = PreConfigure()  # Проверено работает
    configure.prepare()
    # if isinstance(provider, VBox):
    #     VBoxManager.create_snapshot(vms=VMS, snapshot_name="prepare")
    # elif isinstance(provider, Libvirt):
    #     LibvirtManager.create_snapshot(vms=VMS, snapshot_name="prepare")

    domain = DomainVM()  # Проверено работает
    domain.settings()

    database = DatabaseVM()  # Проверено работает
    database.settings()

    load_balancer = LoadBalancer()
    load_balancer.load()

    test = Test()
    test.test()
    SystemCommands.cmd("cat results_balance.txt")
