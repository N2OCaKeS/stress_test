#!/bin/bash

#
#Аргументом скрипту следует указать ветку проекта
#

echo git bench = $1
echo git bench = $2
set -vx

git_directory="stress_test"
#dates_file="/home/u/dates.txt"
#args=`cat "$dates_file"`

#Предустановка пакетов
dpkg -s sysstat &> /dev/null || sudo apt-get install sysstat -y

#Клонируем репозиторий, удаляем старый, если есть
cd /home/u/git
python3 git_clone.py

#Подключаем нужную ветку с проектом
cd "$git_directory"
git checkout $1

#Настраиваем окружение и запускаем тест
cd $1
bash prepare.sh

if [ $3 == "kernel" ]; then
    python3 run.py -n $2 -kn $3
else
    python3 run.py -n $2
fi

