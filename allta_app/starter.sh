#!/bin/bash

set -vx

#
#Аргументом скрипту следует указать ветку проекта
#
localhost=`hostname -I | awk '{print $1}'`
current_kernel=`uname -r`
testenv=`cat /home/u/testenv_*.conf`
git_directory="stress_test"

cleanup_kernel() {
installed_kernels=$(dpkg --list | grep 'linux-image-[0-9]' | awk '{print $2}')
for kernel in $installed_kernels; do
    if [[ "$kernel" != *"$current_kernel"* && "$kernel" != linux-image-5.10* ]]; then
        echo "Removing $kernel..."
        sudo apt remove --purge -y $kernel
    fi
done

installed_headers=$(dpkg --list | grep 'linux-headers-[0-9]' | awk '{print $2}')
for header in $installed_headers; do
    if [[ "$header" != *"$current_kernel"* && "$header" != linux-headers-5.10* ]]; then
        echo "Removing $header..."
        sudo apt remove --purge -y $header
    fi
done
sudo apt-get install linux-headers-$current_kernel -y

installed_lam=$(dpkg --list | grep 'linux-astra-modules-[0-9]' | awk '{print $2}')
for lam in $installed_lam; do
    if [[ "$lam" != *"$current_kernel"* && "$lam" != linux-astra-modules-5.10* ]]; then
        echo "Removing $lam..."
        sudo apt remove --purge -y $lam
    fi
done


echo "Cleaning up..."
sudo apt autoremove -y
}

echo $localhost
echo git bench = $1

#Удаление неиспользуемых ядер
cleanup_kernel

#Предустановка пакетов
dpkg -s sysstat &> /dev/null || sudo apt-get install sysstat -y

#Клонируем репозиторий, удаляем старый, если есть
cd /home/u/git
sudo rm -r /home/u/git/stress_test
git -c http.extraHeader="Authorization: $2" clone --branch "$1" --single-branch https://git.astralinux.ru/scm/qa/stress_test.git
cd "$git_directory"
cd $1


echo sudo mkdir /etc/docker >> prepare.sh
cat << 'EOF' >> prepare.sh
cat << INTERNAL_EOF > /etc/docker/daemon.json
{
  "insecure-registries": ["allta.devos.astralinux.ru:21503"]
}
INTERNAL_EOF
EOF
echo sudo systemctl restart docker >> prepare.sh

echo curl http://10.177.103.10:18181/rest/api/dashboard/$localhost/full >> prepare.sh
echo sed -i \'s/.*cgroup_controllers.*/cgroup_controllers = [ \"cpu\", \"devices\", \"memory\", \"blkio\", \"cpuacct\" ]/g\' /etc/libvirt/qemu.conf >> prepare.sh
echo sudo systemctl restart libvirtd >> prepare.sh
bash prepare.sh $1 $4 $6

if [[ "$testenv" == 'on' ]]; then
    echo 'Подготовка тестового окружения завершена'
    exit 0
else
    if [ "$5" == "kernel" ]; then
        python3 run.py -n "$3" -kn "$5"
    elif [ "$4" == "balance" ]; then
        python3 run.py -n "$3" -bl "$5"
    elif [ "$5" == "oom" ]; then
        python3 run.py -n "$3" -oom "$5"
    else
        python3 run.py -n "$3"
    fi
fi
