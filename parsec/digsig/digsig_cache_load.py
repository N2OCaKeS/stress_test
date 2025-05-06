import os
import concurrent.futures
import time
import subprocess


MAX = 4096
PROG = "/usr/bin/perl"
WORKERS = 10


def check_output_command(command: str) -> str:
        result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        output, errors = result.communicate()
        output = os.linesep.join([s for s in output.splitlines() if s])
        errors = os.linesep.join([s for s in errors.splitlines() if s])
        return output if not errors else errors


def create_hard_link(x):
    dest = f"/usr/bin/digsig_x.{x}"
    try:
        os.link(PROG, dest)
        #print(f"Created link {x}")
    except Exception as e:
        print(f"Failed to create link digsig_x{x}: {e}")

def execute_program(x):
    prog = f"/usr/bin/digsig_x.{x}"
    try:
        os.system(f"{prog} > /dev/null 2>&1")
        #print(f"Executed {x}")
    except Exception as e:
        print(f"Failed to execute digsig_x{x}: {e}")

def remove_hard_link(x):
    dest = f"/usr/bin/digsig_x.{x}"
    try:
        os.remove(dest)
        #print(f"Removed link {x}")
    except Exception as e:
        print(f"Failed to remove link digsig_x{x}: {e}")


start_time = time.time()

with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
    executor.map(create_hard_link, range(1, MAX + 1))

with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
    executor.map(execute_program, range(1, MAX + 1))

with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
    executor.map(remove_hard_link, range(1, MAX + 1))

end_time = time.time()
total_time = round(end_time - start_time, 1)
digsig_count = check_output_command('sudo dmesg -HTx | grep DIGSIG | grep digsig_x | wc -l')

print(f"Время выполнения: {total_time} сек")
print(digsig_count)
print(check_output_command('mv /usr/bin/perl.bak /usr/bin/perl'))

with open('/vagrant/results.txt', 'a') as w:
    w.write(f'{total_time}\n')
    w.write(f'{digsig_count}\n')

