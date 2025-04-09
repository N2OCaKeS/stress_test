#!/bin/bash


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


if test "$(grep -E '1.8.*' /etc/astra_version)"; then
  sudo dpkg -i virtualbox-7.0_7.0.20*.deb
  if [[ $? != 0 ]]; then
    sudo apt install -fy
    sudo dpkg -i virtualbox-7.0_7.0.20*.deb
  fi
  sudo yes | VBoxManage extpack install --replace Oracle_VM_VirtualBox_Extension_Pack-7.0.20*.vbox-extpack
elif test "$(grep -E '1.7.*' /etc/astra_version)"; then
  sudo dpkg -i virtualbox-6.1*.deb
  if [[ $? != 0 ]]; then
    sudo apt install -fy
    sudo dpkg -i virtualbox-6.1*.deb
  fi
  sudo yes | VBoxManage extpack install --replace Oracle_VM_VirtualBox_Extension_Pack-6.1*.vbox-extpack
fi



#vagrant
wget -r -nH --cut-dirs=2 --no-parent ftp://qa111.devos.astralinux.ru/packages/vagrant
if test "$(grep -E '1.8.*' /etc/astra_version)"; then
  sudo dpkg -i vagrant_2.4.1-1_x86_64.deb
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
if test "$(grep -E '1.8.*' /etc/astra_version)"; then
  for plugin in vagrant-vbguest; do
    if test ! "$(vagrant plugin list | grep $plugin)"; then
      wget -O /tmp/gems.tar.gz ftp://qa111.devos.astralinux.ru/packages/vagrant-plugins/gems.tar.gz
      mkdir -p ~/.vagrant.d/gems/3.1.4
      tar -C "$HOME/.vagrant.d/gems/3.1.4" -xvf /tmp/gems.tar.gz
      wget -O "$HOME/.vagrant.d/plugins.json" ftp://qa111.devos.astralinux.ru/packages/vagrant-plugins/plugins.json
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

sleep 2

#Start VM create
VMS=("virtual-station1" "virtual-station2" "virtual-station3" "virtual-station4")
BOX_NAME=orel-vanilla-gui/1.7.5
BOX_URL=ftp://10.177.103.10/boxes/box/1.7.5.o.box
KERNEL=5.10.190-1-generic
RC=1.7.5

declare -A vm_mac_map
vm_mac_map=( ["virtual-station1"]="080027ABCD01"
             ["virtual-station2"]="080027ABCD02"
             ["virtual-station3"]="080027ABCD03"
             ["virtual-station4"]="080027ABCD04" )

sleep 1
echo vagrant box add $BOX_NAME $BOX_URL --force
echo UPDATE=$BOX_NAME BOX_URL=$BOX_URL KERNEL=$KERNEL RC=$RC vagrant up --provider=virtualbox

sudo vagrant box add orel-vanilla-gui/1.7.5 ftp://10.177.103.10/boxes/box/1.7.5.o.box --force
sudo UPDATE=orel-vanilla-gui/1.7.5 BOX_URL=ftp://10.177.103.10/boxes/box/1.7.5.o.box KERNEL=5.10.190-1-generic RC=1.7.5 vagrant up --provider=virtualbox
sleep 1

BRIDGE_IFACE=`vboxmanage list bridgedifs | grep Name | awk '{print$2}' | head -n 1`

for vm in "${VMS[@]}"; do
    VBoxManage controlvm "$vm" poweroff
    sleep 1
    VBoxManage modifyvm "$vm" --nic1 bridged
    VBoxManage modifyvm "$vm" --nested-hw-virt on
    VBoxManage modifyvm "$vm" --bridgeadapter1 "$BRIDGE_IFACE"
    VBoxManage modifyvm "$vm" --macaddress1 "${vm_mac_map[$vm]}"
    VBoxManage startvm "$vm" --type headless
    sleep 1
    VBoxManage snapshot "$vm" take "start_snapshot_1"
done

vboxmanage natnetwork list
vboxmanage list hostonlyifs
vboxmanage list bridgedifs
vboxmanage list vms




#for vm in $VMS; do
#  sudo vboxmanage controlvm $vm poweroff
#  sudo VBoxManage modifyvm virtual-station1 --nested-hw-virt on
#  sudo VBoxManage snapshot $vm restore snapshot_with_git_1
#  sudo vboxmanage startvm $vm --type headless
#done


