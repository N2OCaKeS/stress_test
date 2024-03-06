# Syslog-NG benchmark
Система нагрузочного тестирования Syslog-NG.   


### Test 1
Создается нагрузка посредством генерации различных событий демонами, берущих за основу скрипт **sng_service_templatе.py**

sng_service_template.py, реализован с помощью библиотеки logging. Его ключевое назначнение - непрерывно в течении заданного времени генерировать события таких уровней как:
- debug
- info
- exception
- warning
- error
- critical
Выполняется в режиме **орел**.   

Для того чтобы изменить время выполнения испытания необходимо в конфигурационном файле **sng_conf.py** изменить параметр **TIME_EXEC_ST3_ST4** в том случае если испытание проводится на тестовых стендах LowServer или MiddleServer в соответсвии с [описанием стендов нагрузочного тестирования](https://life.astralinux.ru/pages/viewpage.action?pageId=192234259), в ином случае изменить параметр **TIME_EXEC.py** (минуты)
```
TIME_EXEC = 90 # 1440
TIME_EXEC_ST3_ST4 = 180
```

Количество однотипных сервисов, генерирующих нагрузку указывается в параметре **SERVICE_COUNT**
```
SERVICE_COUNT = 4800
```
Их количетсво рекомендуется подбирать таким образом, чтобы в момент выполнения испытания загруженность оперативной памяти была ~90%

При расчете итогового рейтинга участвуют такие критерии как:
- Загрузка CPU
- Загрузка RAM
- Загрузка RAM сервисом syslog-ng
- Загрузка диска

Так как данные критерии было принято обозначить как равноценные их весовые коэффициенты равны 0.25, данные критерии являются негативными

После окончания испытания полученные результаты записываются в файл sng_report.txt, который включает в себя информацию такую как:
- Astra_version
- Astra_mode
- Kernel
- Service_count
- Load_time_execution
- Rating_CPU
- Rating_memory
- Rating_Syslog-NG_memory
- Rating_disk
- Total_rating

Далее просматриваются все логи в директории /var/log и происходит поиск возникших во время испытания ошибкок. Этот процесс занимает значительное время

#### Зависимости

-  atlassian-python-api
-  pandas 
-  scikit-learn 
-  requests
-  numpy
-  matplotlib
-  lxml
-  bs4


#### Проведение тестирования
Тестирование проводится в автоматическом режиме с помощью оркестратора Bendiks.

- [Bendiks](https://life.astralinux.ru/pages/viewpage.action?pageId=253888776&src=contextnavpagetreemode)

#### Обработка результатов
Результаты обрабатываются и выкладываются в пространстве нагрузочного тестирования confluence 
с помощью орекстратора Bendiks в автоматическом режиме.

- [Пространство](https://life.astralinux.ru/pages/viewpage.action?pageId=140673304&src=contextnavpagetreemode)
- [Статистика](https://life.astralinux.ru/pages/viewpage.action?pageId=140674766&src=contextnavpagetreemode)