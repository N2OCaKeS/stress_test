#!/bin/bash

#
#Аргументом скрипту следует указать ветку проекта
#
localhost=`hostname -I | awk '{print $1}'`

echo $localhost
echo git bench = $1
echo git bench = $2
set -vx

git_directory="stress_test"
#dates_file="/home/u/dates.txt"
#args=`cat "$dates_file"`

#Предустановка пакетов
dpkg -s sysstat &> /dev/null || sudo apt-get install sysstat -y
curl http://10.177.103.10:18181/rest/api/dashboard/$localhost/full

#Клонируем репозиторий, удаляем старый, если есть
cd /home/u/git
python3 git_clone.py

#Подключаем нужную ветку с проектом
cd "$git_directory"
git checkout $1

#Настраиваем окружение и запускаем тест
cd $1
bash prepare.sh $1 $3 $5

if [ "$4" == "kernel" ]; then
    python3 run.py -n "$2" -kn "$4"
elif [ "$4" == "balance" ]; then
    python3 run.py -n "$2" -bl "$4"
elif [ "$4" == "oom" ]; then
    python3 run.py -n "$2" -oom "$4"
else
    python3 run.py -n "$2"
fi

