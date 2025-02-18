#!/bin/bash

set -vx

#
#Аргументом скрипту следует указать ветку проекта
#
localhost=`hostname -I | awk '{print $1}'`
current_kernel=`uname -r`

cleanup_kernel() {
installed_kernels=$(dpkg --list | grep 'linux-image-[0-9]' | awk '{print $2}')
for kernel in $installed_kernels; do
    if [[ "$kernel" != *"$current_kernel"* ]]; then
        echo "Removing $kernel..."
        sudo apt remove --purge -y $kernel
    fi
done

installed_headers=$(dpkg --list | grep 'linux-headers-[0-9]' | awk '{print $2}')
for header in $installed_headers; do
    if [[ "$header" != *"$current_kernel"* ]]; then
        echo "Removing $header..."
        sudo apt remove --purge -y $header
    fi
done

installed_lam=$(dpkg --list | grep 'linux-astra-modules-[0-9]' | awk '{print $2}')
for lam in $installed_lam; do
    if [[ "$lam" != *"$current_kernel"* ]]; then
        echo "Removing $lam..."
        sudo apt remove --purge -y $lam
    fi
done


echo "Cleaning up..."
sudo apt autoremove -y
}

echo $localhost
echo git bench = $1
echo git bench = $2

git_directory="stress_test"
#dates_file="/home/u/dates.txt"
#args=`cat "$dates_file"`

#Удаление неиспользуемых ядер
cleanup_kernel

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
#sed -i '2i export DEBIAN_FRONTEND=noninteractive' prepare.sh
echo curl http://10.177.103.10:18181/rest/api/dashboard/$localhost/full >> prepare.sh
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

