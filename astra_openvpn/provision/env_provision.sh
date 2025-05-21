

set -vx

18repo() {
cat << EOF > /etc/apt/sources.list
deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.0/1.8.0.14/installation 1.8_x86-64 main contrib non-free
deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.0/1.8.0.14/extended-repository 1.8_x86-64 main contrib non-free
deb https://releases.devos.astralinux.ru/frozen/1.8/1.8.0/1.8.0.14/devel-repository 1.8_x86-64 main contrib non-free
EOF
}

17repo() {
echo "grub-pc grub-pc/install_devices multiselect /dev/sda" | sudo debconf-set-selections
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
sudo jq -r ".\"$1\"[]" releases.json > /etc/apt/sources.list
cat << EOF | sudo tee /etc/apt/preferences.d/devel
Package: *
Pin: release l=devel
Pin-Priority: 500

Package: *
Pin: release l=extended
Pin-Priority: 500
EOF

sudo apt update
#sudo astra-update -A -T -r
#sudo apt-get install -y sysstat
#sudo apt-get install -y netcat
#sudo apt-get install linux-[5-6].*-generic -y
#sudo apt-get install linux-[5-6].*-lowlatency -y
sudo apt-get install -y libffi-dev gcc make libpdp-dev
#sudo apt-get install -y python3-numpy
if [ "$HOSTNAME" = "testvm1.stress.rbt" ]; then
    echo "---$(HOSTNAME)---i"
    sudo DEBIAN_FRONTEND=noninteractive apt-get -y install astra-openvpn-server
    sudo astra-openvpn-server start
    sudo astra-openvpn-server status
else
    echo "---($HOSTNAME)---"
    sudo DEBIAN_FRONTEND=noninteractive apt-get -y install openvpn sshpass

fi

sudo DEBIAN_FRONTEND=noninteractive apt-get install -y pkg-config
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev libsqlite3-dev wget libbz2-dev
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y libffi-dev strace 
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y libcurl4-gnutls-dev
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y rustc cargo
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-requests
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y liblzma-dev
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y linux-tools-`uname -r`

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

kernel="$2"
kernel_conf=$(sudo cat /boot/grub/grub.cfg | grep menuentry_id | awk '{print $17}' | grep $kernel | tr -d "\'")
if ! grep -q '^GRUB_DEFAULT=' /etc/default/grub; then
    echo 'GRUB_DEFAULT=0' | sudo tee -a /etc/default/grub
fi
sudo sed -i "s/GRUB_DEFAULT=.*/GRUB_DEFAULT=$kernel_conf/" /etc/default/grub
sudo update-grub
cat /etc/default/grub | grep GRUB_DEFAULT


cat /etc/astra/build_version
cat /etc/astra/build_version > /home/av.txt
