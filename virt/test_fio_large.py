import os
from libs.libtests import LargeFio
from libs.libpublic import Public
from libs.virtlib import info_list
from virt_conf import REPORT_PATH
from allta import SystemCommands
TEST_CYCLE_VERSION = "1.7.5"
CONF_PARENT_PAGE = f"STRESS {TEST_CYCLE_VERSION} fio_large"
CONF_NEW_PAGE_NAME = f"stress_{TEST_CYCLE_VERSION}_fio_large"
KERNEL = SystemCommands.check_output_command("unamer -r")
# 0. Готовим каталоги под результаты/шаблоны
os.makedirs(REPORT_PATH, exist_ok=True)
# 1. Гоним тест
lf = LargeFio(rc_vbox=TEST_CYCLE_VERSION, testdir=REPORT_PATH, vm_count=1, kernel=KERNEL, vcpu=16, ram=32768)
lf.prepare_vms()
lf.start_test()
lf.results_processing()  # сформирует HTML

# 2. Обновляем host info
info_list()

# 3. Публикуем только в Confluence (без Zephir)
pub = Public(
    username="mfilippenko",
    token="",
    conf_space="~mfilippenko",
    conf_parent_page=CONF_PARENT_PAGE,
    conf_new_page_name=CONF_NEW_PAGE_NAME,
    grade_stand="12",
    test_cycle_version=TEST_CYCLE_VERSION,
    testname="fio_large"
)
pub.run_publish()
