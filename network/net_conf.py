import os
from pathlib import Path

BASE_PATH = "/home/u/git/stress_test/network"

# Confluence
REPORT_PATH = f'{os.getcwd()}/test_results'

#VM Settings
USERNAME = "u"
PASSWORD = "1"

# Base params
# Create dir if not created
VM_OS_INFO_PATH = f"{BASE_PATH}/vm_info"
if Path(VM_OS_INFO_PATH).is_dir:
    pass
else:
    os.mkdir(VM_OS_INFO_PATH, mode=777)
VM_INFONAME = f'{VM_OS_INFO_PATH}/av.txt'
VM_KERNEL = f'{VM_OS_INFO_PATH}/kernel.txt'

# Test Params

# Kernel Network
KERNEL_NET_VM_COUNT = 2
KERNEL_NET_VCPU = 8
KERNEL_NET_RAM = 16384

# Load params
IOF_OFF_PATH = '/home/u/results_iof_off.txt'
IOF_ON_PATH = '/home/u/results_iof_on.txt'
ITERATIONS = 10