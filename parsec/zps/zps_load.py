import os
import concurrent.futures
import time
from subprocess import run


MAX = 4096
PROG = "/usr/bin/ssh"
WORKERS = 10


def cmd(command: str):
        return run(command, shell=True)


def create_hard_link(x):
    dest = f"/home/x.{x}"
    try:
        os.link(PROG, dest)
        #print(f"Created link {x}")
    except Exception as e:
        print(f"Failed to create link {x}: {e}")

def execute_program(x):
    prog = f"/home/x.{x}"
    try:
        os.system(prog)
        #print(f"Executed {x}")
    except Exception as e:
        print(f"Failed to execute {x}: {e}")

def remove_hard_link(x):
    dest = f"/home/x.{x}"
    try:
        os.remove(dest)
        #print(f"Removed link {x}")
    except Exception as e:
        print(f"Failed to remove link {x}: {e}")


start_time = time.time()

with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
    executor.map(create_hard_link, range(1, MAX + 1))

with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
    executor.map(execute_program, range(1, MAX + 1))

with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
    executor.map(remove_hard_link, range(1, MAX + 1))

end_time = time.time()
print(f"Время выполнения: {end_time - start_time} сек")



cmd('sudo dmesg -HTx | grep DIGSIG | grep ssh | wc -l')

