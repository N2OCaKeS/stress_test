astra-modeswitch set 2 && astra-mac-control enable && astra-mic-control enable

yes 1 | adduser user1
yes 1 | adduser user2

pdpl-user -l 0:0 user1
pdpl-user -l 0:1 user2

apt install apache2 libapache2-mod-authnz-pam -y
a2enmod authnz_pam

cat << EOF > /var/www/html/level0.html
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Level 0</title>
</head>
<body>
    <p>Уровень 0</p>
</body>
</html>
EOF

cat << EOF > /var/www/html/level1.html
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Level 1</title>
</head>
<body>
    <p>Уровень 1</p>
</body>
</html>
EOF

cat << EOF > /etc/apache2/sites-available/000-default.conf
<VirtualHost *:80>
        ServerAdmin webmaster@localhost
        DocumentRoot /var/www/html
        <Directory /var/www/html>
                Options Indexes FollowSymLinks MultiViews
                AllowOverride None
                AuthType Basic
                AuthName "PAM authentication"
                AuthBasicProvider PAM
                AuthPAMService apache2
                Require valid-user
        </Directory>
        ErrorLog ${APACHE_LOG_DIR}/error.log
        CustomLog ${APACHE_LOG_DIR}/access.log combined
</VirtualHost>
EOF


pdpl-file 3:63:-1:ccnr /var/www/html/
pdpl-file 1:0:0:0 /var/www/html/level1.html 
pdpl-file 0:0:0:0 /var/www/html/level0.html 

pdp-ls -Md /var/www/html/
pdp-ls -M /var/www/html/

systemctl restart apache2
systemctl status apache2
