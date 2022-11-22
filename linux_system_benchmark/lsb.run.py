# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: vgusev@astralinux.ru
# ; Date: 2022
# ;===========================================================
# TODO: Название файла!!!!
import argparse
import subprocess

parser = argparse.ArgumentParser(description="DESCRIPTION")
parser.add_argument('-m', '--mode',
                    action='store',
                    required=False,
                    choices=['default',
                             'extended',],
                    default='default',
                    help='help me',
                    dest='MODE')

# TODO: PEP8
# TODO: Если уж написал то перемести туда где он должен быть lib/* и иметь соответсвующее имя
def test_run():

    #Список тестов
    # TODO: такие вещи лучше выносить в конфиг и в многострочном формате
    tests_list = ['dhry2reg', 'whetstone-double', 'syscall', 'pipe', 'context1', 'spawn', 'execl', 'fstime-w', 'fstime-r', 'fstime', 
            'fsbuffer-w', 'fsbuffer-r', 'fsbuffer', 'fsdisk-w ', 'fsdisk-r', 'fsdisk', 'shell1', 'shell8']  
    
    #Количество прогонов тестов
    # TODO: 'run' это глагол
    #       Это тоже должно быть в конфиге
    runs_number = 12    
    #Количество тестов

    tests_number = len(tests_list) - 1
    test_name = tests_list[tests_number]
    cycles = runs_number

    # TODO: for!!!???
    #
    while tests_number > 0:

        while cycles > 0:

            # TODO: Почему команда в виде []?
            #       f-string не юзаем пока что не везде поддерживается либо format(), прямая конкатенация по ситуации
            #       управление выводом лучше явно указывать
            subprocess.run(['cd byte-unixbench/UnixBench && ./Run -c 6 -i 1 %s' %(test_name)], shell=True)
            cycles = cycles - 1

        tests_number = tests_number - 1
        test_name = tests_list[tests_number]
        cycles = runs_number

args = parser.parse_args() # TODO: Почему это здесь, а не в районе 20?

# TODO: структура вот осюда начинается по факту и её нет
#       нет созадния файлов логов/репортов или их очистки
#       несколько раз обсуждали что-то вроде TestSet.test() и где?
if args.MODE == 'default':
    test_run()
elif args.MODE == 'extended':
    print("In developing")
