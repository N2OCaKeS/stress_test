#!/bin/bash


venv(){
    # usermod -aG docker u
    # apt-get install python3 docker.io docker-compose python3-venv python3.11-venv apache2-utils pip postgresql postgresql-contrib libpq-dev -y
    # sudo service postgresql start
    sudo apt-get install libpq-dev gcc python3-dev python3-pip python3.11-venv libapache2-mod-wsgi-py3 -y 
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip
    pip install --upgrade setuptools wheel
    pip install -r requirements.txt
}


load_test(){
    python3 main.py --test load -ram 100M -t 1 -c 5
}


drop_db(){
    sudo -u postgres psql
    \c postgres
    DROP DATABASE mydb;
    \q
    # обновление migrations:
    # sudo rm -rf migrations/
    # psql -U u -h 127.0.0.1 -d mydb
    # delete from alembic_version;
}

app_settings(){
    md5_pass=$(echo -n "1postgres" | md5sum | awk '{print "md5"$1}')

    sudo apt-get update
    sudo apt-get install postgresql postgresql-contrib redis-server pgbouncer apache2 apache2-utils -y  
#    export PGPASSWORD="1postgres"

#    sudo -u postgres psql -c "ALTER USER postgres WITH ENCRYPTED PASSWORD '${md5_pass}';"
#    sudo -u postgres psql -c "CREATE DATABASE mydb;"
#    sudo -u postgres psql -c "CREATE ROLE u WITH LOGIN ENCRYPTED PASSWORD '${md5_pass}';"
#    sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE mydb TO u;"
#    sudo -u postgres psql -c "ALTER USER u CREATEDB;"
#    sudo -u postgres psql -d mydb -c "GRANT ALL ON schema public TO u;"
    sudo -u postgres psql <<EOF
    ALTER USER postgres WITH ENCRYPTED PASSWORD '${md5_pass}';
    CREATE DATABASE mydb;
    CREATE ROLE u WITH LOGIN ENCRYPTED PASSWORD '${md5_pass}';
    GRANT ALL PRIVILEGES ON DATABASE mydb TO u;
    ALTER USER u CREATEDB;
    \c mydb
    GRANT ALL ON schema public TO u;
EOF

    sudo sed -i 's/^\(local\s\+all\s\+postgres\s\+\).*/\1md5/' /etc/postgresql/*/main/pg_hba.conf
    sudo sed -i 's/^\(host\s\+all\s\+all\s\+127.0.0.1\/32\s\+\).*/\1md5/' /etc/postgresql/*/main/pg_hba.conf
    sudo sed -i 's/^\(host\s\+all\s\+all\s\+::1\/128\s\+\).*/\1md5/' /etc/postgresql/*/main/pg_hba.conf
    sudo sed -i 's/^REDIS_HOST = "redis"/REDIS_HOST = "127.0.0.1"/' web_app/config.py
    sudo cp pg/postgresql.conf /etc/postgresql/*/main/postgresql.conf 
    echo "data_directory = '/var/lib/postgresql/15/main'" | sudo tee -a /etc/postgresql/15/main/postgresql.conf

    sudo chown -R postgres:postgres /var/lib/postgresql/
    sudo chmod -R 700 /var/lib/postgresql/

    sudo systemctl restart postgresql

    sudo cp pg/pgbouncer/pgbouncer_host/* /etc/pgbouncer/
    sudo systemctl restart pgbouncer
    sudo rm -f /etc/apache2/sites-available/*
    sudo cp apache-config/config_host/apache2.conf /etc/apache2/apache2.conf
    sudo cp apache-config/config_host/web-app.conf /etc/apache2/sites-available/
    sudo chown www-data:www-data /etc/apache2/sites-available/web-app.conf
    sudo chmod 644 /etc/apache2/sites-available/web-app.conf
    sudo chown -R www-data:www-data /home/u/git/stress_test/docker/site/
    sudo chmod -R 755 /home/u/git/stress_test/docker/site/
    sudo a2enmod wsgi
    sudo a2ensite web-app
    sudo apachectl configtest
    sudo apachectl -M | grep astra
    sudo systemctl restart apache2
}
migrate(){
    flask db init
    flask db migrate
    flask db upgrade
}


case $1 in 
    load)
        load_test
        ;;
    prepare)
        venv
	    app_settings
        ;;
esac
