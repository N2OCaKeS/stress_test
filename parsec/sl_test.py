from libs.libparsec import check_output_command, command, info_list
from os.path import isdir
from os import mkdir
from re import findall
from sl_conf import (REPORT_PATH, FLAMEGRAPH_NAME, REPORT_FILENAME,
                     TOTALDF_NAME, DETAILDF_NAME,
                     UB_PATH, UB_CONCURRENCY, PERF_FREQ, SPINLOCK_PATTERN)
from json import dumps, loads
import pandas as pd
import subprocess
import signal


class SpinlockImpactTest:
    def __init__(self):
        self.flamegraph_name = FLAMEGRAPH_NAME
        self.spinlock_functions = self._get_spinlock_functions()

    def _get_spinlock_functions(self):
        output = check_output_command(
            f"grep -E '{SPINLOCK_PATTERN}' /proc/kallsyms | awk '{{print $3}}' | sort -u"
        )
        return [f for f in output.split('\n') if f]

    def filter_by_spinlock_function(self):
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

        with open('./libs/out.perf', 'w') as w:
            w.write('')
        with open('./libs/out.perf', 'a') as w:
            for i in final_list:
                for j in i[0]:
                    w.writelines(f'{j}')
                w.writelines('\n')
                for j in i[1:]:
                    w.writelines(f'\t\t{j}\n')
                    if j.split(' ')[1].split('+')[0] in self.spinlock_functions:
                        break
                w.writelines('\n')

    def spinlock_impact_by_unixbench(self):
        if not isdir(REPORT_PATH):
            mkdir(REPORT_PATH, 0o777)

        print('Starting perf record...')
        perf_proc = subprocess.Popen(
            f'sudo perf record -a -g -F {PERF_FREQ} -o libs/perf.data',
            shell=True
        )

        print(f'Running UnixBench with {UB_CONCURRENCY} workers...')
        command(f'cd {UB_PATH} && ./Run -c {UB_CONCURRENCY}')

        print('Stopping perf...')
        perf_proc.send_signal(signal.SIGINT)
        perf_proc.wait()

        command('cd libs && sudo perf report -i perf.data > perf_report.txt')
        command('cd libs && sudo perf script -i perf.data > out_rare.perf')
        self.filter_by_spinlock_function()
        command('cd libs && sudo perl libstackcollapse-perf.pl out.perf > out.folded')
        command(f'sudo perl libs/libflamegraph.pl libs/out.folded > {REPORT_PATH}/{self.flamegraph_name}')

        print(f'\033[93m\nSpinlock functions found ({len(self.spinlock_functions)}):\n{self.spinlock_functions}\n\033[0m')

        def used_cpu_time(func_name):
            func_command = f"cat {REPORT_PATH}/{self.flamegraph_name} | grep -w '{func_name}' | awk '{{print $4}}'"
            return check_output_command(func_command)

        used_cpu_dict = {
            func: [findall(r'\d+\.\d+', x) for x in used_cpu_time(func).split('\n')]
            for func in self.spinlock_functions
        }

        used_cpu_dict = {
            key: [round(sum(float(v[0]) for v in vals if v), 2)]
            for key, vals in used_cpu_dict.items()
        }

        used_cpu_dict = {k: v for k, v in used_cpu_dict.items() if v[0] > 0}

        print('\n*** Spinlock CPU usage ***')
        for key, value in used_cpu_dict.items():
            print(f'{key}: {value[0]}%')

        total_used = round(sum(sum(v) for v in used_cpu_dict.values()), 2)
        print(f'\nTotal spinlock CPU: {total_used}%\n')

        with open(f'{REPORT_PATH}/{REPORT_FILENAME}', 'w') as f:
            f.write(dumps({
                'total': {'%': {'Total spinlock CPU': total_used}},
                'detail': {'%': used_cpu_dict}
            }, indent=4))

        with open(f'{REPORT_PATH}/{REPORT_FILENAME}', 'r') as f:
            data = loads(f.read())

        total_df = pd.DataFrame(data['total'])
        detail_df = pd.DataFrame(data['detail'])
        total_df.to_html(TOTALDF_NAME)
        detail_df.to_html(DETAILDF_NAME)
        info_list()
