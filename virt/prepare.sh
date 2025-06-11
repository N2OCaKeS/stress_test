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
sudo apt-get install -y libffi-dev strace zip unzip
sudo apt-get install -y libcurl4-gnutls-dev
sudo apt-get install -y rustc cargo
sudo apt-get install -y python3-requests
sudo apt-get install -y linux-tools-`uname -r`

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
pip install -i http://10.177.103.10:3141/root/release --trusted-host 10.177.103.10:3141 allta

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
sudo adduser $USER libvirt

#vagrant
wget -r -nH --cut-dirs=2 --no-parent ftp://qa111.devos.astralinux.ru/packages/vagrant
if test "$(grep -E '1.8.*' /etc/astra_version)"; then
  sudo dpkg -i vagrant_2.4.3-1_x86_64.deb
elif test "$(grep -E '1.7.*' /etc/astra_version)"; then
  sudo dpkg -i vagrant_2.2.19_x86_64.deb
fi



if test ! "$(dpkg -l | awk '{print $2}' | grep ^vagrant$)"; then
  # vagrant package download
  wget -r -nH --cut-dirs=2 --no-parent \
  ftp://qa111.devos.astralinux.ru/packages/vagrant 2>/dev/null

  if [ $? != 0 ]; then
    >&2 echo -e "\e[91mERROR (!) Package import\e[0m"
    exit 1
  fi

  # clean old environment
  if test -d /opt/vagrant/embedded/gems; then
    sudo rm -rf /opt/vagrant/embedded/gems/*
  fi


  # remove garbage
  sudo rm vagrant_*.deb
  sudo rm astra-vagrant.tar.gz
  sudo rm -rf log/
fi

# extra packages installation, hese packages script use
sudo apt-get update
for pack in nano diffutils ssh sshpass openssh-client curl wget whiptail ansible jq python3-requests; do
  if test ! "$(dpkg -l | awk '{print $2}' | grep ^$pack$)"; then
    sudo apt-get -y install $pack
    [ $? != 0 ] && apt-get -f -y install
  fi
done

if [ ! -d ~/.vagrant.d/ ]; then
  cd /tmp/ && vagrant init
fi

# check 'vbguest' (Vbox Guests) plugin, install
if test "$(grep -E '1.8.*' /etc/astra_version)"; then
  for plugin in vagrant-vbguest; do
    if test ! "$(vagrant plugin list | grep $plugin)"; then
      wget -O /tmp/gems.tar.gz ftp://qa111.devos.astralinux.ru/packages/vagrant-plugins/gems.tar.gz
      mkdir -p ~/.vagrant.d/gems/3.1.4
      tar -C "$HOME/.vagrant.d/gems/3.1.4" -xvf /tmp/gems.tar.gz
      wget -O "$HOME/.vagrant.d/plugins.json" ftp://qa111.devos.astralinux.ru/packages/vagrant-plugins/plugins18.json
      [ $? != 0 ] && exit 1
    fi
  done
elif test "$(grep -E '1.7.*' /etc/astra_version)"; then
  for plugin in vagrant-vbguest; do
    if test ! "$(vagrant plugin list | grep $plugin)"; then
      wget -O /tmp/gems.tar.gz ftp://qa111.devos.astralinux.ru/packages/vagrant-plugins/gems.tar.gz
      mkdir -p ~/.vagrant.d/gems/2.7.4
      tar -C "$HOME/.vagrant.d/gems/2.7.4" -xvf /tmp/gems.tar.gz
      wget -O "$HOME/.vagrant.d/plugins.json" ftp://qa111.devos.astralinux.ru/packages/vagrant-plugins/plugins.json
      [ $? != 0 ] && exit 1
    fi
  done
fi


if [[ $(egrep -c '(vmx|svm)' /proc/cpuinfo) -gt 0 ]]; then
    echo "supports hardware virtualization is ok"
else 
    echo "system does not supports hardware virtualization" 
fi

