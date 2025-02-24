import os
import concurrent.futures

MAX = 16384
PROG = "/usr/bin/ssh"
WORKERS = 10


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



with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
    executor.map(create_hard_link, range(1, MAX + 1))

with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
    executor.map(execute_program, range(1, MAX + 1))

with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
    executor.map(remove_hard_link, range(1, MAX + 1))

