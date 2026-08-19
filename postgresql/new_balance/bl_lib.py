import copy
import os

from allta import Libvirt, SystemCommands, LibvirtManager
from new_balance.roles.database.db import DatabaseVM
from new_balance.roles.domain.domain import DomainVM
from new_balance.roles.load_balancer.load_balancer import LoadBalancer
from new_balance.roles.task.pre_configure import PreConfigure
from new_balance.roles.web.apache import ApacheVM
from new_balance.roles.task.test import Test, InfoSysLoadTest
from new_balance.roles.vm_info import (VERSION_OS, VMS, VMS_DATES, VMS_GROUPS,
                           INFO_SYS_ONLY_VMS, INFO_SYS_VMS_DATES, PROVIDER)

# from new_balance.roles.web.apache import ApacheVM

def balance(rc, sec_mode="s", type_test="balance"):
    provider = PROVIDER
    info_sys_types = ("info-sys", "info-sys-orel")

    if type_test in info_sys_types:
        # web1/loader по умолчанию отсутствуют в VMS/VMS_DATES/VMS_GROUPS (см.
        # vm_info.py) — добавляем их здесь (изменяем те же объекты, что импортированы
        # во всех модулях, поэтому domain.py/pre_configure.py и т.д. их увидят).
        for vm in INFO_SYS_ONLY_VMS:
            if vm not in VMS:
                VMS.append(vm)
        VMS_DATES.update(INFO_SYS_VMS_DATES)
        for group in ("all", "domain_client"):
            for vm in INFO_SYS_ONLY_VMS:
                if vm not in VMS_GROUPS[group]:
                    VMS_GROUPS[group].append(vm)
        if "web1" not in VMS_GROUPS["web"]:
            VMS_GROUPS["web"].append("web1")
        if "loader" not in VMS_GROUPS["loader"]:
            VMS_GROUPS["loader"].append("loader")

    new_vms_data = {}
    if isinstance(provider, Libvirt):
        save_path = "vms.json"

        if os.path.exists(save_path):
            new_vms_data = LibvirtManager.Vm.load_vms_data(save_path)
            if type_test not in info_sys_types:
                for vm in INFO_SYS_ONLY_VMS:
                    new_vms_data.pop(vm, None)
            LibvirtManager.Snapshot.revert(vms=VMS, snapshot_name="build")
        else:
            provider.prepare()
            SystemCommands.cmd(
                'sudo sed -i \'s|#cgroup_controllers = \\[ "cpu", "devices", "memory", "blkio", "cpuset", "cpuacct" \\]|cgroup_controllers = [ "cpu", "devices", "memory" ]|\' /etc/libvirt/qemu.conf && sudo systemctl restart libvirtd'
            )

            build_vms_dates = VMS_DATES
            if type_test in info_sys_types:
                # protopack (setup_protopack) грузит в /tmp дамп build_packages_new (~5G),
                # дефолтного корневого диска для database1-3 на это не хватает
                build_vms_dates = copy.deepcopy(VMS_DATES)
                for db_vm in ("database1", "database2", "database3"):
                    build_vms_dates[db_vm]["disk"] = "40"

            if VERSION_OS == "1.7":
                new_vms_data = provider.build(box=f"1.7.5.{sec_mode}", rc=rc, vms=VMS, vms_dates=build_vms_dates)
            elif VERSION_OS == "1.8":
                new_vms_data = provider.build(box=f"1.8.1.{sec_mode}", rc=rc, vms=VMS, vms_dates=build_vms_dates)

            LibvirtManager.Vm.save_vms_data(vms_dates=new_vms_data, save_path=save_path)  # сохраняем IP ВМ для следующего запуска / save VM IPs for next run

    VMS_DATES.update(new_vms_data)  # обновляем оригинальный словарь in-place, чтобы все модули увидели новые IP / update original dict in-place so all modules see new IPs
    provider.check(vms=VMS, vms_dates=VMS_DATES)

    configure = PreConfigure()  # Проверено работает
    configure.prepare(type_test=type_test)


    domain = DomainVM()  # Проверено работает
    domain.settings(type_test=type_test)

    database = DatabaseVM()  # Проверено работает
    database.settings(type_test=type_test)

    if type_test in info_sys_types:
        database.setup_protopack(type_test=type_test)  # база protopack внутри contrprimer, для info-sys

    load_balancer = LoadBalancer()
    load_balancer.load(type_test=type_test)

    if type_test == "info-sys":
        database.setup_mac()        # MAC-метки на protopack + роль protopack_web, до web.settings()
        database.setup_privsock()   # PARSEC_CAP_PRIV_SOCK на database1/2/3, иначе МРД-уровень >=1 виснет
                                    
        web = ApacheVM()            # после domain, чтобы Kerberos уже работал
        web.settings(type_test=type_test)

        info_sys_load = InfoSysLoadTest()
        info_sys_load.run()
    elif type_test == "info-sys-orel":
        web = ApacheVM()
        web.settings(type_test=type_test)

        info_sys_load = InfoSysLoadTest()
        info_sys_load.run()
    
    if type_test == "balance":
        test = Test()
        test.test()
        SystemCommands.cmd("cat results_balance.txt")
        LibvirtManager.Vm.stop(vms=VMS)
