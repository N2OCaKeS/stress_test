cd ./site
flask db migrate
flask db upgrade

apache2ctl -D FOREGROUND