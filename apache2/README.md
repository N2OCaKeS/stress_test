# Apache_benchmark

Комплекс нагрузочного тестирования, направленный на тестирование различных компонентов Apache2.


### Test 1
### Apache_RP
Нагрузочное тестирование веб сервера Apache2 с Reverse Proxy, посредством Apache Benchmark   
Выполняется в режиме **смоленск**.  

### Test 2
### Apache_Bench_PAM

Нагрузочное тестирование веб сервера Apache2 с PAM-аутентификацией (Pluggable Authentication Modules — подключаемые модули аутентификации), посредством Apache Benchmark.  
Тест разворачивает 2 ВМ (виртуальные машины): `testvm1` — сервер, `testvm2` — клиент.  
Сравнивает производительность Apache2 в двух режимах:

- **С PAM** (AstraMode on): запросы выполняются от пользователей с разными метками МАС (мандатное управление доступом) — без категорий и с категориями.
- **Без PAM** (AstraMode off): запросы выполняются без аутентификации.

Нагрузка генерируется с нарастающим параллелизмом (concurrency) от минимального шага до максимального значения.  
Выполняется в режиме **смоленск**.

#### Зависимости

- fabric
- paramiko
- invoke
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

