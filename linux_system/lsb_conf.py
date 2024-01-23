"""
    Основная рабочая директория
"""
SCRIPT_DIR = '/home/u/git/stress_test/linux_system'

"""
    Названия директорий
"""
REPORT_DIR = '{}/report'.format(SCRIPT_DIR)
INFO_DIR = '{}/report'.format(SCRIPT_DIR)
TEMPLATE_DIR = '{}/template'.format(SCRIPT_DIR)
LOG_DIR = '{}/log'.format(SCRIPT_DIR)
VENV_PATH = '/home/u/python/Python-3.12.1/venv/bin/python3.12'

"""
    Названия основных файлов
"""
REPORT_FILENAME = 'lsb_report.txt'
INFO_FILENAME = 'lsb_info.txt'
RATING_FILENAME = 'lsb_rating.txt'

"""
    Имена тестов
"""
TEST_NAMES = (
    'dhry2reg',
    'whetstone-double',
    'execl',
    'fstime',
    'fsbuffer',
    'fsdisk',
    'pipe',
    'context1',
    'spawn',
    'shell1',
    'shell8',
    'syscall'
)

"""
    Регулярные выражения для парсинга результатов какждого теста
"""
REGEXP_PARSERS = {
    'dhry2reg': r'Dhrystone\s2\susing\sregister\svariables\s*(\d*.\d)\slps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'whetstone-double': r'Double-Precision\sWhetstone\s*(\d*.\d)\sMWIPS\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'execl': r'Execl\sThroughput\s*(\d*.\d)\slps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'fstime': r'File\sCopy\s1024\sbufsize\s2000\smaxblocks\s*(\d*.\d)\sKBps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'fsbuffer': r'File\sCopy\s256\sbufsize\s500\smaxblocks\s*(\d*.\d)\sKBps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'fsdisk': r'File\sCopy\s4096\sbufsize\s8000\smaxblocks\s*(\d*.\d)\sKBps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'pipe': r'Pipe\sThroughput\s*(\d*.\d)\slps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'context1': r'Pipe-based\sContext\sSwitching\s*(\d*.\d)\slps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'spawn': r'Process\sCreation\s*(\d*.\d)\slps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'shell1': r'Shell\sScripts\s\(1\sconcurrent\)\s*(\d*.\d)\slpm\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'shell8': r'Shell\sScripts\s\(8\sconcurrent\)\s*(\d*.\d)\slpm\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
    'syscall': r'System\sCall\sOverhead\s*(\d*.\d)\slps\s*\((\d*.\d)\ss,\s(\d*)\ssamples',
}

"""
    Единицы измерения значений каждого теста
"""
TEST_MEASURE = {
    'dhry2reg': 'lps',
    'whetstone-double': 'MWIPS',
    'execl': 'lps',
    'fstime': 'KBps',
    'fsbuffer': 'KBps',
    'fsdisk': 'KBps',
    'pipe': 'lps',
    'context1': 'lps',
    'spawn': 'lps',
    'shell1': 'lpm',
    'shell8': 'lpm',
    'syscall': 'lps',
}

STAND1_LOWER_LIMIT = 12
STAND1_UPPER_LIMIT = 14
STAND1_STEP = 2

STAND2_LOWER_LIMIT = 12
STAND2_UPPER_LIMIT = 14
STAND2_STEP = 2

STAND3_LOWER_LIMIT = 12
STAND3_UPPER_LIMIT = 14
STAND3_STEP = 2

STAND4_LOWER_LIMIT = 12
STAND4_UPPER_LIMIT = 14
STAND4_STEP = 2

"""
    Описание основных графиков
"""
GRAPH_DESCRIPTIONS = {
    'lsb_dhry2reg_parallel_threads_bench_value_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      '<b>dhry2reg</b>. Этот тест используется для измерения и сравнения производительности компьютеров. Тест фокусируется на обработке строк, поскольку в нем нет операций с плавающей запятой. На него сильно влияют дизайн аппаратного и программного обеспечения, параметры компилятора и компоновщика, оптимизация кода, кэш-память, состояния ожидания и целочисленные типы данных.'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количество параллельных потоков;</li>'
                                      '    <li><b>OY</b>: Количестов итераций в секунду (lps) ;</li>'  # Если разделить на 1757 то будет DMIPS. DMIPS/MHz величина для сравнения разных процов.
                                      '</ul></p>',
    'lsb_whetstone-double_parallel_threads_bench_value_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      '<b>whetstone-double</b>. Этот тест измеряет скорость и эффективность операций с плавающей запятой. Этот тест содержит несколько модулей, предназначенных для представления набора операций, обычно выполняемых в научных приложениях. Используется широкий спектр функций C, включая `sin`, `cos`, `sqrt`, `exp` и `log`, а также математические операции с целыми числами и числами с плавающей запятой, доступ к массивам, условные переходы и вызовы процедур. Этот тест измеряет как целочисленные, так и арифметические операции с плавающей запятой.'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количество параллельных потоков;</li>'
                                      '    <li><b>OY</b>: Миллион Whetstone-инструкций в секунду (MWIPS);</li>'
                                      '</ul></p>',
    'lsb_execl_parallel_threads_bench_value_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      '<b>execl</b>. Этот тест измеряет количество вызовов `execl`, которые могут быть выполнены в секунду. `execl` является частью семейства функций exec, которые заменяют текущий образ процесса новым образом процесса. Эта и многие другие подобные команды являются внешними интерфейсами для функции `execve()`.'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количество параллельных потоков;</li>'
                                      '    <li><b>OY</b>: Количество вызовов `execl` в секунду (lps) ;</li>'
                                      '</ul></p>',
    'lsb_fstime_parallel_threads_bench_value_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      '<b>fstime</b>. Он измеряет скорость, с которой данные могут быть переданы из одного файла в другой с использованием различных размеров буфера. Тесты чтения, записи и копирования файла фиксируют количество символов, которые можно записать, прочитать и скопировать за указанное время (по умолчанию 10 секунд).'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количество параллельных потоков;</li>'
                                      '    <li><b>OY</b>: KBps;</li>'
                                      '</ul></p>',
    'lsb_fsbuffer_parallel_threads_bench_value_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      '<b>fsbuffer</b>. Он измеряет скорость, с которой данные могут быть переданы из одного файла в другой с использованием различных размеров буфера. Тесты чтения, записи и копирования файла фиксируют количество символов, которые можно записать, прочитать и скопировать за указанное время (по умолчанию 10 секунд).'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количество параллельных потоков;</li>'
                                      '    <li><b>OY</b>: KBps;</li>'
                                      '</ul></p>',
    'lsb_fsdisk_parallel_threads_bench_value_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      '<b>fsdisk</b>. Он измеряет скорость, с которой данные могут быть переданы из одного файла в другой с использованием различных размеров буфера. Тесты чтения, записи и копирования файла фиксируют количество символов, которые можно записать, прочитать и скопировать за указанное время (по умолчанию 10 секунд).'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количество параллельных потоков;</li>'
                                      '    <li><b>OY</b>: KBps;</li>'
                                      '</ul></p>',
    'lsb_pipe_parallel_threads_bench_value_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      '<b>pipe</b>. Канал — простейшая форма связи между процессами. Пропускная способность канала — это количество раз (в секунду), которое процесс может записать 512 байт в канал и прочитать их обратно. Тест пропускной способности конвейера не имеет реального аналога в реальном программировании.'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количество параллельных потоков;</li>'
                                      '    <li><b>OY</b>: Пропускная способность канала (lps);</li>'
                                      '</ul></p>',
    'lsb_context1_parallel_threads_bench_value_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      '<b>context1</b>. Этот тест измеряет количество обменов данными через пайп между двумя процессами при котором тестовая программа порождает дочерний процесс, с которым оно осуществляет двунаправленный обмен.'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количество параллельных потоков;</li>'
                                      '    <li><b>OY</b>: Количество обменов данными через пайп между двумя процессами в секунду (lps);</li>'
                                      '</ul></p>',
    'lsb_spawn_parallel_threads_bench_value_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      '<b>spawn</b>. Этот тест измеряет количество раз, когда процесс может разветвляться и пожинать дочерний процесс, который немедленно завершается. Создание процесса относится к фактическому созданию блоков управления процессом и выделению памяти для новых процессов, поэтому это относится непосредственно к пропускной способности памяти. Как правило, этот эталонный тест используется для сравнения различных реализаций вызовов создания процессов операционной системы.'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количество параллельных потоков;</li>'
                                      '    <li><b>OY</b>: Создано блоков управления и распределения памяти в секунду (lps);</li>'
                                      '</ul></p>',
    'lsb_shell1_parallel_threads_bench_value_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      '<b>shell1</b>. Тест сценариев оболочки измеряет, сколько раз в минуту процесс может запускаться и получать набор из одной, двух, четырех и восьми одновременных копий сценариев оболочки, где сценарий оболочки применяет серию преобразований к файлу данных.'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количество параллельных потоков;</li>'
                                      '    <li><b>OY</b>: Количество запусков в минуту (lpm);</li>'
                                      '</ul></p>',
    'lsb_shell8_parallel_threads_bench_value_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      '<b>shell8</b>. Тест сценариев оболочки измеряет, сколько раз в минуту процесс может запускаться и получать набор из одной, двух, четырех и восьми одновременных копий сценариев оболочки, где сценарий оболочки применяет серию преобразований к файлу данных.'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количество параллельных потоков;</li>'
                                      '    <li><b>OY</b>: Количество запусков в минуту (lpm);</li>'
                                      '</ul></p>',
    'lsb_syscall_parallel_threads_bench_value_graph.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">'
                                      '<b>syscall</b>. Это оценивает стоимость входа и выхода из ядра операционной системы, то есть накладные расходы на выполнение системного вызова. Он состоит из простой программы, многократно вызывающей системный вызов `getpid` (который возвращает идентификатор процесса вызывающего процесса). Время выполнения таких вызовов используется для оценки стоимости входа и выхода из ядра.'
                                      '<ul>'
                                      '    <li><b>OX</b>: Количество параллельных потоков;</li>'
                                      '    <li><b>OY</b>: Накладные расходы на выполнение системного вызова в секунду (lps);</li>'
                                      '</ul></p>',
}
