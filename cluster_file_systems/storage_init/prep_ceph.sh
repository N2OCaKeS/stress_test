sudo apt install ssh -y
sudo systemctl enable --now ssh

sudo adduser ceph-adm
echo "ceph-adm ALL = (root) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/ceph-adm
sudo chmod 0440 /etc/sudoers.d/ceph-adm
sudo pdpl-user -i 63 ceph-adm

if [[ $(cat /etc/astra/build_version) == 1.7* ]]; then
    sudo apt install ceph-deploy -y
else
    sudo apt install cephadm -y
fi
