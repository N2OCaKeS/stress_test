#!/bin/bash
# Prepare server for Apache2 tests.

. libald.sh
. liblocal.sh
# Astra version check
version=$(grep -o '[0-9]\.[0-9]' /etc/astra_version)

# Mac context values.
level=2
category0=2
category1=8
path_conf_level=$(pdp-ls -Md /var)

# Calculate category set (like 0x2 + 0x8 = 0xA
category_set() {
    local hexcat0=0x${category0}
    local hexcat1=0x${category1}
    local catsum=$((${hexcat0} + ${hexcat1}))
    catset=$(echo "obase=16; $catsum" | bc)
}

create_local_categories() {
    for c in ${category0} ${category1}; do
        create_local_category $c 
    done
}

create_local_user_for_test() {
    local username="helic"
    create_local_user $username
    pdpl-user ${username} -l 0:${level} -c 0:${catset} > /dev/null
}

create_local_user_for_bm() {
    local username="chingachgook"
    create_local_user $username
    pdpl-user ${username} -l 0:${level} -c 0:${catset} > /dev/null
}

create_ald_categories() {
    for c in ${category0} ${category1}; do
        create_ald_category $c 
    done
}

create_ald_user_for_test() {
    local username="copter"
    create_ald_user $username
    allow_hosts $username susrv.rtfm.rbt suac.rtfm.rbt
    create_ald_passwd
    ald-admin user-mac $username --max-lev-int=$level \
        --max-cat-hex=0x$catset $opts
    clear_ald_passwd
}

create_ipa_user_for_test() {
    ipa pwpolicy-mod global_policy --minlength=1 --minclasses=1
    echo -e "Усама\nБудьоннеладен\n1\n1" | ipa user-add foripa --password
    ipa user-mod foripa --macmin=0 --macmax=2
    echo -e "foripa" | ipa macmaxcat-add-macmaxcat Категория_1
    echo -e "foripa" | ipa macmaxcat-add-macmaxcat Категория_3
}


firefox_negotiate_check() {
    local syssettings="/etc/firefox/syspref.js"
    grep "trusted-uris" $syssettings &> /dev/null
    return $?
}

firefox_negotiate() {
    local syssettings="/etc/firefox/syspref.js"
    local site="http://"
    local tu="network.negotiate-auth.trusted-uris"
    local deu="network.negotiate-auth.delegation-uris"

    printf "pref(\"%s\", \"%s\");\n" "$tu" "$site" >> $syssettings
    printf "pref(\"%s\", \"%s\");\n" "$deu" "$site" >> $syssettings
}

create_html() {
    if [[ $version > 1.5 ]]; then
        sed -i 's/DocumentRoot\ \/var\/www\/html/ DocumentRoot\ \/var\/www/g' /etc/apache2/sites-available/000-default.conf
	else
        sed -i 's/DocumentRoot\ \/var\/www\/html/ DocumentRoot\ \/var\/www/g' /etc/apache2/sites-available/default
    fi
    service apache2 restart
    local mac_cont=""
    local fname=""
    local curcat=""

    if [[ -n $2 ]]; then
        mac_cont="Level: ${1}, Category: 0x${2}"
        fname="lev${1}cat${2}.html"
        curcat="0x${2}"
    else
        mac_cont="Level: ${1}, Category: empty"
        fname="lev${1}.html"
        curcat="0"
    fi
    printf "<html><body><h2>${mac_cont}</h2></body><html>"\
        > /var/www/${fname}
    pdpl-file ${1}:0:${curcat}:0 /var/www/${fname}
}

make_site() {
    find /var/www/ -type f -name "index.*" -exec mv '{}' '{}.bp' \;
    if [ -e apache_test.php ]; then
        cp apache_test.php /var/www/index.php
        chmod 644 /var/www/index.php
        if [[ $path_conf_level =~ "Низкий" ]]; then
            pdpl-file 3:0:-1:ccnr /var/www
        else 
            pdpl-file 3:63:-1:ccnr /var/www
        fi
        create_html 0
        create_html $level
        create_html $level $catset
    else
        printf "apache_test.php not found!\n" >& 2
    fi
}

pam() {
    create_local_level $level
    create_local_categories
    create_local_user_for_test
    make_site
}

bm() {
    create_local_level $level
    create_local_categories
    create_local_user_for_bm
    make_site
}


kerberos() {
    create_ald_level $level
    create_ald_categories
    create_ald_user_for_test
    if ! firefox_negotiate_check; then
        firefox_negotiate
    fi
    make_site
}

ipa16() {
    create_ipa_user_for_test
    if ! firefox_negotiate_check; then
        firefox_negotiate
    fi
    make_site
}

ipa18() {
    create_ipa_user_for_test
    if ! firefox_negotiate_check; then
        firefox_negotiate
    fi
    make_site
}

usage() {
printf "Usage: ./apache_server_prepare.sh (pam|kerb)\n"
}

if [[ $UID != 0 ]]; then
    printf "root required\n" >& 2
    exit 1
fi

category_set