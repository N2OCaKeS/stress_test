# Virt_benchmark

Комплекс нагрузочного тестирования, направленный на тестирование различных компонентов QEMU/KVM/Libvirt.


### Test 1
### Steal_time
В тесте производится оценка влияния простых операций на производительность CPU в ВМ без нагрузки и с нагрузкой.   

Под простыми операциями подразумевается обращение к инструкции cpuid через ассемблер и получение информации, такой как     
вендор процессора, серийный номер, поддерживаемые функции и т.д.    
Во время теста оценивается steal time на подконтрольных ВМ, а также общее влияние на утилизацию CPU хоста.    
Выполняется в режиме **орел**.  



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
- setuptools
- paramiko

#### Проведение тестирования
Тестирование проводится в автоматическом режиме с помощью оркестратора Bendiks.

- [Bendiks](https://life.astralinux.ru/pages/viewpage.action?pageId=253888776&src=contextnavpagetreemode)

#### Обработка результатов
Результаты обрабатываются и выкладываются в пространстве нагрузочного тестирования confluence 
с помощью орекстратора Bendiks в автоматическом режиме.

- [Пространство](https://life.astralinux.ru/pages/viewpage.action?pageId=140673304&src=contextnavpagetreemode)
- [Статистика](https://life.astralinux.ru/pages/viewpage.action?pageId=140674766&src=contextnavpagetreemode)

