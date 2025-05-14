#!/bin/bash


CPATH="/home/u/git/stress_test/astra_openvpn"
SYS_VERSION=$(cat /etc/astra/build_version | tr -d '[:space:]')
SYS_KERNEL=$(uname -r | tr -d '[:space:]')

export DEBIAN_FRONTEND=noninteractive
export DEBCONF_NONINTERACTIVE_SEEN=true

# set repo
dpkg -s jq &> /dev/null || sudo apt-get install jq -y
wget http://allta.devos.astralinux.ru/rest/api/get-repo-path -O releases.json
sudo jq -r ".\"$2\"[]" releases.json > /etc/apt/sources.list
# доб EXT если версия 1.7
if [[ "$SYS_VERSION" == 1.7* ]]; then
    echo "Добавляем строки с extended-repository..."
    grep -E '/[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/base-repository' /etc/apt/sources.list | \
    sed 's|/[0-9]\+\.[0-9]\+\.[0-9]\+\.[0-9]\+/base-repository|/EXT_latest/extended-repository|' >> /etc/apt/sources.list
fi
echo 1
cat << EOF | sudo tee /etc/apt/preferences.d/devel
Package: *
Pin: release l=devel
Pin-Priority: 500

Package: *
Pin: release l=extended
Pin-Priority: 500
EOF

# create venv
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev libsqlite3-dev wget libbz2-dev
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y libffi-dev strace 
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y libcurl4-gnutls-dev
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y rustc cargo
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-requests
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y liblzma-dev
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y linux-tools-`uname -r`
echo 2
# test packages




#sudo apt install astra-openvpn-server -y

#python
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
cd ${CPATH}
pip install -i http://10.177.103.10:3141/root/release --trust 10.177.103.10 allta
python3.12 -m pip install --upgrade pip
python3.12 -m pip install -r ${CPATH}/req.txt
#if [[ $? != 0 ]]; then
#    python3.12 -m pip install -r requirements.txt

#lvirt
sudo DEBIAN_FRONTEND=noninteractive apt-get install virt-manager libvirt-clients libvirt-daemon libvirt-dev libvirt0 -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install qemu ebtables libguestfs-tools ruby-fog-libvirt
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

wget http://allta.devos.astralinux.ru/rest/api/get-box-config -O box-config.json 


# create lv-nets
#virsh net-define ${CPATH}/lv-nets/vpn-net.xml
#virsh net-start vpn-net
#virsh net-autostart vpn-net

#virsh net-define ${CPATH}/lv-nets/pooler-net.xml
#virsh net-start pooler-net
#virsh net-autostart pooler-net

# маршрутизация
#echo 1 | sudo tee /proc/sys/net/ipv4/ip_forward

#sudo iptables -A FORWARD -i virbr100 -o virbr0 -j ACCEPT
#sudo iptables -A FORWARD -i virbr0 -o virbr100 -j ACCEPT
#sudo iptables -t nat -A POSTROUTING -s 192.168.100.0/24 -o virbr0 -j MASQUERADE

#sudo iptables -A FORWARD -i virbr100 -o virbr200 -j ACCEPT
#sudo iptables -A FORWARD -i virbr200 -o virbr100 -j ACCEPT
