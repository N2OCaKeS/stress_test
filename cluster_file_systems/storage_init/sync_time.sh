sudo apt install systemd-timesyncd -y
sudo timedatectl set-ntp true
sudo systemctl start systemd-timesyncd
systemctl --quiet is-active systemd-timesyncd