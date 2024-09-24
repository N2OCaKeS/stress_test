#ansible
sudo apt-get install ansible -y
sudo apt-get install sshpass -y

#lvirt
apt-get install virt-manager libvirt-clients libvirt-daemon libvirt-dev libvirt0 -y

#vagrant
wget -r -nH --cut-dirs=2 --no-parent ftp://qa111.devos.astralinux.ru/packages/vagrant
sudo dpkg -i vagrant_2.2.19_x86_64.deb
sudo adduser $USER libvirt

#add user in groups
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

#check supports hardware virtualization
if [[ $(egrep -c '(vmx|svm)' /proc/cpuinfo) -gt 0 ]]; then
    echo "supports hardware virtualization is ok"
else 
    echo "system does not supports hardware virtualization" 
fi



if test "$(sudo virsh net-list --all | grep default)"; then
sudo virsh net-undefine default
fi

# upd network
IFACE=`ip -o link show | awk -F': ' '{print $2}' | head -n 2 | tail -n 1`
cat << EOF > /etc/network/interfaces

source /etc/network/interfaces.d/*

# The loopback network interface
auto lo
iface lo inet loopback

auto br0
iface br0 inet static
    address 10.177.103.203
    netmask 255.255.255.0
    gateway 10.177.103.254
    bridge_ports $IFACE
    bridge_stp off
    bridge_fd 0
    bridge_maxwait 0
    dns-nameserver 10.177.128.198

auto $IFACE
iface $IFACE inet manual

dns-nameservers 10.177.128.198
EOF
systemctl restart networking
fi


# check user group 'libvirt'
test "$(groups $USER | grep libvirt)" || sudo usermod -aG libvirt $USER



