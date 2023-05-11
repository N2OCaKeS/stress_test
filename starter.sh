#!/bin/bash

#
#Аргументом скрипту следует указать ветку проекта
#

echo git bench = $1
set -vx

git_directory="stress_test"
#dates_file="/home/u/dates.txt"
#args=`cat "$dates_file"`

#Клонируем репозиторий, удаляем старый, если есть
cd /home/u/git
./git_clone.py

#Подключаем нужную ветку с проектом
cd "$git_directory"
git checkout $1

#Настраиваем окружение и запускаем тест
cd $1
./prepare.sh
./run.py

