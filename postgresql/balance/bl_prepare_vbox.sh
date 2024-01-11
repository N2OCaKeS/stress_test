#!/bin/bash

#sudo echo deb ftp://10.177.5.111/astra/testing/1.8.0.2/devel 1.8_x86-64 main contrib non-free >> /etc/apt/sources.list
#sudo apt update -y


#ansible
sudo apt-get install ansible -y
sudo apt-get install sshpass -y

#python
sudo apt-get install -y python3-paramiko python3-pip python3-psycopg2

#virtualbox
#wget -r -nH --cut-dirs=3 --no-parent ftp://qa111.devos.astralinux.ru/packages/virtualbox
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
sudo dpkg -i virtualbox-*.deb
sudo apt install pkexec -y
sudo apt install policykit-1 -y
sudo yes | VBoxManage extpack install --replace Oracle_VM_VirtualBox_Extension_Pack-*.vbox-extpack

#vagrant
wget -r -nH --cut-dirs=2 --no-parent ftp://qa111.devos.astralinux.ru/packages/vagrant
sudo dpkg -i vagrant_2.2.19_x86_64.deb


#source "provision/env_provision.sh"

# uid check
#if test $(id -u) == 0; then
#  >&2 echo -e "\e[91mERROR (!) Required: id != 0\e[0m"
#  exit 1
#fi

# install virtualbox-6.1 & ext.pack
# if test ! "$(dpkg -l | awk '{print $2}' | grep ^virtualbox-6.1$)"; then
#   wget -r -nH --cut-dirs=3 --no-parent \
#   ftp://qa111.devos.astralinux.ru/packages/virtualbox 2>/dev/null

#   if [ $? != 0 ]; then
#     >&2 echo -e "\e[91mERROR (!) Package vbox import\e[0m"
#     exit 1
#   fi

  # sudo apt install gcc make perl -y
  # sudo dpkg -i libvpx5_1.7.0-3+deb10u1_amd64.deb
  # sudo dpkg -i virtualbox-6.1_6.1.36-152435~Debian~buster_amd64.deb
  # if [ $? != 0 ]; then
  #   >&2 echo -e "\e[91mERROR (!) VirtualBox package installation\e[0m"
  #   exit 1
  # fi

#   sudo yes | VBoxManage extpack install Oracle_VM_VirtualBox_Extension_Pack-6.1.36a-152435.vbox-extpack
#   if [ $? != 0 ]; then
#     >&2 echo -e "\e[91mERROR (!) VirtualBox Extension Pack installation\e[0m"
#     exit 1
#   fi
#   if test ! "$(vboxmanage list extpacks | awk '{print $2}' | grep 6.1.36$)"; then
#     >&2 echo -e "\e[91mERROR (!) wrong install VirtualBox Extension Pack\e[0m"
#     exit 1
#   fi

#   sudo rm libvpx5_1.7.0-3+deb10u1_amd64.deb
#   sudo rm virtualbox-6.1_6.1.36-152435~Debian~buster_amd64.deb
#   sudo rm Oracle_VM_VirtualBox_Extension_Pack-6.1.36a-152435.vbox-extpack
# fi

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

  # vagrant installation
  sudo dpkg -i vagrant_2.2.19_x86_64.deb
  if [ $? != 0 ]; then
    >&2 echo -e "\e[91mERROR (!) Vagrant package installation\e[0m"
    exit 1
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
# for plugin in vagrant-vbguest; do
#   if test ! "$(vagrant plugin list | grep $plugin)"; then
#     wget -O /tmp/gems.tar.gz ftp://qa111.devos.astralinux.ru/packages/vagrant-plugins/gems.tar.gz
#     mkdir -p ~/.vagrant.d/gems/2.7.4
#     tar -C "$HOME/.vagrant.d/gems/2.7.4" -xvf /tmp/gems.tar.gz
#     wget -O "$HOME/.vagrant.d/plugins.json" ftp://qa111.devos.astralinux.ru/packages/vagrant-plugins/plugins.json  
#     [ $? != 0 ] && exit 1
#   fi
# done

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
  exit 1
fi

# # create NAT network for vbox
# if test ! "$(vboxmanage natnetwork list | grep $vbox_nat)"; then
#   vboxmanage natnetwork add --netname "$vbox_nat" \
#   --network "$vbox_nat_ip/$vbox_subnet_mask" --enable --dhcp on

#   if [ $? != 0 ]; then
#     >&2 echo -e "\e[91mERROR (!) Can't create NAT - '$vbox_nat' network\e[0m"
#     exit 1
#   fi
# fi

# # create forwarding rules for NAT network
# for dom in ${vbox_machines[*]}; do
#   declare -n vm_hash=$dom
#   vboxmanage natnetwork modify --netname $vbox_nat --port-forward-4 \
#   "$dom:tcp:[127.0.0.1]:${vm_hash[forward-port]}:[${vm_hash[ip]}]:22" 2>/dev/null
# done




#vagrant box add http://qa111.devos.astralinux.ru/vault/vagrant/smol-1.8.0.json --force
#UPDATE='smolensk-vanilla-gui/1.8.0.2' vagrant up

