pkg_manager=$1

sudo $pkg_manager update

# create venv in script_dir
sudo $pkg_manager install -y python3
sudo $pkg_manager install -y pip
sudo $pkg_manager install -y parted
sudo $pkg_manager install -y build-essential    
sudo $pkg_manager install -y zlib1g-dev
sudo $pkg_manager install -y libncurses5-dev
sudo $pkg_manager install -y libgdbm-dev
sudo $pkg_manager install -y libnss3-dev
sudo $pkg_manager install -y libssl-dev
sudo $pkg_manager install -y libreadline-dev
sudo $pkg_manager install -y libffi-dev
sudo $pkg_manager install -y libsqlite3-dev
sudo $pkg_manager install -y wget
sudo $pkg_manager install -y libbz2-dev
sudo $pkg_manager install -y strace
sudo $pkg_manager install -y libffi-dev  
sudo $pkg_manager install -y python3-requests
sudo $pkg_manager install -y exfat-utils
sudo $pkg_manager install -y exfatprogs
sudo $pkg_manager install -y xfsprogs


# Allta devpi package index
sudo python3 -m pip config --global set global.index-url http://allta.devos.astralinux.ru:3141/root/release
sudo python3 -m pip config --global set global.trusted-host allta.devos.astralinux.ru
python3 -m pip install --upgrade pip #--break-system-packages
python3 -m pip install -r req.txt #--break-system-packages


