SERVICE_COUNT = 4800 # 4800
TIME_EXEC = 20 #1440 # 1440
SCRIPT_DIR = "/home/u/git/stress_test/syslog_ng"
REPORT_PATH = "{}/report".format(SCRIPT_DIR)
REPORT_FILENAME = 'main_report.html'
TEMPLATE_PATH = "{}/templates".format(SCRIPT_DIR)
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