from libs.libtests import XFSMemoryLeak
from kernel_conf import BASE_PATH, XFS_MEMORY_LEAK_RAM, XFS_MEMORY_LEAK_VCPU, XFS_MEMORY_LEAK_VM_COUNT

TEST_CYCLE_VERSION = "1.7.9.UU.1.1"
CONF_PARENT_PAGE = f"STRESS {TEST_CYCLE_VERSION} xfs_memory_leak"
CONF_NEW_PAGE_NAME = f"stress_{TEST_CYCLE_VERSION}_xfs_memory_leak"

KERNEL = "6.1.161-1-generic"

xfs_memory_leak = XFSMemoryLeak(rc_name=TEST_CYCLE_VERSION, 
                                testdir=BASE_PATH, 
                                vm_count=XFS_MEMORY_LEAK_VM_COUNT, 
                                kernel=KERNEL, 
                                vcpu=XFS_MEMORY_LEAK_VCPU, 
                                ram=XFS_MEMORY_LEAK_RAM)
xfs_memory_leak.prepare_vms()
xfs_memory_leak.start_test()
status = xfs_memory_leak.results_processing()

