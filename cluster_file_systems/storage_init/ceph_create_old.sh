# От пользователя ceph-amd

# Указать основную
# ssh-copy-id ceph-adm@astra-ceph-admin
sshpass -p "1" ssh-copy-id -o StrictHostKeyChecking=no ceph-adm@astra-ceph-admin;
for N in $(seq 1 3); do sshpass -p "1" ssh-copy-id -o StrictHostKeyChecking=no ceph-adm@astra-ceph$N; done
ceph-deploy --username ceph-adm install --mon --osd astra-ceph1 astra-ceph2 astra-ceph3
for N in $(seq 1 3); do ssh ceph-adm@astra-ceph$N sudo reboot; done

sleep 300

ceph-deploy --username ceph-adm install --mgr astra-ceph1
ceph-deploy --username ceph-adm new astra-ceph1 astra-ceph2 astra-ceph3

ceph-deploy --username ceph-adm mon create-initial
ceph-deploy --username ceph-adm mgr create astra-ceph1

for i in {1..3}; do
    if ceph-deploy --username ceph-adm osd create --data /dev/sdb "astra-ceph${i}"; then
        echo "OSD успешно добавлен на astra-ceph${i}"
    else
        echo "Ошибка при добавлении OSD на astra-ceph${i}"
    fi
    sleep 5
done

# Указать основную
ceph-deploy --username ceph-adm install --cli astra-ceph-admin
ceph-deploy admin astra-ceph-admin
sudo chmod 644 /etc/ceph/ceph.client.admin.keyrings

# Далее разделение на Ceph FS и RBD

ceph-deploy --username ceph-adm install --mds astra-ceph1
ceph-deploy --username ceph-adm mds create astra-ceph1

sudo ceph osd pool create cephfs_data 64
sudo ceph osd pool create cephfs_metadata 64
sudo ceph osd pool application enable cephfs_metadata cephfs
sudo ceph fs new cephfs cephfs_metadata cephfs_data

sudo mkdir /mnt/cephfs/data_test
sudo  ceph auth get-or-create client.datatest mon 'allow r' mds 'allow r,allow rw path=/data_test' osd 'allow rw pool=cephfs_data'
secret_key=$(sudo ceph auth get-key client.datatest)

# mount на другом хосте или вм


