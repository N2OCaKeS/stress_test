KERNEL="6.1.161-1-generic"


sudo dpkg -s linux-image-${KERNEL} &> /dev/null || sudo apt-get install linux-image-${KERNEL} -y
sudo dpkg -s linux-headers-${KERNEL} &> /dev/null || sudo apt-get install linux-headers-${KERNEL} -y
sudo dpkg -s linux-astra-modules-${KERNEL} &> /dev/null || sudo apt-get install linux-astra-modules-${KERNEL} -y

kernel_conf=$(sudo cat /boot/grub/grub.cfg | grep menuentry_id | awk '{print $17}' | grep $KERNEL | tr -d "'")
sudo sed -i "s/.*GRUB_DEFAULT=.*/GRUB_DEFAULT=${kernel_conf}/" /etc/default/grub   
sudo update-grub

cat /etc/default/grub
sleep 10

echo -e "\n\nПерезагрузка через 5 секунд...\n"
sleep 5
sudo reboot