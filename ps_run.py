from libs.libparsec import (check_output_command,
                            command)


#количество создаваемых потоков 
concurrency = 1
#количество циклов для каждого потока
counter = 1000

load_command = f'cd libs && time ./load_parsec /tmp {concurrency} {counter}'
load_rare_results = check_output_command(load_command)
find_args = ['real', 'user', 'sys']

print(load_rare_results)

#load_rare_results = """double free or corruption (out)
#/bin/sh: строка 1: 17361 Аварийный останов         ./load_parsec /tmp 10 1000
#real    0m11,150s
#user    0m0,154s
#sys     0m6,594s"""

load_rare_results = [
    result.replace('\t', '-').split('-') for match in find_args for result in \
    load_rare_results.strip().split('\n') if result.startswith(match)
    ]

#print(load_rare_results)

load_rare_results = {i[0]:i[1] for i in load_rare_results}
load_results = {
    key:{'Seconds':int(value[0]) * 60 + float(value[1].replace(',', '.')) 
         for value in [load_rare_results[key].strip('s').split('m')]} for key in find_args
}

print(load_results)

