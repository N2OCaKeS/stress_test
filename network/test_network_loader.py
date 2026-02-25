# import os
from libs.libtests import NetworkLoad
# from libs.libpublic import Public
# from libs.virtlib import info_list
from net_conf import BASE_PATH, KERNEL_NET_VM_COUNT, KERNEL_NET_VCPU, KERNEL_NET_RAM
# from allta import SystemCommands
TEST_CYCLE_VERSION = "1.7.5"
CONF_PARENT_PAGE = f"STRESS {TEST_CYCLE_VERSION} kernel net load"
CONF_NEW_PAGE_NAME = f"stress_{TEST_CYCLE_VERSION}_kernel_net_load"
# KERNEL = SystemCommands.check_output_command("unamer -r")
KERNEL = ""
# # 0. Готовим каталоги под результаты/шаблоны
# os.makedirs(REPORT_PATH, exist_ok=True)
# 1. Гоним тест
kernel = NetworkLoad(rc_name=TEST_CYCLE_VERSION, testdir=BASE_PATH, vm_count=KERNEL_NET_VM_COUNT, kernel=KERNEL, vcpu=KERNEL_NET_VCPU, ram=KERNEL_NET_RAM)
kernel.prepare_vms()
kernel.start_test()
kernel.results_processing()

