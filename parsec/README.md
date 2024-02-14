# Parsec_benchmark

Комплекс нагрузочного тестирования, направленный на тестирование различных компонентов СЗИ.


### Test 1
### Parsec_impact_FS_test
Производится оценка влияния модулей Parsec на производительность CPU под нагрузкой файловой системы.    

В тесте производится расчет общего времени системных вызовов функций parsec при параллельной нагрузке ФС,  
путем профилирования утилитой perf нагрузочного скрипта.  
Нагрузочный скрипт генерирует случайные файлы в выбранной ФС, размером 1 байт.  
Для теста выбрана ФС: tmpfs, как наиболее быстрая, зависящая не от скорости блочного устройства (hdd/ssd),  
а от ram/cpu.


#### Зависимости

- atlassian-python-api 
- pretty-html-table 
- pandas 
- pysnooper
- scikit-learn 
- requests
- numpy
- matplotlib
- lxml
- bs4
- prettytable

#### Проведение тестирования
Тестирование проводится в автоматическом режиме с помощью оркестратора Bendiks.

- [Bendiks](https://life.astralinux.ru/pages/viewpage.action?pageId=253888776&src=contextnavpagetreemode)

#### Обработка результатов
Результаты обрабатываются и выкладываются в пространстве нагрузочного тестирования confluence 
с помощью орекстратора Bendiks в автоматическом режиме.

- [Пространство](https://life.astralinux.ru/pages/viewpage.action?pageId=140673304&src=contextnavpagetreemode)
- [Статистика](https://life.astralinux.ru/pages/viewpage.action?pageId=140674766&src=contextnavpagetreemode)

