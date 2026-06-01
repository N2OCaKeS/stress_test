from libs.libparsec import check_output_command, command, info_list
from os.path import isdir
from os import mkdir
from re import findall
from ps_conf import (REPORT_PATH, FLAMEGRAPH_NAME, UB_FLAMEGRAPH_NAME, FS_FLAMEGRAPH_NAME, L2_FLAMEGRAPH_NAME, REPORT_FILENAME,
                     TIMEDF_NAME, TOTALDF_NAME, DETAILDF_NAME,
                     FS_TIMEDF_NAME, FS_TOTALDF_NAME, FS_DETAILDF_NAME,
                     L2_TIMEDF_NAME, L2_TOTALDF_NAME, L2_DETAILDF_NAME,
                     UB_PATH, UB_CONCURRENCY,
                     FS_MARK_PATH, FS_MARK_DIR1, FS_MARK_DIR2, FS_MARK_DIR3, FS_MARK_DIR4, FS_MARK_DIR5, FS_MARK_SIZE, FS_MARK_COUNT,
                     LOAD2_PATH, LOAD2_DIR, LOAD2_WORKERS, LOAD2_LOOPS, LOAD2_ARCHIVE_LOOPS,
                     PERF_FREQ, SPINLOCK_PATTERN)
from json import dumps, loads
import pandas as pd
import subprocess
import time


class SpinlockImpactTest:
    def __init__(self):
        self.flamegraph_name = FLAMEGRAPH_NAME
        self.spinlock_functions = self._get_spinlock_functions()

    def _get_spinlock_functions(self):
        output = check_output_command(
            f"grep -E '{SPINLOCK_PATTERN}' /proc/kallsyms | awk '{{print $3}}' | sort -u"
        )
        return [f for f in output.split('\n') if f and not f.startswith('__ksymtab_')]

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
            ['sudo', 'perf', 'record', '-a', '-g', '-F', str(PERF_FREQ), '-o', 'libs/perf.data']
        )

        print(f'Running UnixBench with {UB_CONCURRENCY} workers...')
        start = time.perf_counter()
        command(f'cd {UB_PATH} && ./Run -c {UB_CONCURRENCY}')
        duration = round(time.perf_counter() - start, 3)

        print('Stopping perf...')
        subprocess.run(['sudo', 'pkill', '-INT', '-x', 'perf'])
        perf_proc.wait()

        command('cd libs && sudo perf report -i perf.data > perf_report.txt')
        command('cd libs && sudo perf script -i perf.data > out_rare.perf')
        self.filter_by_spinlock_function()
        command('cd libs && sudo perl libstackcollapse-perf.pl out.perf > out.folded')
        command(f'sudo perl libs/libflamegraph.pl libs/out.folded > {REPORT_PATH}/{UB_FLAMEGRAPH_NAME}')

        print(f'\033[93m\nSpinlock functions found ({len(self.spinlock_functions)}):\n{self.spinlock_functions}\n\033[0m')

        def used_cpu_time(func_name):
            func_command = f"cat {REPORT_PATH}/{UB_FLAMEGRAPH_NAME} | grep -w '{func_name}' | awk '{{print $4}}'"
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
        spinlock_seconds = round((total_used / 100) * duration, 3)

        print(f'\nTotal spinlock CPU share: {total_used}%')
        print(f'Duration: {duration} sec')
        print(f'Spinlock time: {spinlock_seconds} sec\n')

        with open(f'{REPORT_PATH}/{REPORT_FILENAME}', 'w') as f:
            f.write(dumps({
                'time': {
                    'duration': {'Seconds': duration},
                    'spinlock': {'Seconds': spinlock_seconds}
                },
                'total': {'%': {'Total spinlock CPU share': total_used}},
                'detail': {'%': used_cpu_dict}
            }, indent=4))

        with open(f'{REPORT_PATH}/{REPORT_FILENAME}', 'r') as f:
            data = loads(f.read())

        time_df = pd.DataFrame(data['time'])
        total_df = pd.DataFrame(data['total'])
        detail_df = pd.DataFrame(data['detail'])
        time_df.to_html(TIMEDF_NAME)
        total_df.to_html(TOTALDF_NAME)
        detail_df.to_html(DETAILDF_NAME)
        info_list()

    def spinlock_impact_by_fs_mark(self):
        if not isdir(REPORT_PATH):
            mkdir(REPORT_PATH, 0o777)
        if not isdir(FS_MARK_DIR1):
            mkdir(FS_MARK_DIR1, 0o777)
        if not isdir(FS_MARK_DIR2):
            mkdir(FS_MARK_DIR2, 0o777)
        if not isdir(FS_MARK_DIR3):
            mkdir(FS_MARK_DIR3, 0o777)
        if not isdir(FS_MARK_DIR4):
            mkdir(FS_MARK_DIR4, 0o777)
        if not isdir(FS_MARK_DIR5):
            mkdir(FS_MARK_DIR5, 0o777)

        print('Starting perf record...')
        perf_proc = subprocess.Popen(
            ['sudo', 'perf', 'record', '-a', '-g', '-F', str(PERF_FREQ), '-o', 'libs/perf.data']
        )

        print(f'Running fs_mark (size={FS_MARK_SIZE}, count={FS_MARK_COUNT})...')
        start = time.perf_counter()
        command(
            f'{FS_MARK_PATH} -t 30 -d {FS_MARK_DIR1} -d {FS_MARK_DIR2} -d {FS_MARK_DIR3} -d {FS_MARK_DIR4} -d {FS_MARK_DIR5} -s {FS_MARK_SIZE} -n {FS_MARK_COUNT}'
        )
        duration = round(time.perf_counter() - start, 3)

        print('Stopping perf...')
        subprocess.run(['sudo', 'pkill', '-INT', '-x', 'perf'])
        perf_proc.wait()

        command('cd libs && sudo perf report -i perf.data > perf_report.txt')
        command('cd libs && sudo perf script -i perf.data > out_rare.perf')
        self.filter_by_spinlock_function()
        command('cd libs && sudo perl libstackcollapse-perf.pl out.perf > out.folded')
        command(f'sudo perl libs/libflamegraph.pl libs/out.folded > {REPORT_PATH}/{FS_FLAMEGRAPH_NAME}')

        print(f'\033[93m\nSpinlock functions found ({len(self.spinlock_functions)}):\n{self.spinlock_functions}\n\033[0m')

        def used_cpu_time(func_name):
            func_command = f"cat {REPORT_PATH}/{FS_FLAMEGRAPH_NAME} | grep -w '{func_name}' | awk '{{print $4}}'"
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
        spinlock_seconds = round((total_used / 100) * duration, 3)

        print(f'\nTotal spinlock CPU share: {total_used}%')
        print(f'Duration: {duration} sec')
        print(f'Spinlock time: {spinlock_seconds} sec\n')

        with open(f'{REPORT_PATH}/{REPORT_FILENAME}', 'w') as f:
            f.write(dumps({
                'time': {
                    'duration': {'Seconds': duration},
                    'spinlock': {'Seconds': spinlock_seconds}
                },
                'total': {'%': {'Total spinlock CPU share': total_used}},
                'detail': {'%': used_cpu_dict}
            }, indent=4))

        with open(f'{REPORT_PATH}/{REPORT_FILENAME}', 'r') as f:
            data = loads(f.read())

        time_df = pd.DataFrame(data['time'])
        total_df = pd.DataFrame(data['total'])
        detail_df = pd.DataFrame(data['detail'])
        time_df.to_html(FS_TIMEDF_NAME)
        total_df.to_html(FS_TOTALDF_NAME)
        detail_df.to_html(FS_DETAILDF_NAME)
        info_list()

    def spinlock_impact_by_load2noarch(self):
        if not isdir(REPORT_PATH):
            mkdir(REPORT_PATH, 0o777)
        if not isdir(LOAD2_DIR):
            mkdir(LOAD2_DIR, 0o777)
        
        command('make -C libs load2noarch')

        print('Starting perf record...')
        perf_proc = subprocess.Popen(
            ['sudo', 'perf', 'record', '-a', '-g', '-F', str(PERF_FREQ), '-o', 'libs/perf.data']
        )

        print(f'Running load2noarch (workers={LOAD2_WORKERS}, loops={LOAD2_LOOPS}, archive_loops={LOAD2_ARCHIVE_LOOPS})...')
        start = time.perf_counter()
        command(f'{LOAD2_PATH} {LOAD2_DIR} {LOAD2_WORKERS} {LOAD2_LOOPS} {LOAD2_ARCHIVE_LOOPS}')
        duration = round(time.perf_counter() - start, 3)

        print('Stopping perf...')
        subprocess.run(['sudo', 'pkill', '-INT', '-x', 'perf'])
        perf_proc.wait()

        command('cd libs && sudo perf report -i perf.data > perf_report.txt')
        command('cd libs && sudo perf script -i perf.data > out_rare.perf')
        self.filter_by_spinlock_function()
        command('cd libs && sudo perl libstackcollapse-perf.pl out.perf > out.folded')
        command(f'sudo perl libs/libflamegraph.pl libs/out.folded > {REPORT_PATH}/{L2_FLAMEGRAPH_NAME}')

        print(f'\033[93m\nSpinlock functions found ({len(self.spinlock_functions)}):\n{self.spinlock_functions}\n\033[0m')

        def used_cpu_time(func_name):
            func_command = f"cat {REPORT_PATH}/{L2_FLAMEGRAPH_NAME} | grep -w '{func_name}' | awk '{{print $4}}'"
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
        spinlock_seconds = round((total_used / 100) * duration, 3)

        print(f'\nTotal spinlock CPU share: {total_used}%')
        print(f'Duration: {duration} sec')
        print(f'Spinlock time: {spinlock_seconds} sec\n')

        with open(f'{REPORT_PATH}/{REPORT_FILENAME}', 'w') as f:
            f.write(dumps({
                'time': {
                    'duration': {'Seconds': duration},
                    'spinlock': {'Seconds': spinlock_seconds}
                },
                'total': {'%': {'Total spinlock CPU share': total_used}},
                'detail': {'%': used_cpu_dict}
            }, indent=4))

        with open(f'{REPORT_PATH}/{REPORT_FILENAME}', 'r') as f:
            data = loads(f.read())

        time_df = pd.DataFrame(data['time'])
        total_df = pd.DataFrame(data['total'])
        detail_df = pd.DataFrame(data['detail'])
        time_df.to_html(L2_TIMEDF_NAME)
        total_df.to_html(L2_TOTALDF_NAME)
        detail_df.to_html(L2_DETAILDF_NAME)
        info_list()
