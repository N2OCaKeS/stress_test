#!/bin/bash

set -vx

17repo()
{
cat << EOF > /etc/apt/sources.list
deb ftp://qa111.devos.astralinux.ru/astra/stable/1.7/base-repository$1 1.7_x86-64 main contrib non-free
EOF
}

17repo_test()
{
cat << EOF > /etc/apt/sources.list
deb ftp://qa111.devos.astralinux.ru/astra/testing/1.7-testing/base-repository 1.7_x86-64 main contrib non-free
EOF
}

18repo_test()
{
cat << EOF > /etc/apt/sources.list
deb ftp://qa111.devos.astralinux.ru/astra/testing/1.8-testing/installation 1.8_x86-64 main contrib non-free
deb ftp://qa111.devos.astralinux.ru/astra/testing/1.8-testing/devel 1.8_x86-64 main non-free contrib
EOF
}

echo $1 > /etc/astra_update_box

test "$(grep 1.7.0 /etc/astra_update_box)" && 17repo
test "$(grep 1.7.1 /etc/astra_update_box)" && 17repo "-1"
test "$(grep 1.7.2 /etc/astra_update_box)" && 17repo "-2"
test "$(grep 1.7.2.UU.1 /etc/astra_update_box)" && 17repo "-2.1"
test "$(grep 1.7.3 /etc/astra_update_box)" && 17repo "-3"
test "$(grep 1.7.3.UU.1 /etc/astra_update_box)" && 17repo "-3.1"
test "$(grep 1.7.3.UU.2 /etc/astra_update_box)" && 17repo "-3.2"
test "$(grep 1.7.4 /etc/astra_update_box)" && 17repo "-4"
test "$(grep 1.7.4.UU.1 /etc/astra_update_box)" && 17repo "-4.1"
test "$(grep 1.7.5 /etc/astra_update_box)" && 17repo "-5"
test "$(grep 1.7.5.UU.1 /etc/astra_update_box)" && 17repo_test
test "$(grep 1.7.6 /etc/astra_update_box)" && 17repo_test
test "$(grep 1.8.0 /etc/astra_update_box)" && 18repo_test
sudo apt-get update
sudo astra-update -A -T -r
sudo apt-get install -y sysstat
sudo apt-get install -y netcat
sudo apt-get install linux-[5-6].*-generic -y
sudo apt-get install linux-[5-6].*-lowlatency -y


kernel="$2"
kernel_conf=$(sudo cat /boot/grub/grub.cfg | grep menuentry_id | awk '{print $17}' | grep $kernel | tr -d "\'")
if ! grep -q '^GRUB_DEFAULT=' /etc/default/grub; then
    echo 'GRUB_DEFAULT=0' | sudo tee -a /etc/default/grub
fi
sudo sed -i "s/GRUB_DEFAULT=.*/GRUB_DEFAULT=$kernel_conf/" /etc/default/grub
sudo update-grub
cat /etc/default/grub | grep GRUB_DEFAULT


cat /etc/astra_version
cat /etc/astra_version > /home/av.txt

