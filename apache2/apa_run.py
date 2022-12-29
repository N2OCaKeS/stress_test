#!/usr/bin/env python3
import argparse
from time import sleep
from invoke import UnexpectedExit
from libs.libapa import Color, check_output_command, command, ssh_shell_command, \
        create_user, set_level_on_user, astra_mode_switch 
from apa_conf import TESTED_SERVER_IP, ApacheNode, ClientNode, \
        MAX_CONCURRENCY, MAX_REQUESTS, CONCURRENCY_STEP, \
        TESTED_QA_USER, TESTED_QA_USER_MAC, TESTED_QA_USER_MAC_CAT


DESCRIPTION = "Apache2 load tests"
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('-r', '--run',
                    action='store',
                    choices=[
                        'full-run-smol', 
                        'setup-server', 
                        'setup-client',
                        'run-tests-smol'
                         ],
                    required=True,
                    help='Setup servers for load testing',
                    dest='RUN')

args = parser.parse_args()
RUN = args.RUN

yanus = "10.177.5.111"


def set_up_apache():
    print("{}Setting up apache2 server on tested_server{}".format(Color.Yellow, Color.END))
    ssh_shell_command("wget -r -q -P ./ ftp://{}/git/skts-test/testlink/ 1>/dev/null".format(yanus),
                      ApacheNode.admin)
    ssh_shell_command("echo | sudo -S apt-get install apache2 libapache2-mod-authnz-pam", ApacheNode.admin)
    ssh_shell_command("cd 10.177.5.111/git/skts-test/testlink && sudo bash apache_server_prepare.sh pam",
                      ApacheNode.admin)
    sleep(2)

def apache_benchmark_configure():
    ssh_shell_command("echo | sudo -S apt-get install apache2-utils -y", ClientNode.admin)
    try:
        ssh_shell_command("echo | sudo -S usercat -a 8 'Cat_8'", ClientNode.admin)
    except UnexpectedExit:
        print("Catch error of existent Category, ignore it")
        pass


def apache_pam(configure_or_reset):  # configure or reset
    if configure_or_reset == "reset":
        ApacheNode.admin.put("000-default-no-pam.conf", "/tmp/")
        ApacheNode.admin.put("apache2", "/tmp/")
        ssh_shell_command("echo | sudo -S cp /tmp/apache2 /etc/pam.d/apache2", ApacheNode.admin)
        ssh_shell_command("echo | sudo -S cp /tmp/000-default-no-pam.conf /etc/apache2/sites-available/000-default.conf", ApacheNode.admin)
        ssh_shell_command("cd {}/git/skts-test/testlink && sudo bash apache_server_prepare.sh pam".format(yanus), ApacheNode.admin)
    elif configure_or_reset == 'configure':
        ApacheNode.admin.put("apache2", "/tmp/")
        ApacheNode.admin.put("000-default-pam.conf", "/tmp/")
        ssh_shell_command("echo | sudo -S cp /tmp/apache2 /etc/pam.d/apache2", ApacheNode.admin)
        ssh_shell_command("echo 'account required pam_tally.so' | sudo tee -a /etc/pam.d/apache2", ApacheNode.admin)
        ssh_shell_command("echo | sudo -S cp /tmp/000-default-pam.conf /etc/apache2/sites-available/000-default.conf", ApacheNode.admin)
        ssh_shell_command("echo | sudo usermod -aG shadow www-data", ApacheNode.admin)
        ssh_shell_command("cd {}/git/skts-test/testlink && sudo bash apache_server_prepare.sh pam".format(yanus), ApacheNode.admin)


def create_server_test_users():
    create_user(TESTED_QA_USER, ApacheNode.admin)
    create_user(TESTED_QA_USER_MAC, ApacheNode.admin)
    create_user(TESTED_QA_USER_MAC_CAT, ApacheNode.admin)
    set_level_on_user(TESTED_QA_USER_MAC, ApacheNode.admin, "0", "2:2", "0x0:0x0")
    set_level_on_user(TESTED_QA_USER_MAC_CAT, ApacheNode.admin, "0", "2:2", "0xA:0xA")


def create_client_test_users():
    create_user(TESTED_QA_USER, ClientNode.admin)
    create_user(TESTED_QA_USER_MAC, ClientNode.admin)
    create_user(TESTED_QA_USER_MAC_CAT, ClientNode.admin)
    set_level_on_user(TESTED_QA_USER_MAC, ClientNode.admin, "0", "2:2", "0x0:0x0")
    set_level_on_user(TESTED_QA_USER_MAC_CAT, ClientNode.admin, "0", "2:2", "0xA:0xA")


def ab_tests_pam(user, url, node):
    sleep(5)
    print("{}Running test for user {}{}".format(Color.Blue, user, Color.END))
    astra_mode_switch("enable", ApacheNode.admin)
    for concurrent in range(CONCURRENCY_STEP, MAX_CONCURRENCY, CONCURRENCY_STEP):
        print("{}Runing test with {} concurrent users and {} requests {}".format(Color.Yellow, concurrent, MAX_REQUESTS, Color.END))
        ssh_shell_command("bash /ab/ab-graph.sh -u {url} -n {request} -c {concurrent} -E '-A {user}:1'"
                          .format(
                                  request=MAX_REQUESTS, 
                                  concurrent=concurrent,
                                  user=user, 
                                  url=url), node)
        ssh_shell_command("tar -czf {user}-{concurrent}-pam-mode.tar.gz -C report ."
                          .format(
                                  user=user,
                                  concurrent=concurrent),node)
        node.get("{user}-{concurrent}-pam-mode.tar.gz".format(user=user, concurrent=concurrent), "report/")

def ab_tests_no_pam(user, url, node):
    sleep(5)
    print("{}Running test for user {}{}".format(Color.Blue, user, Color.END))
    apache_pam("reset")
    astra_mode_switch("disable", ApacheNode.admin)
    command("mkdir report")
    for concurrent in range(CONCURRENCY_STEP, MAX_CONCURRENCY, CONCURRENCY_STEP):
        print("{}Runing test with {} concurrent users and {} requests {}".format(Color.Yellow, concurrent, MAX_REQUESTS, Color.END))
        ssh_shell_command("bash /ab/ab-graph.sh -u {url} -n {request} -c {concurrent}"
                          .format(
                                  request=MAX_REQUESTS, 
                                  concurrent=concurrent,
                                  user=user, 
                                  url=url), node)
        ssh_shell_command("tar -czf {user}-{concurrent}-no-pam-mode.tar.gz -C report ."
                          .format(
                                  user=user,
                                  concurrent=concurrent),node)
        node.get("{user}-{concurrent}-no-pam-mode.tar.gz".format(user=user, concurrent=concurrent), "report/")


def apache_prepare():
    set_up_apache()
    apache_pam("configure")
    create_server_test_users()


def client_prepare():
    apache_benchmark_configure()
    create_client_test_users()
    ssh_shell_command("echo | sudo -S mkdir /ab/", ClientNode.admin)
    ssh_shell_command("echo | sudo -S chmod 777 /ab/", ClientNode.admin)
    ClientNode.admin.put("ab-graph.sh", "/ab/")


def smol_pam_tests():
    apache_pam("configure")
    ab_tests_pam(TESTED_QA_USER_MAC, "http://{}/lev2.html".format(TESTED_SERVER_IP), ClientNode.qa1_login)
    ab_tests_pam(TESTED_QA_USER_MAC_CAT, "http://{}/lev2catA.html".format(TESTED_SERVER_IP), ClientNode.qa2_login)
    ab_tests_no_pam(TESTED_QA_USER, "http://{}/lev0.html".format(TESTED_SERVER_IP), ClientNode.qa0_login)


def create_plots():
    reports = (check_output_command("ls report | grep tar.gz")).split("\n")
    for report in reports:
        command("mkdir -p report/{}".format(report.split(".")[0]))
        command("tar -xf report/{} -C report/{}".format(report, report.split(".")[0]))
        command("./ab-plot.sh {}".format(report.split(".")[0]))


if __name__ == '__main__':
    if RUN == "full-run-smol":
        client_prepare()
        apache_prepare()
        smol_pam_tests()
        create_plots()
    elif RUN == "setup-server":
        apache_prepare()
    elif RUN == "setup-client":
        client_prepare()
    elif RUN == "run-tests-smol":
        smol_pam_tests()
        create_plots()

