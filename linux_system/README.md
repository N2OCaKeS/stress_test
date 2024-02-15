# Linux_system_benchmark
## UnixBench
Нагрузочное тестирование организовано на базе UnixBench.   


### Test 1
### Syslog-NG
Бенчмарк предоставляет базовый индикатор производительности Unix-подобной системы.   
Тесты для многопроцессорной системы.   
По умолчанию выбранные тесты выполняются дважды - один раз с одной копией каждой тестовой программы,   
запущенной одновременно, и один раз с N копиями, где N - количество CPU.       
Выполняется в режиме **орел**.   


### Test 2
### Syslog-NG parsec
**Доработанный под parsec** бенчмарк предоставляет базовый индикатор производительности    
Unix-подобной системы. Тесты для многопроцессорной системы.     
По умолчанию выбранные тесты выполняются дважды - один раз с одной копией каждой тестовой программы,   
запущенной одновременно, и один раз с N копиями, где N - количество CPU.       
Выполняется в режиме **смоленск**.


#### Зависимости

-  atlassian-python-api
-  pysnooper
-  pretty-html-table
-  pandas 
-  scikit-learn 
-  requests
-  numpy
-  matplotlib
-  lxml
-  bs4
-  pexpect
-  prettytable


#### Проведение тестирования
Тестирование проводится в автоматическом режиме с помощью оркестратора Bendiks.

- [Bendiks](https://life.astralinux.ru/pages/viewpage.action?pageId=253888776&src=contextnavpagetreemode)

#### Обработка результатов
Результаты обрабатываются и выкладываются в пространстве нагрузочного тестирования confluence 
с помощью орекстратора Bendiks в автоматическом режиме.

- [Пространство](https://life.astralinux.ru/pages/viewpage.action?pageId=140673304&src=contextnavpagetreemode)
- [Статистика](https://life.astralinux.ru/pages/viewpage.action?pageId=140674766&src=contextnavpagetreemode)