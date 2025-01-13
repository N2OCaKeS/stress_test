

sudo adduser am289
sudo pdpl-user -l 0:2 am289

sudo adduser data
sudo pdpl-user -l 0:2 data

sudo adduser data_db
sudo pdpl-user -l 0:0 data_db

sudo adduser data_common
sudo pdpl-user -l 0:0 data_common

sudo adduser ab122
sudo pdpl-user -l 0:2 ab122

sudo adduser ott1g
sudo pdpl-user -l 0:2 ott1g

sudo adduser ott32
sudo pdpl-user -l 0:2 ott32



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



sed -i 's/.*ac_ignore_socket_maclabel*/ac_ignore_socket_maclabel = false/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf
sed -i 's/.*ac_enable_grant_options*/ac_enable_grant_options = true/g' /etc/postgresql/$PG_VERSION/$PG_SETEST_CLUSTER/postgresql.conf


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
psql -c "MAC CCR ON CLUSTER IS OFF; "
psql -c "CREATE DATABASE test; "
psql -c "\c test;"
psql -c "MAC LABEL ON DATABASE test is '{2,0}';"
psql -c "MAC CCR ON DATABASE test is OFF;"
psql -c "MAC CCR ON SCHEMA public is Off;"
psql -c "MAC LABEL ON SCHEMA public is '{2,0}';"
EOF







