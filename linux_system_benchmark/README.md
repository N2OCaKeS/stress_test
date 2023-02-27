# Нагрузочное тестирование.
## linux_system_benchmark
Тестирование организовано на базе UnixBench. Цель UnixBench — предоставить базовый индикатор производительности Unix-подобной системы. Тесты для многопроцессорной системы. По умолчанию выбранные тесты выполняются дважды - один раз с одной копией каждой тестовой программы, запущенной одновременно, и один раз с N копиями, где N - количество CPU.
### **Зависимости**
- scipy == 1.7.3
- numpy == 1.21.6
- pandas == 1.3.5
- matplotlib == 3.5.3
- pretty-html-table == 0.9.16
- atlassian-python-api == 3.28.1

### **Нейминг**
Файловый тег:
- _**lsb**_

Отчеты:
- _**UnixBench**_

### **Настройка тестовой машины**
Для настройки стендовой машины перед проведение тестирования необходимо:
1. Cклонировать сей проект.

`git clone -b linux_system ssh://git@git.astralinux.ru:7999/qa/stress_test.git`

2. Далее необходимо вписать корректный путь до проекта в переменную **SCRIPT_DIR** в файле **lsb_conf.py**. 
   
`SCRIPT_DIR = '/media/sf_git/stress_test/linux_system_benchmark'`

3. Далее необходимо создать виртуальное окружение c помощью **lsb_prep.sh**.

`cd linux_system_benchmark && ./lsb_prep.sh`

### **Запуск**
Запуск теста производить от суперпользователя (root):

`cd cd linux_system_benchmark`

`venv/bin/python lsb_run.py`

- _**--mode**_  - необязательный выбор режима (по умолчанию _default_)
- _**--stand-name**_ - обязательный параметр имени стендовой машины 

### **Проведение тестирования**
Во время проведения тестирования в терминал выводится информация о текущем тесте.

### **Обработка результатов**
После прохождения теста в текущей папке linux_system_benchmark/report создается .tar с результатами тестирования в виде графиков и таблиц.

### **Поддерживаемые версии ОС**
  - 1.7

### **Git**

branch: *linux_system*

url: *https://git.astralinux.ru/projects/QA/repos/stress_test/browse/linux_system_benchmark?at=refs%2Fheads%2Flinux_system*

> ssh://git@git.astralinux.ru:7999/qa/stress_test.git

### **Авторы**

rkuznetsov@astralinux.ru