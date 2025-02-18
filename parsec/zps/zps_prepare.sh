
set -vx

astra-modeswitch set 2 && astra-mac-control enable && astra-mic-control enable && astra-digsig-control enable && reboot


#Нижеследующее запускать отдельным скриптом
astra-digsig-control status
#update-initramfs -uk all


/usr/bin/ssh --help

cp /usr/bin/ssh /usr/bin/ssh.bak
dd if=<(echo -n $(printf '\\x%02x' $((0x$(tail -c 1 /usr/bin/ssh | hexdump -v -e '/1 "%02x"') |\
 1)))) of=/usr/bin/ssh bs=1 seek=$(($(stat -c %s /usr/bin/ssh) - 1)) count=1 conv=notrunc

/usr/bin/ssh --help
dmesg -HTx | grep DIGSIG | grep ssh
dmesg -HTx | grep DIGSIG | grep ssh | wc -l



