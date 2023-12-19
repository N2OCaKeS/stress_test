#!/bin/bash
#
# Installation Testo environment and packages
# Default networks configurations int0, int1, int2, nat-libvirt
#

# uid check
# if test $(id -u) == 0; then
#   echo -e "\e[91mERROR (!) Required: id != 0\e[0m"
#   exit 1
# fi

# extra packages installation
sudo apt-get update
for pack in jq python3-requests ssh sshpass xmlstarlet virt-manager libvirt-clients libvirt-daemon libvirt-dev libvirt0; do
  if test ! "$(dpkg -l | awk '{print $2}' | grep ^$pack$)"; then
    sudo apt-get -y install $pack 
    [ $? != 0 ] && apt-get -f -y install
  fi
done

for group in kvm libvirt libvirt-qemu libvirt-admin; do
  if test ! "$(groups | grep ${group})"; then
    sudo usermod -aG ${group} $USER 
  fi
done

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
    rm -rf /opt/vagrant/embedded/gems/*
  fi

  # vagrant installation
  sudo dpkg -i vagrant_2.2.19_x86_64.deb
  if [ $? != 0 ]; then
    >&2 echo -e "\e[91mERROR (!) Vagrant package installation\e[0m"
    exit 1
  fi

  # remove garbage
  rm vagrant_2.2.19_x86_64.deb
  rm astra-vagrant.tar.gz
  rm -rf log/
fi


if [ ! -d ~/.vagrant.d/ ]; then
  cd /tmp/ && vagrant init
fi



define_network()
{
  net=$1
  sudo virsh net-define /tmp/$net
  sudo virsh net-start $net
  sudo virsh net-autostart $net
  rm /tmp/$net
}

if test "$(sudo virsh net-list --all | grep default)"; then
    sudo virsh net-undefine default
fi

# define virtual networks
if test ! "$(sudo virsh net-list --all | grep vagrant-libvirt)"; then
cat << EOF > /tmp/vagrant-libvirt
<network>
  <name>vagrant-libvirt</name>
  <uuid>e4c83d6f-a465-41f2-9562-a39336ac2b25</uuid>
  <forward mode='nat'>
    <nat>
      <port start='1024' end='65535'/>
    </nat>
  </forward>
  <bridge name='virbr4' stp='on' delay='0'/>
  <mac address='52:54:00:f0:b5:6f'/>
  <domain name='vagrant-libvirt'/>
  <ip address='192.168.100.1' netmask='255.255.255.0'>
    <dhcp>
      <range start='192.168.100.128' end='192.168.100.254'/>
    </dhcp>
  </ip>
</network>
EOF
define_network vagrant-libvirt
fi

if test ! "$(sudo virsh net-list --all | grep int0)"; then
cat << EOF > /tmp/int0
<network>
  <name>int0</name>
  <uuid>5d5747ef-4011-4435-9909-532c0e20d11a</uuid>
  <forward dev='eth0' mode='nat'>
    <nat>
      <port start='1024' end='65535'/>
    </nat>
    <interface dev='eth0'/>
  </forward>
  <bridge name='virbr0' stp='on' delay='0'/>
  <mac address='52:54:00:33:f3:b0'/>
  <domain name='int0'/>
  <ip address='10.0.0.1' netmask='255.255.255.0'>
    <dhcp>
      <host mac='08:00:27:E1:87:C4' ip='10.0.0.21'/>
      <host mac='08:00:27:64:AF:57' ip='10.0.0.22'/>
      <host mac='08:00:27:35:FB:4D' ip='10.0.0.25'/>
      <host mac='08:00:27:93:D3:2B' ip='10.0.0.24'/>
      <host mac='08:00:27:73:E5:1C' ip='10.0.0.23'/>
    </dhcp>
  </ip>
</network>
EOF
define_network int0
fi

if test ! "$(sudo virsh net-list --all | grep int2)"; then
cat << EOF > /tmp/int2
<network>
  <name>int2</name>
  <uuid>53f5b0a2-5a6d-4741-96e3-9929b6bdfc1f</uuid>
  <forward dev='eth0' mode='nat'>
    <nat>
      <port start='1024' end='65535'/>
    </nat>
    <interface dev='eth0'/>
  </forward>
  <bridge name='virbr1' stp='on' delay='0'/>
  <mac address='52:54:00:d5:57:39'/>
  <domain name='int2'/>
  <ip address='10.0.20.1' netmask='255.255.255.0'>
    <dhcp>
      <host mac='52:54:00:25:F4:8B' ip='10.0.20.20'/>
      <host mac='52:54:00:7D:A5:95' ip='10.0.20.21'/>
      <host mac='08:00:27:86:E7:E6' ip='10.0.20.22'/>
      <host mac='52:54:00:12:2A:57' ip='10.0.20.23'/>
      <host mac='52:54:00:67:A4:E3' ip='10.0.20.30'/>
      <host mac='52:54:00:50:A4:81' ip='10.0.20.101'/>
      <host mac='52:54:00:B9:E4:50' ip='10.0.20.102'/>
      <host mac='52:54:00:E3:7B:25' ip='10.0.20.103'/>
    </dhcp>
  </ip>
</network>
EOF
define_network int2
fi

if test ! "$(sudo virsh net-list --all | grep int1)"; then
cat << EOF > /tmp/int1
<network>
  <name>int1</name>
  <uuid>b6f7009b-886c-4c59-86d7-9a1daadd642a</uuid>
  <forward dev='eth0' mode='nat'>
    <nat>
      <port start='1024' end='65535'/>
    </nat>
    <interface dev='eth0'/>
  </forward>
  <bridge name='virbr2' stp='on' delay='0'/>
  <mac address='52:54:00:53:1e:9d'/>
  <domain name='int1'/>
  <ip address='10.0.10.1' netmask='255.255.255.0'>
    <dhcp>
      <host mac='08:00:27:56:02:DE' ip='10.0.10.6'/>
      <host mac='08:00:27:AC:61:56' ip='10.0.10.7'/>
    </dhcp>
  </ip>
</network>
EOF
define_network int1
fi

# check user group 'libvirt'
test "$(groups $USER | grep libvirt)" || sudo usermod -aG libvirt $USER

# iptables fix subnet ping
sudo touch /etc/libvirt/hooks/qemu
sudo chmod 777 /etc/libvirt/hooks/qemu
cat <<\EOF > /etc/libvirt/hooks/qemu
#!/bin/bash
if [ "$2" = "prepare" ]; then
  if test -z "$(iptables-save | grep 'i virbr0 -o virbr1')"; then
    iptables-save | sed -n '1,/REJECT/p' | head -n -1 > /tmp/iptables
    echo "
-A FORWARD -s 10.0.0.0/24 -i virbr0 -o virbr1 -j ACCEPT
-A FORWARD -s 10.0.0.0/24 -i virbr0 -o virbr2 -j ACCEPT
-A FORWARD -s 10.0.10.0/24 -i virbr1 -o virbr0 -j ACCEPT
-A FORWARD -s 10.0.10.0/24 -i virbr1 -o virbr2 -j ACCEPT
-A FORWARD -s 10.0.20.0/24 -i virbr2 -o virbr0 -j ACCEPT
-A FORWARD -s 10.0.20.0/24 -i virbr2 -o virbr1 -j ACCEPT
" >> /tmp/iptables
    iptables-save | sed -n '/REJECT/,$p' >> /tmp/iptables
    iptables-restore < /tmp/iptables
    rm /tmp/iptables
  fi
fi

if [ "$2" = "stopped" ]; then
  json_default_pool=$(jq '."default-pool"' /etc/libvirt/hooks/aqs-config.json | tr -d '"')
  chmod a+rw -R ${json_default_pool}
  chmod a+rw /etc/libvirt/qemu/*.xml
fi
EOF

yes | sudo cp -rf /home/$USER/git/astra-qa-stand/aqs-config.json /etc/libvirt/hooks/

#bypassing the error with initiation pool-dumpxml default
virt-manager &
pid=$!
pid_cor=$(($pid + 2))
sleep 3
sudo kill -KILL $pid_cor

actual_default_pool=$(sudo virsh pool-dumpxml default | grep -i path | sed 's/<[^>]*>//g')
json_qemu_path=$(jq '."qcow2-path"' /home/$USER/git/astra-qa-stand/aqs-config.json | tr -d '"')
json_default_pool=$(jq '."default-pool"' /home/$USER/git/astra-qa-stand/aqs-config.json | tr -d '"')

if [ $actual_default_pool != $json_default_pool ]; then
  sudo virsh pool-destroy default 
  sudo virsh pool-undefine default 
  sudo mkdir -p $json_default_pool
  sudo chmod a+rw -R $json_default_pool 
  sudo virsh pool-define-as --name default --type dir --target $json_default_pool
  sudo virsh pool-autostart default
  sudo virsh pool-start default 
fi

if [ ! -d "${json_qemu_path}" ]; then
  sudo mkdir -p "${json_qemu_path}"
  sudo chmod -R 777  "${json_qemu_path}"
else
  sudo chmod -R 777  "${json_qemu_path}"
fi

qemu_conf_user=$(sudo grep "user = ${USER@Q}" /etc/libvirt/qemu.conf)
qemu_needed_user="user = ${USER@Q}"

if  [[ ${qemu_conf_user} != ${qemu_needed_user} ]]; then
  echo -e ${qemu_needed_user} | sudo tee -a /etc/libvirt/qemu.conf
fi

sudo systemctl restart libvirtd



