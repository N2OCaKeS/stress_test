#!/bin/bash

18repo() {
cat << EOF > /etc/apt/sources.list
deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.0/1.8.0.14/installation 1.8_x86-64 main contrib non-free
deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.0/1.8.0.14/extended-repository 1.8_x86-64 main contrib non-free
deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.0/1.8.0.14/devel-repository 1.8_x86-64 main contrib non-free
EOF
}

17repo() {
cat << EOF > /etc/apt/sources.list
deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.1/1.7.1.8/installation/ 1.7_x86-64 main contrib non-free
deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.1/1.7.1.8/base-repository/ 1.7_x86-64 main contrib non-free
deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.1/1.7.1.8/update-repository/ 1.7_x86-64 main contrib non-free
deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.1/EXT_latest/extended-repository/ 1.7_x86-64 main contrib non-free
EOF
}

test "$(grep 1.7 /etc/astra_version)" && 17repo && sudo apt update
test "$(grep 1.8 /etc/astra_version)" && 18repo && sudo apt update

dpkg -s jq &> /dev/null || sudo apt-get install jq -y
wget http://allta.devos.astralinux.ru/rest/api/get-repo-path -O releases.json
sudo jq -r ".\"$2\"[]" releases.json > /etc/apt/sources.list
cat << EOF | sudo tee /etc/apt/preferences.d/devel
Package: *
Pin: release l=devel
Pin-Priority: 500

Package: *
Pin: release l=extended
Pin-Priority: 500
EOF
sudo apt update

# create venv 
sudo apt-get install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev libsqlite3-dev wget libbz2-dev
sudo apt-get install -y libffi-dev strace 
sudo apt-get install -y libcurl4-gnutls-dev
sudo apt-get install -y rustc cargo
sudo apt-get install -y python3-requests

if test "$(grep -E '1.8.*' /etc/astra_version)"; then
    sudo apt-get install -y linux-tools-6.1*-generic
    sudo apt-get install -y linux-tools-common-6.*
else
    sudo apt-get install -y linux-tools-5.10*-generic linux-tools-5.15*-generic linux-tools-common-5.15*
    sudo apt-get install -y linux-tools-5.15*-lowlatency
fi

sudo mkdir /home/u/python
cd /home/u/python
sudo wget -P /home/u/python ftp://10.177.103.10/python/*
tar -xf Python-3.12.1.tar.xz
cd Python-3.12.1
./configure --enable-optimizations
make -j 6
sudo make altinstall

python3.12 -m venv venv
source venv/bin/activate

cd /home/u/git/stress_test/$1
python3.12 -m pip install --upgrade pip
python3.12 -m pip install -r req.txt
if [[ $? != 0 ]]; then
    python3.12 -m pip install -r req.txt
fi

#ansible
sudo apt-get install ansible -y
sudo apt-get install sshpass -y

#lvirt
apt-get install virt-manager libvirt-clients libvirt-daemon libvirt-dev libvirt0 -y

#vagrant
wget -r -nH --cut-dirs=2 --no-parent ftp://qa111.devos.astralinux.ru/packages/vagrant
sudo dpkg -i vagrant_2.2.19_x86_64.deb
sudo adduser $USER libvirt

for group in kvm libvirt libvirt-qemu libvirt-admin; do
  if test ! "$(groups | grep ${group})"; then
    sudo usermod -aG ${group} $USER 
  fi
done

# check 'vbguest' (Vbox Guests) plugin, install
for plugin in vagrant-vbguest; do
  if test ! "$(vagrant plugin list | grep $plugin)"; then
    wget -O /tmp/gems.tar.gz ftp://qa111.devos.astralinux.ru/packages/vagrant-plugins/gems.tar.gz
    mkdir -p ~/.vagrant.d/gems/2.7.4
    tar -C "$HOME/.vagrant.d/gems/2.7.4" -xvf /tmp/gems.tar.gz
    wget -O "$HOME/.vagrant.d/plugins.json" ftp://qa111.devos.astralinux.ru/packages/vagrant-plugins/plugins.json  
    [ $? != 0 ] && exit 1
  fi
done


if [[ $(egrep -c '(vmx|svm)' /proc/cpuinfo) -gt 0 ]]; then
    echo "supports hardware virtualization is ok"
else 
    echo "system does not supports hardware virtualization" 
fi