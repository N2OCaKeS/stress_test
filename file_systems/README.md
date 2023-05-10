# Нагрузочное тестирование.
## file_system_benchmark
Система нагрузочного тестирования некластерных ФС. 
После проведения тестирования автоматически генерируется архив с отчётом. 

### **Зависимости**
- fabric == 2.6.0
- pandas == 1.3.5
- numpy == 1.21.6
- matplotlib == 3.5.3
- scipy == 1.7.3
- pretty-html-table == 0.9.16

### **Настройка тестовой машины**
Для настройки стендовой машины перед проведение тестирования необходимо склонировать сей проект.
Далее необходимо вписать путь до проекта в переменную **SCRIPT_DIR** в файле fsb_conf.py.
    
    SCRIPT_DIR = '/media/sf_git/stress_test/file_system_benchmark'

Далее необходимо создать виртуальное окружение.

`cd file_system_benchmark && ./fsb_prep.sh`

### **Запуск**
На данный момент реализовано 5 наборов тестов.

- **base_load** - загрузка разными файлами на r/w. от 100 до 1000000 файлов.
- **timeout** - загрузка разными файлами на r/w на протяжении времению 5000 файлов
- **multithreaded** - загрузка разными файлами на r/w на протяжении времени в несколько потоков (3,4,5)
- **big_files** - загрузка разными файлами большого объема (текстовый файл, архив, iso) на r/w
- **fs_mark_count** - бенчмаркинг при помощи fs_mark с итерированием по количеству файлов
- **fs_mark_size** - бенчмаркинг при помощи fs_mark с итерированием по размеру файлов

Запуск теста производить от суперпользователя (root):

`cd file_system_benchmark`

`venv/bin/python3 fsb_run.py`

- **--virtual** - в случае использования виртуального стенда AQS(VBox) 
- **--disk-size** - размер создаваемого тестового диска в случае использования виртуального стенда 
- **--host** - имя тестовой машины (sudcm) 
- **--fs** - тестируемая ФС (ext2, ext3, ext4, fat, ntfs, xfs)
- **--test-set** - набор тестов
- **--parsec** - работать с файлами с ненулевой мандатной меткой


    START_BORDER_FOR_DATA

    STEP_FOR_BORDER

    END_BORDER_FOR_DATA

    TIMEOUT

    NUMBER_OF_TEST_FILES

    TH_START_BORDER_FOR_DATA

    TH_STEP_FOR_BORDER

    TH_END_BORDER_FOR_DATA

### **Проведение тестирования**
Во время проведения тестирования в терминал выводятся сообщения о прохождении каждого теста c пометкой **pass / fail**
### **Обработка результатов**
После прохождения теста в текущей папке file_system_benchmark создается .tar с результатами тестирования в виде графиков и таблиц
(реализовано для fs_mark_count и fs_mark_size).
Общий отчет собран в файле main_report.html

### **Поддерживаемые версии ОС**
  - 1.7

### **Git**

branch: *file_systems*

url: *https://git.astralinux.ru/projects/QA/repos/stress_test/browse/file_system_benchmark?at=refs%2Fheads%2Ffile_systems*

> ssh://git@git.astralinux.ru:7999/qa/stress_test.git

### **Авторы**

rkuznetsov@astralinux.ru