import os
import requests
from pathlib import Path

jira_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-jira-url'
confluence_url_api = 'http://allta.devos.astralinux.ru/rest/api/get-confluence-url'
response_jira_url = requests.get(jira_url_api)
response_confluence_url = requests.get(confluence_url_api)
JIRA_URL = response_jira_url.text
CONFLUENCE_URL = response_confluence_url.text
VENV_PATH = '/home/u/python/Python-3.12.1/venv/bin/python3.12'

BASE_PATH = "/home/u/git/stress_test/kernel"

# Confluence
REPORT_PATH = f'{os.getcwd()}/test_results'

#VM Settings
USERNAME = "u"
PASSWORD = "1"

# Base params
# Create dir if not created
VM_OS_INFO_PATH = f"{BASE_PATH}/vm_info"
Path(VM_OS_INFO_PATH).mkdir(mode=0o777, parents=True, exist_ok=True)
VM_INFONAME = f'{VM_OS_INFO_PATH}/av.txt'
VM_KERNEL = f'{VM_OS_INFO_PATH}/kernel.txt'

VM_TEST1_OUTPUT = f'{BASE_PATH}/test1_output.txt'
VM_TEST2_OUTPUT = f'{BASE_PATH}/test2_output.txt'
RESULTS_FILE = 'results.json'

# Test Params

# Kernel Network
SEGMENTATION_FAULT_VM_COUNT = 1
SEGMENTATION_FAULT_VCPU = 4
SEGMENTATION_FAULT_RAM = 4096

XFS_MEMORY_LEAK_VM_COUNT = 1
XFS_MEMORY_LEAK_VCPU = 4
XFS_MEMORY_LEAK_RAM = 8192

USAGE_OS_RESULTS_DIR = f"{BASE_PATH}/results"
USAGE_OS_IDLE_CSV = f"{USAGE_OS_RESULTS_DIR}/idle_os.csv"
USAGE_OS_LOAD_CSV = f"{USAGE_OS_RESULTS_DIR}/load_os.csv"
USAGE_OS_MATH_MODEL_FILE = f"{USAGE_OS_RESULTS_DIR}/math_model_results.json"

# Метрики для матмодели геометрического среднего (load ОС относительно idle-baseline)
USAGE_OS_MATH_MODEL_METRICS = {
    "cpu_used_pct": "Загрузка CPU (ОС), %",
    "mem_used_pct": "Использование RAM (ОС), %",
    "load_1m": "Load average (1 мин)",
    "context_switches_per_sec": "Переключения контекста, 1/с",
    "interrupts_per_sec": "Прерывания, 1/с",
}

# Все метрики idle/load_os.csv для графика на странице отчёта
USAGE_OS_ALL_METRICS = {
    "cpu_user_pct": "CPU user, %",
    "cpu_system_pct": "CPU system, %",
    "cpu_irq_pct": "CPU irq+softirq, %",
    "cpu_iowait_pct": "CPU iowait, %",
    "cpu_steal_pct": "CPU steal, %",
    "cpu_idle_pct": "CPU idle, %",
    "cpu_used_pct": "CPU used, %",
    "load_1m": "Load average, 1 мин",
    "load_5m": "Load average, 5 мин",
    "load_15m": "Load average, 15 мин",
    "tasks_running": "Задач в очереди",
    "tasks_total": "Задач всего",
    "mem_total_kb": "Memory total, KB",
    "mem_available_kb": "Memory available, KB",
    "mem_used_kb": "Memory used, KB",
    "mem_used_pct": "Memory used, %",
    "swap_total_kb": "Swap total, KB",
    "swap_used_kb": "Swap used, KB",
    "swap_used_pct": "Swap used, %",
    "buffers_kb": "Buffers, KB",
    "cached_kb": "Cached, KB",
    "slab_kb": "Slab, KB",
    "kernel_memory_kb": "Kernel memory (stack+page tables), KB",
    "shmem_kb": "Shared memory, KB",
    "context_switches_per_sec": "Переключения контекста, 1/с",
    "interrupts_per_sec": "Прерывания, 1/с",
    "procs_running": "Процессов running",
    "procs_blocked": "Процессов blocked",
    "disk_read_kbps": "Диск чтение, KB/s",
    "disk_write_kbps": "Диск запись, KB/s",
    "net_rx_kbps": "Сеть rx (внешняя), KB/s",
    "net_tx_kbps": "Сеть tx (внешняя), KB/s",
    "net_lo_rx_kbps": "Сеть rx (loopback), KB/s",
    "net_lo_tx_kbps": "Сеть tx (loopback), KB/s",
}