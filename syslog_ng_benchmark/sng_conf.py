SERVICE_COUNT = 4800 # 4900
TIME_EXEC = 1440 # 1440
REPORT_PATH = "/home/u/git/stress_test/syslog_ng_benchmark/report"
TEMPLATE_PATH = "/home/u/git/stress_test/syslog_ng_benchmark/templates"
IMAGE_WIDTH = 23 # 23
IMAGE_HEIGHT = 16 # 16

INFO_FILENAME = '{}/sng_info.txt'.format(REPORT_PATH)

'''
    Описание для графиков отчета
'''
GRAPH_DESCRIPTIONS = {
    'sng_cpu.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">График зависимости загрузки ЦПУ от количества прошедших секунд.<ul><li><b>OX</b>: Количество секунд после запуска тестового сценария;</li><li><b>OY</b>: Загрузка ЦПУ;</li><li><b>Функция</b>: Аппроксимирующая функция точек;</li></ul></p>',
    'sng_memory.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">График зависимости загрузки оперативной памяти от количества прошедших секунд.<ul><li><b>OX</b>: Количество секунд после запуска тестового сценария;</li><li><b>OY</b>: Загрузка оперативной памяти;</li><li><b>Функция</b>: Аппроксимирующая функция точек</li></ul></p>',
    'sng_syslog_memory.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">График зависимости загрузки оперативной памяти сервисом syslog-ng от количества прошедших секунд.<ul><li><b>OX</b>: Количество секунд после запуска тестового сценария;</li><li><b>OY</b>: Загрузка оперативной памяти сервисом syslog-ng;</li><li><b>Функция</b>: Аппроксимирующая функция точек</li></ul></p>',
    'sng_disk.png': '<p style="font-family: Century Gothic, sans-serif; font-size: 14px;">График зависимости загрузки диска (дисковое заполнение) от количества прошедших секунд.<ul><li><b>OX</b>: Количество секунд после запуска тестового сценария;</li><li><b>OY</b>: Загрузка диска (дисковое наполнение)</li><li><b>Функция</b>: Аппроксимирующая функция точек</li></ul></p>',
}