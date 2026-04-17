# Для DEVQA-6388

KERNEL="6.1.152-1-generic"

echo deb https://releases.devos.astralinux.ru/frozen/1.7/1.7.9/1.7.9.41/base-repository 1.7_x86-64 main contrib non-free | sudo tee -a /etc/apt/sources.list
sudo apt update

sudo dpkg -s linux-image-${KERNEL} &> /dev/null || sudo apt-get install linux-image-${KERNEL} -y
sudo dpkg -s linux-headers-${KERNEL} &> /dev/null || sudo apt-get install linux-headers-${KERNEL} -y
sudo dpkg -s linux-astra-modules-${KERNEL} &> /dev/null || sudo apt-get install linux-astra-modules-${KERNEL} -y

kernel_conf=$(sudo cat /boot/grub/grub.cfg | grep menuentry_id | awk '{print $17}' | grep $KERNEL | tr -d "'")
sudo sed -i "s/.*GRUB_DEFAULT=.*/GRUB_DEFAULT=${kernel_conf}/" /etc/default/grub
   
sudo sed -i 's/\(GRUB_CMDLINE_LINUX_DEFAULT=.*\)"/\1 init_on_free=0"/' /etc/default/grub
sudo update-grub

echo -e "\n\nПерезагрузка через 5 секунд...\n"
sleep 5
sudo reboot



# Опционально
# После перезагрузки!
# sudo apt purge linux-image-6.1.161-1-generic linux-headers-6.1.161-1-generic linux-astra-modules-6.1.161-1-generic