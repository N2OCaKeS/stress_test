#!/bin/bash



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