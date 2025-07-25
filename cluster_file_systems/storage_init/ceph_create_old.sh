# От пользователя ceph-amd
ssh-keygen
for N in $(seq 1 3); do ssh-copy-id ceph-adm@testvm$N; done

# Указать основную
ssh-copy-id ceph-adm@astra-ceph-admin

ceph-deploy --username ceph-adm install --mon --osd testvm1 testvm2 testvm3
for N in $(seq 1 3); do ssh ceph-adm@testvm$N sudo reboot; done

ceph-deploy --username ceph-adm install --mgr testvm1
ceph-deploy --username ceph-adm new testvm1 testvm2 testvm3

ceph-deploy --username ceph-adm mon create-initial
ceph-deploy --username ceph-adm mgr create testvm1

ceph-deploy --username ceph-adm osd create --data /dev/sdb testvm1
ceph-deploy --username ceph-adm osd create --data /dev/sdb testvm2
ceph-deploy --username ceph-adm osd create --data /dev/sdb testvm3

# Указать основную
ceph-deploy --username ceph-adm install --cli astra-ceph-admin
ceph-deploy admin astra-ceph-admin

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


