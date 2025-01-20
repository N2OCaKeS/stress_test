for user in am289 data ab122 ott1g ott32; do
    yes 1 | sudo adduser ${user}
    sudo pdpl-user -l 0:2 ${user}
done

for user in data_db data_common; do
    yes 1 | sudo adduser ${user}
    sudo pdpl-user -l 0:0 ${user}
done


CURRENT_PATH=`pwd`
if test "$(grep -E '1.8.*' /etc/astra_version)"; then
  PG_VERSION=$(cat psb_conf.py | grep 'PG_VERSION_18 =' | awk '{print $3}')
else
  PG_VERSION=$(cat psb_conf.py | grep 'PG_VERSION =' | awk '{print $3}')
fi

apt-get install -y postgresql-${PG_VERSION}


sudo usermod -a -G shadow postgres
sudo setfacl -d -m u:postgres:r /etc/parsec/macdb
sudo setfacl -R -m u:postgres:r /etc/parsec/macdb
sudo setfacl -m u:postgres:rx /etc/parsec/macdb
sudo setfacl -d -m u:postgres:r /etc/parsec/capdb
sudo setfacl -R -m u:postgres:r /etc/parsec/capdb
sudo setfacl -m u:postgres:rx /etc/parsec/capdb 



sed -i 's/.*ac_ignore_socket_maclabel.*/ac_ignore_socket_maclabel = false/g' /etc/postgresql/$PG_VERSION/main/postgresql.conf
sed -i 's/.*ac_enable_grant_options.*/ac_enable_grant_options = true/g' /etc/postgresql/$PG_VERSION/main/postgresql.conf


sudo systemctl restart parsec
sudo systemctl restart postgresql 


sudo -u postgres -i << EOF
psql -c "CREATE user am289 with password 'useram289';"
psql -c "CREATE user data with password 'userdata';"
psql -c "CREATE user data_db with password 'userdata_db';"
psql -c "CREATE user data_com with password 'userdata_com';"
psql -c "CREATE USER ab122 with password 'userab122';"
psql -c "CREATE USER ott1g with password 'userott1g';"
psql -c "CREATE USER ott32 with password 'userott32';"
EOF


sudo -u postgres -i << EOF
psql -c "MAC LABEL ON CLUSTER IS '{2,0}';"
psql -c "MAC LABEL ON TABLESPACE pg_global IS '{2,0}';"
psql -c "MAC CCR ON CLUSTER IS OFF;"
psql -c "CREATE DATABASE test;"
psql -d test -c "MAC LABEL ON DATABASE test is '{2,0}';"
psql -d test -c "MAC CCR ON DATABASE test is OFF;"
psql -d test -c "MAC CCR ON SCHEMA public is Off;"
psql -d test -c "MAC LABEL ON SCHEMA public is '{2,0}';"
EOF


sudo tar -xzvf sql/test.tar.gz -C /var/lib/postgresql

sudo -u postgres -i << EOF
psql -d test < test.sql 
EOF


#sudo -u postgres -i << EOF
#psql -d test -c "select am289.form_am289n04()"
#EOF