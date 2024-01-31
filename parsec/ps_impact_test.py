from libs.libparsec import (check_output_command,
                            command)
from os.path import isfile, isdir
from os import mkdir
from re import findall


#количество создаваемых потоков 
concurrency = 100
#количество циклов для каждого потока
counter = 1000

load_dir = '/tmp/x'
perf_report_name = 'perf_report.txt'
shared_object_name = 'kernel.kallsyms'
flamegraph_name = 'result_flamegraph.svg'
load_rare_results_name = 'load_rare_results.txt'
load_command = f'cd libs && {{ time sudo perf record -a -g -F 99 ./load_test {load_dir} \
    {concurrency} {counter} ; }} 2> {load_rare_results_name}'
find_args = ['real', 'user', 'sys']
found_functions = f"cat libs/{perf_report_name} | grep {shared_object_name} | awk '{{print $6}}'"
load_rare_results = 'real\t0m0,000s\nuser\t0m0,000s\nsys\t0m0,000s'
parsec_function = ['parsec_capable_ilev', 'parsec_inode_permission_a', 'i_pdpl_get',
'parsec_secid', 'pdpl_put', 'pdpl_get', 'parsec_current_permission', 'parsec_caps_task_get',
'caps_cpy', 'pdpml_permission', 'parsec_realpath_from_dentry', 'parsec_task_lbl_get',
'parsec_log', 'parsec_inode_permission', 'parsec_audit_rule_match', 'pdpl_permission',
'psc_audit_check', 'parsec_confine_check', 'is_kernel_audit', 'i_audit_get', 'di_audit_init',
'pdpml_conf_permission', 'di_pdpl_set_from_tsk', 'di_pdpl_set', 'parsec_file_open',
'di_faud_from_xattr', 'pdpl_cmp', 'irelax_permission', 'di_pdpl_init', 'parsec_path_mknod',
'psc_secid_to_lbl', 'inode_post_create', 'parsec_instantiate', 'parsec_i_alloc_security',
'di_getxattr', 'file_audit_inherit_default_audit', 'parsec_i_free_security', 'parsec_inode_init_xattrs',
'di_pdpl_from_xattr', 'parsec_path_hook.isra.0', 'pdpl_dup', 'pdpl_inherit_integrity', 'cell_check',
'pdpml_put', 'do_i_init_security', 'aud_cpy', 'di_chlbl_permission', 'do_post_create',
'parsec_current_getsecid_subj', 'pdpl_or_type', 'faud_dup', 'parsec_oracle_check_dentry',
'inode_use_init_xattrs.isra.0', 'I_LBL_UPD', 'parsec_aud_task_get', 'pdpl_inherit_secrecy']

if not isdir(load_dir):
    mkdir(load_dir, 0o777)
    command(f'sudo mount -t tmpfs -o size=100M test_dir {load_dir}')

command(load_command)
command(f'cd libs && sudo perf report > {perf_report_name}')
command('cd libs && sudo perf script -i perf.data > out.perf')
command('cd libs && sudo perl libstackcollapse-perf.pl out.perf > out.folded')
command(f'sudo perl libs/libflamegraph.pl libs/out.folded > {flamegraph_name}')
print(f'\nUsed dir: {load_dir}\n')

if isfile(f'libs/{load_rare_results_name}'):
    with open(f'libs/{load_rare_results_name}', 'r') as r:
        load_rare_results = r.read()
        #print(load_rare_results)

# # # Found used function
def found_used_functions(shared_object=False):
    if shared_object:
        if isfile(f'libs/{perf_report_name}'):
            used_functions = check_output_command(found_functions)
        #print(f'used_functions {used_functions}')
        cleared_used_functions = used_functions.split('\n')
    else: cleared_used_functions = set(parsec_function)
    print(f'\033[93m\nUsed Functions:\n{set(parsec_function)}\n\033[0m')
    #print(f'cleared_used_functions {cleared_used_functions}')
    return cleared_used_functions

def used_cpu_time(func_name): 
    func_command = f"cat {flamegraph_name} | grep -w '{func_name}' | awk '{{print $4}}'"
    return check_output_command(func_command)

used_cpu_dict = {
    func:[findall(r'\d+\.\d+', x) for x in used_cpu_time(func).split('\n')] for func in found_used_functions()
}
#print('*** used_cpu_dict(rare) ***')
#for key, value in used_cpu_dict.items():
#    print(key, value)

used_cpu_dict = {
    key:[round(sum(float(v[0]) for v in list_value if v), 2)] for key, list_value in used_cpu_dict.items()
}
print('\n*** used_cpu_dict ***')
for key, value in used_cpu_dict.items():
    print(f'{key}: {value}%')

total_used = round(sum([sum(v) for v in used_cpu_dict.values() if v]), 2)
print(f'\nTotal used by parsec func: {total_used}%\n')


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

