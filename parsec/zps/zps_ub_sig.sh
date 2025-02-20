UB_PATH=/home/u/git/stress_test/linux_system/byte-unixbench-master/UnixBench

apt install -y bsign-integrator

#вычистить весь мусор, если есть.
rm /root/.gnupg/openpgp-revocs.d/*.rev /root/.gnupg/pubring.kbx /root/.gnupg/trustdb.gpg

gpg --full-gen-key --quiet --batch <( echo "Key-Type: GOST_R34.10-2012"; \
						echo "Key-Length: 3072"; \
						echo "Name-Real: Load Tester"; \
						echo "Name-Email: load@tester.rbt"; \
						echo "Expire-Date: 0"; \
						echo "Passphrase: 12345678"; )
gpg --export "load@tester.rbt" > /root/load.gpg
cp /root/load.gpg /etc/digsig/certs/

printf "Установлен bsign-integrator \nСоздан тестовый ключ\nСертификат ключа помещён в ЗПС\n" && sleep 5
update-initramfs -uk all

for file in $(find $UB_PATH -type f -executable); do
	bsign-integrator -s -p=12345678 -k=$(grep -ro load@tester.rbt /root/.gnupg/openpgp-revocs.d/ | awk -F'[/.]' '{print $7}') $file
done

printf "Подписанo действительным ключём \nВключёнo в список учёта \n" && sleep 5
update-initramfs -uk all
reboot

