from time import sleep

from allta import VBoxManager, Libvirt, VBox, SystemCommands
from new_balance.roles.database.db import DatabaseVM
from new_balance.roles.domain.domain import DomainVM
from new_balance.roles.load_balancer.load_balancer import LoadBalancer
from new_balance.roles.task.pre_configure import PreConfigure
from new_balance.roles.task.test import Test
from new_balance.roles.vm_info import VERSION_OS, VMS, VMS_DATES, PROVIDER

def balance(rc, sec_mode = "s"):
    provider = PROVIDER
    if isinstance(provider, VBox):
        vagrant_path = '/home/u/git/stress_test/postgresql/new_balance/'
        prepare_path = '/home/u/git/stress_test/postgresql/new_balance/prepare/prepare.sh'
        provider.prepare(prepare_path)
        if VERSION_OS == '1.7':
            provider.build(vagrant_path, f'1.7.5.s', rc, VMS, VMS_DATES)
        elif VERSION_OS == '1.8':
            provider.build(vagrant_path, f'1.8.1.s', rc, VMS, VMS_DATES)
            SystemCommands.cmd('sudo mv /home/u/python/Python-3.12.1/venv/bin/python3')
        sleep(60)

    elif isinstance(provider, Libvirt):
        provider.prepare()
        SystemCommands.cmd(
            "sudo sed -i 's|#cgroup_controllers = \\[ \"cpu\", \"devices\", \"memory\", \"blkio\", \"cpuset\", \"cpuacct\" \\]|cgroup_controllers = [ \"cpu\", \"devices\", \"memory\" ]|' /etc/libvirt/qemu.conf && sudo systemctl restart libvirtd"
        )

        if VERSION_OS == '1.7':
            provider.build(f'1.7.5.{sec_mode}', rc, VMS, VMS_DATES)
        elif VERSION_OS == '1.8':
            provider.build(f'1.8.1.{sec_mode}', rc, VMS, VMS_DATES)

    provider.check(VMS, VMS_DATES)

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
    # test.test()
    SystemCommands.cmd('cat results_balance.txt')

