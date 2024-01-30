from libs.libparsec import (check_output_command,
                            command)
from os.path import isfile
from re import findall


#количество создаваемых потоков 
concurrency = 10
#количество циклов для каждого потока
counter = 1000

perf_report_name = 'perf_report.txt'
shared_object_name = 'kernel.kallsyms'
flamegraph_name = 'result_flamegraph.svg'
load_command = f'cd libs && sudo perf record -a -g -F 99 time ./load_test /tmp {concurrency} {counter}'
load_rare_results = check_output_command(load_command); print(load_rare_results)
find_args = ['real', 'user', 'sys']
found_functions = f"cat {perf_report_name} | grep {shared_object_name} | awk '{{print $6}}'"

command(f'cd libs && sudo perf report > {perf_report_name}')
command('cd libs && sudo perf script -i perf.data > out.perf')
command('cd libs && sudo perl libstackcollapse-perf.pl out.perf > out.folded')
command(f'sudo perl libs/libflamegraph.pl libs/out.folded > {flamegraph_name}')

# # # Found used function
if isfile(f'libs/{perf_report_name}'):
    used_functions = check_output_command(found_functions)
print(f'used_functions {used_functions}')
cleared_used_functions = [func for func in set(used_functions)]
print(f'cleared_used_functions {cleared_used_functions}')

def used_cpu_time(func_name): 
    func_command = f"cat {flamegraph_name} | grep {func_name} | awk '{{print $4}}'"
    return check_output_command(func_command)

used_cpu_dict = {
    func:[findall(r'\d+\.\d+', x)[0] for x in used_cpu_time(func).split('\n')] for func in cleared_used_functions
}
print(f'used_cpu_dict {used_cpu_dict}')

load_rare_results = [
    result.replace('\t', '-').split('-') for match in find_args for result in \
    load_rare_results.strip().split('\n') if result.startswith(match)
    ]

load_rare_results = {i[0]:i[1] for i in load_rare_results}
load_results = {
    key:{'Seconds':int(value[0]) * 60 + float(value[1].replace(',', '.')) 
         for value in [load_rare_results[key].strip('s').split('m')]} for key in find_args
}

print(load_results)

