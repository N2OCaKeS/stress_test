### Запуск
Подготовка
```bash
sudo astra-modeswitch set 2 && sudo astra-mac-control enable && sudo astra-mic-control enable
```

Выбор ядра для загрузки
```bash
sudo grep menuentry /boot/grub/grub.cfg | grep linux
```
Пример: `GRUB_DEFAULT=gnulinux-6.1.90-1-generic-advanced-c4a94e47-6e0b-402b-a2a0-8f2b4257d5b3`


Запуск теста
```bash
sudo perf record -g -a ./load2noarch /home/u/test/ 150 20000 5   
sudo perf script | perl libstackcollapse-perf.pl | perl libflamegraph.pl > 150_20000_5.svg   
```



#### Отключение модуля parsec (поможет исключить его влияние на системные процессы):
Подготовка режима
```bash
sudo astra-modeswitch set 0
sudo reboot
```
Создать файл `/etc/modprobe.d/parsec.conf` с содержимым `install parsec /bin/false`:
```bash
echo install parsec /bin/false | sudo tee /etc/modprobe.d/parsec.conf
```

Выполнить пересоздание `initd`:
```bash
sudo update-initramfs -u -k all
sudo reboot
```

Проверка корректности отключения модуля:
```bash
lsmod | grep parsec
```



#### Включение модуля parsec:
Удалить файл `/etc/modprobe.d/parsec.conf` с содержимым `install parsec /bin/false`:
```bash
sudo rm /etc/modprobe.d/parsec.conf
```

Выполнить пересоздание `initd`:
```bash
sudo update-initramfs -u -k all
sudo reboot
```

Проверка корректности включения модуля:
```bash
lsmod | grep parsec
```

Подготовка режима
```bash
sudo astra-modeswitch set 2 && sudo astra-mac-control enable && sudo astra-mic-control enable
sudo reboot
```

