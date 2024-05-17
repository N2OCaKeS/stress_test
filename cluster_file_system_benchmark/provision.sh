#!/bin/bash

set -vx

dpkg -s jq &> /dev/null || sudo apt-get install jq -y
sudo wget http://bendiks.devos.astralinux.ru/rest/api/get-repo-path -O releases.json
sudo jq -r ".\"$1\"[]" releases.json | sudo tee /etc/apt/sources.list
cat << EOF | sudo tee /etc/apt/preferences.d/devel
Package: *
Pin: release l=devel
Pin-Priority: 500
EOF
sudo apt update

sudo astra-update -A -T -r
sudo apt-get install -y sysstat
sudo apt-get install -y netcat
sudo apt-get install linux-[5-6].*-generic -y
sudo apt-get install linux-[5-6].*-lowlatency -y
sudo apt-get install -y libffi-dev gcc make libpdp-dev


kernel="$2"
kernel_conf=$(sudo cat /boot/grub/grub.cfg | grep menuentry_id | awk '{print $17}' | grep $kernel | tr -d "\'")
if ! grep -q '^GRUB_DEFAULT=' /etc/default/grub; then
    echo 'GRUB_DEFAULT=0' | sudo tee -a /etc/default/grub
fi
sudo sed -i "s/GRUB_DEFAULT=.*/GRUB_DEFAULT=$kernel_conf/" /etc/default/grub
sudo update-grub
cat /etc/default/grub | grep GRUB_DEFAULT


echo "PermitRootLogin yes" >> /etc/ssh/sshd_config
sudo systemctl restart ssh
echo -e "1\n1" | sudo passwd

sudo apt-get update -y  && sudo apt-get install -y nfs-common 
sleep 10

sudo mkdir /git

sudo apt install -y python3-pip

sudo mount $3:/home/u/git/stress_test/cluster_file_system_benchmark /git

sudo pip3 install fabric --break-system-packages

sudo pip3 install -r /git/req.txt --break-system-packages
