echo "PermitRootLogin yes" >> /etc/ssh/sshd_config
sudo systemctl restart ssh
echo -e "1\n1" | sudo passwd

sudo apt-get update -y  && sudo apt-get install -y nfs-common 
sleep 10

sudo mkdir /git

sudo apt install -y python3-pip

sudo mount $1:/home/u/git/stress_test/cluster_file_system_benchmark /git

sudo pip3 install fabric --break-system-packages

sudo pip3 install -r /git/req.txt --break-system-packages