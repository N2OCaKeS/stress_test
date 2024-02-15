# PostgreSQL_benchmark
Комплекс нагрузочного тестирования, направленный на тестирование БД PostgreSQL.


### Test 1
### PostgreSQL_benchmark
Система нагрузочного тестирования БД PostgreSQL с использованием функционала **pgbench**. 
Подключение к базе происходит при помощи сетевого сокета.
Используется один итерационный цикл по кол-ву клиентов с зафиксированными параметрами. 
  - масштабирование = 500 
  - транзакции = 100000 
  - потоки = 200 
  - клиенты = 100 - 500 (шаг равен 100)


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