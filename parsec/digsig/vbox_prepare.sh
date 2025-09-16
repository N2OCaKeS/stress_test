#!/bin/bash

set -vx

#virtualbox
wget -r -nH --cut-dirs=3 --no-parent ftp://qa111.devos.astralinux.ru/packages/vbox7
wget -r -nH --cut-dirs=3 --no-parent ftp://qa111.devos.astralinux.ru/stress_reports/vbox
wget http://security.debian.org/debian-security/pool/updates/main/o/openssl/libssl1.1_1.1.1n-0+deb10u6_amd64.deb
sudo apt-get install plymouth-themes -y
sudo apt install gcc make perl rsync -y
sudo apt install libopus0 -y
sudo apt install libqt5opengl5 -y 
sudo apt install libqt5printsupport5 -y
sudo apt install libsdl1.2debian -y
sudo dpkg -i libssl1.1_1.1.1n-0+deb10u6_amd64.deb 
sudo dpkg -i libvpx5_1.7.0-3+deb10u1_amd64.deb
sudo apt install psmisc -y
sudo apt install pkexec -y
sudo apt install policykit-1 -y

ASTRA_VERSION=$(cat /etc/astra_version)
KERNEL_VERSION=$(uname -r)

if [[ "$ASTRA_VERSION" =~ ^1.8 ]] || [[ "$KERNEL_VERSION" =~ ^6.1 ]]; then
#if grep -qE '1.8.*' /etc/astra_version || uname -r | grep -q 6.1; then
  sudo dpkg -i virtualbox-7.0_7.0.20*.deb
  if [[ $? != 0 ]]; then
    sudo apt install -fy
    sudo dpkg -i virtualbox-7.0_7.0.20*.deb
  fi
  sudo yes | VBoxManage extpack install --replace Oracle_VM_VirtualBox_Extension_Pack-7.0.20*.vbox-extpack
elif [[ "$ASTRA_VERSION" =~ ^1.7 ]] && ! [[ "$KERNEL_VERSION" =~ ^6.1 ]]; then
#elif grep -qE '1.7.*' /etc/astra_version && ! uname -r | grep -q '^6\\.1'; then
  sudo dpkg -i virtualbox-6.1*.deb
  if [[ $? != 0 ]]; then
    sudo apt install -fy
    sudo dpkg -i virtualbox-6.1*.deb
  fi
  sudo yes | VBoxManage extpack install --replace Oracle_VM_VirtualBox_Extension_Pack-6.1*.vbox-extpack
fi



#vagrant
wget -r -nH --cut-dirs=2 --no-parent ftp://qa111.devos.astralinux.ru/packages/vagrant
if [[ "$ASTRA_VERSION" =~ ^1.8 ]] || [[ "$KERNEL_VERSION" =~ ^6.1 ]]; then
  sudo dpkg -i vagrant_2.4.1-1_x86_64.deb
elif [[ "$ASTRA_VERSION" =~ ^1.7 ]] && ! [[ "$KERNEL_VERSION" =~ ^6.1 ]]; then
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
  sudo rm vagrant_2.2.19_x86_64.deb
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
if [[ "$ASTRA_VERSION" =~ ^1.8 ]] || [[ "$KERNEL_VERSION" =~ ^6.1 ]]; then
  for plugin in vagrant-vbguest; do
    if test ! "$(vagrant plugin list | grep $plugin)"; then
      wget -O /tmp/gems.tar.gz ftp://qa111.devos.astralinux.ru/packages/vagrant-plugins/gems.tar.gz
      mkdir -p ~/.vagrant.d/gems/3.1.4
      tar -C "$HOME/.vagrant.d/gems/3.1.4" -xvf /tmp/gems.tar.gz
      wget -O "$HOME/.vagrant.d/plugins.json" ftp://qa111.devos.astralinux.ru/packages/vagrant-plugins/plugins.json
      [ $? != 0 ] && exit 1
    fi
  done
if [[ "$ASTRA_VERSION" =~ ^1.7 ]] && ! [[ "$KERNEL_VERSION" =~ ^6.1 ]]; then
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


# important group for vbox environment
if test ! "$(cat /etc/group | grep vboxusers)"; then
  sudo groupadd vboxusers
fi

for group in vboxusers; do
  if test ! "$(groups | grep ${group})"; then
    sudo usermod -aG ${group} $USER 
  fi
done

forward_path=/proc/sys/net/ipv4/conf/all

if test -d $forward_path; then
  if ! test "$(cat $forward_path/forwarding | grep 1)"; then
    echo 1 | sudo tee $forward_path/forwarding
  fi
else
  >&2 echo -e "\e[91mERROR (!) $forward_path/ path not exist\e[0m"
  exit 1
fi

if test -e /etc/sysctl.conf; then
  if test ! "$(cat /etc/sysctl.conf | grep ^net.ipv4.ip_forward=1)"; then
    echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.conf
  fi
else
  >&2 echo -e "\e[91mERROR (!) /etc/sysctl.conf not exist\e[0m"
  exit 1
fi


# check 'Extension Pack'
if test ! "$(vboxmanage list extpacks | grep "Oracle VM VirtualBox Extension Pack")"; then
  >&2 echo -e "\e[91mERROR (!) 'Extension Pack' is absent\e[0m"
  exit 1
fi

# check 'Guest Additions'
if test ! -e /usr/share/virtualbox/VBoxGuestAdditions.iso; then
  >&2 echo -e "\e[91mERROR (!) /usr/share/virtualbox/VBoxGuestAdditions.iso not exist\e[0m"
  exit 1
fi

# check $USER groups, vboxusers required
if test ! "$(groups | grep vboxusers)"; then
  >&2 echo -e "\e[91mERROR (!) '$USER' is not a member of the 'vboxusers' group\e[0m"
  echo -e "\e[91mPlease, reboot your system and restart this script again\e[0m"
  exit 0
fi


