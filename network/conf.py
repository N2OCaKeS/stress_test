import os
from pathlib import Path

BASE_PATH = "/home/u/git/stress_test/network"

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
