pkg_manager=$1

sudo $pkg_manager update

# create venv in script_dir
sudo $pkg_manager install -y python3
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


python3 -m pip install --upgrade pip
python3 -m pip install -r req.txt


