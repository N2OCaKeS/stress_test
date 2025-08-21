sudo apt install ssh sshpass -y
sudo systemctl enable --now ssh

sudo apt install ceph-common -y

# sudo adduser ceph-adm
# useradd -m -s /bin/bash ceph-adm && echo -e "1\n1" | passwd ceph-adm
useradd -m -s /bin/bash ceph-adm && usermod -p $(openssl passwd -1 '1') ceph-adm
echo "ceph-adm ALL = (root) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/ceph-adm
sudo chmod 0440 /etc/sudoers.d/ceph-adm
sudo pdpl-user -i 63 ceph-adm

if [[ $(cat /etc/astra/build_version) == 1.7* ]]; then
    sudo apt install ceph-deploy -y
else
    sudo apt install cephadm -y
fi

# HOSTS_ENTRIES="
# 10.0.5.11      astra-ceph1
# 10.0.5.12      astra-ceph2
# 10.0.5.13      astra-ceph3
# 10.0.5.20      astra-ceph-admin
# "
# echo "$HOSTS_ENTRIES" | sudo tee -a /etc/hosts > /dev/null

sudo -u ceph-adm ssh-keygen -t rsa -N "" -f /home/ceph-adm/.ssh/id_rsa