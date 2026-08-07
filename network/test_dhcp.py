# import os
from libs.libtests import Dhcp
# from libs.libpublic import Public
# from libs.virtlib import info_list
from net_conf import BASE_PATH, DHCP_VM_COUNT, DHCP_VCPU, DHCP_RAM
# from allta import SystemCommands
TEST_CYCLE_VERSION = "1.8.6.39"
CONF_PARENT_PAGE = f"STRESS {TEST_CYCLE_VERSION} kernel net load"
CONF_NEW_PAGE_NAME = f"stress_{TEST_CYCLE_VERSION}_kernel_net_load"
# KERNEL = SystemCommands.check_output_command("unamer -r")
KERNEL = ""
# # 0. Готовим каталоги под результаты/шаблоны
# os.makedirs(REPORT_PATH, exist_ok=True)
# 1. Гоним тест
dhcp = Dhcp(rc_name=TEST_CYCLE_VERSION, testdir=BASE_PATH, vm_count=DHCP_VM_COUNT, kernel=KERNEL, vcpu=DHCP_VCPU, ram=DHCP_RAM)
dhcp.prepare_vms()
dhcp.start_test()
dhcp.results_processing()

