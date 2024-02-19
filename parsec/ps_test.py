from libs.libparsec import (check_output_command,
                            command,
                            info_list)
from os.path import isfile, isdir
from os import mkdir
from re import findall
from ps_conf import REPORT_PATH, CONC, COUNTER, FILE_SYSTEM, FLAMEGRAPH_NAME, REPORT_FILENAME, \
                    TIMEDF_NAME, TOTALDF_NAME, DETAILDF_NAME
from json import dumps, loads
import pandas as pd



class ParsecImpactTest:
    def __init__(self):
        self.load_dir = '/tmp/test_dir'
        self.perf_report_name = 'perf_report.txt'
        self.shared_object_name = 'kernel.kallsyms'
        self.flamegraph_name = FLAMEGRAPH_NAME
        self.load_rare_results_name = 'load_rare_results.txt'
        self.load_command = f'cd libs && {{ time sudo perf record -a -g -F 99 ./load_test {self.load_dir} \
            {CONC} {COUNTER} ; }} 2> {self.load_rare_results_name}'
        self.find_args = ['real', 'user', 'sys']
        self.found_functions = f"cat libs/{self.perf_report_name} | grep {self.shared_object_name} | awk '{{print $6}}'"
        self.load_rare_results = 'real\t0m0,000s\nuser\t0m0,000s\nsys\t0m0,000s'
        self.parsec_function = check_output_command('nm /usr/lib/modules/`uname -r`/misc/parsec.ko | grep -i " t " | \
                                                     awk \'{print $3}\'').split('\n')
        
    def filter_by_parsec_function(self):
        with open('./libs/out_rare.perf', 'r') as r:
            text = r.readlines()

        final_list = []
        temp_list = []

        for line in text:
            stripped_line = line.strip()
            if stripped_line:
                temp_list.append(stripped_line)
            elif temp_list:
                final_list.append(temp_list)
                temp_list = []

        if temp_list:
            final_list.append(temp_list)
        print(final_list)

        with open('./libs/out.perf', 'w') as w:
            w.write('')
        with open('./libs/out.perf', 'a') as w:
            for i in final_list:
                for j in i[0]:
                    w.writelines(f'{j}')
                w.writelines('\n')
                for j in i[1:]:
                    w.writelines(f'\t\t{j}\n')
                    #print(j.split(' ')[1])
                    if j.split(' ')[1] in self.parsec_function:
                        break
                w.writelines('\n')


    def parsec_impact_by_fs_load(self):
        if not isdir(self.load_dir):
            mkdir(self.load_dir, 0o777)
            command(f'sudo mount -t {FILE_SYSTEM} -o size=100M test_dir {self.load_dir}')
        
        if not isdir(REPORT_PATH):
            mkdir(REPORT_PATH, 0o777)

        command(self.load_command)
        command(f'cd libs && sudo perf report > {self.perf_report_name}')
        command('cd libs && sudo perf script -i perf.data > out_rare.perf')
        self.filter_by_parsec_function()
        command('cd libs && sudo perl libstackcollapse-perf.pl out.perf > out.folded')
        command(f'sudo perl libs/libflamegraph.pl libs/out.folded > {REPORT_PATH}/{self.flamegraph_name}')
        print(f'\nUsed dir: {self.load_dir}\n')

        if isfile(f'libs/{self.load_rare_results_name}'):
            with open(f'libs/{self.load_rare_results_name}', 'r') as r:
                load_rare_results = r.read()
                #print(load_rare_results)

        # # # Found used function
        def found_used_functions(shared_object=False):
            if shared_object:
                if isfile(f'libs/{self.perf_report_name}'):
                    used_functions = check_output_command(self.found_functions)
                #print(f'used_functions {used_functions}')
                cleared_used_functions = used_functions.split('\n')
            else: cleared_used_functions = set(self.parsec_function)
            print(f'\033[93m\nUsed Functions:\n{set(self.parsec_function)}\n\033[0m')
            #print(f'cleared_used_functions {cleared_used_functions}')
            return cleared_used_functions

        def used_cpu_time(func_name): 
            func_command = f"cat {REPORT_PATH}/{self.flamegraph_name} | grep -w '{func_name}' | awk '{{print $4}}'"
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
            result.replace('\t', '-').split('-') for match in self.find_args for result in \
            load_rare_results.strip().split('\n') if result.startswith(match)
            ]

        load_rare_results = {i[0]:i[1] for i in load_rare_results}
        load_results = {
            key:{'Seconds':int(value[0]) * 60 + float(value[1].replace(',', '.')) 
                for value in [load_rare_results[key].strip('s').split('m')]} for key in self.find_args
        }
        print(load_results)

        with open(f'{REPORT_PATH}/{REPORT_FILENAME}', 'w') as tr_write:
            tr_write.write(dumps({'time':load_results,
                                  'total':{'%':{'Total used by parsec func':total_used}},
                                  'detail':{'%':used_cpu_dict}
                                  }, indent=4))

        with open(f'{REPORT_PATH}/{REPORT_FILENAME}', 'r') as tr_read:
            dates = loads(tr_read.read())

        time_df = pd.DataFrame(dates['time'])
        total_df = pd.DataFrame(dates['total'])
        detail_df = pd.DataFrame(dates['detail'])
        time_df.to_html(TIMEDF_NAME)
        total_df.to_html(TOTALDF_NAME)
        detail_df.to_html(DETAILDF_NAME)
        info_list()


