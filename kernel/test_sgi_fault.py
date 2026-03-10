# import os
from libs.libtests import Sigmentation_fault
# from libs.libpublic import Public
# from libs.virtlib import info_list
from kernel_conf import BASE_PATH, SEGMENTATION_FAULT_VM_COUNT, SEGMENTATION_FAULT_VCPU, SEGMENTATION_FAULT_RAM
# from allta import SystemCommands
TEST_CYCLE_VERSION = "1.7.5"
CONF_PARENT_PAGE = f"STRESS {TEST_CYCLE_VERSION} kernel net load"
CONF_NEW_PAGE_NAME = f"stress_{TEST_CYCLE_VERSION}_kernel_net_load"
# KERNEL = SystemCommands.check_output_command("unamer -r")
KERNEL = ""
# # 0. Готовим каталоги под результаты/шаблоны
# os.makedirs(REPORT_PATH, exist_ok=True)
# 1. Гоним тест
kernel = Sigmentation_fault(rc_name=TEST_CYCLE_VERSION, testdir=BASE_PATH, vm_count=SEGMENTATION_FAULT_VM_COUNT, kernel=KERNEL, vcpu=SEGMENTATION_FAULT_VCPU, ram=SEGMENTATION_FAULT_RAM)
kernel.prepare_vms()
kernel.start_test()
kernel.results_processing()

