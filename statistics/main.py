import argparse

from statistics_st import BaseStatistics, FreeIpaStatistics, VirtStatistics, PostgreSQLStatistics
from parsers import ApacheParser, ParsecParser

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('-u', '--username',
                        action='store',
                        required=True,
                        help='confluence user',
                        dest='USER')
    parser.add_argument('-t', '--token',
                        action='store',
                        required=True,
                        default=None,
                        help='confluence access token',
                        dest='TOKEN')
    
    args = parser.parse_args()

    unix_stat = BaseStatistics(stat_title="UnixBench", 
                               username=args.USER, 
                               tokenconf=args.TOKEN,
                               set_of_test_types={"unix", "unix_parsec"}
                               )
    unix_stat.create()
    system_services_stat = BaseStatistics(stat_title="Системные службы",
                                          username=args.USER, 
                                          tokenconf=args.TOKEN,
                                          set_of_test_types={"auditd-p", "auditd-f", "auditd-u", "syslog-ng"}
                                          )
    system_services_stat.create()
    file_systems_stat = BaseStatistics(stat_title="Файловые системы",
                                       username=args.USER,
                                       tokenconf=args.TOKEN,
                                       set_of_test_types={"EXFAT", "EXT2", "EXT4", "EXT4_parsec", "FAT", "NTFS", "XFS", "XFS_parsec", "OCFS2"},
                                       comparison_list=[["EXT4", "XFS"], ["EXT4", "EXT4_parsec"]])
    file_systems_stat.create()
    postresql_stat = PostgreSQLStatistics(stat_title="PostgreSQL",
                                          username=args.USER,
                                          tokenconf=args.TOKEN,
                                          set_of_test_types={"postgresql", "postgresql-sm", "postgresql-aud-off", "psql_parsec", "psql_vanilla", "tantor_vanilla", "psql_balance"},
                                          comparison_list=[
                                              ["postgresql", "postgresql-sm"], 
                                              ["postgresql", "postgresql-aud-off"], 
                                              ["postgresql", "psql_parsec"], 
                                              ["postgresql", "psql_vanilla"]],
                                          comparison_kernel_list=["postgresql"])
    postresql_stat.create()
    apache_stat = BaseStatistics(stat_title="Apache",
                                 username=args.USER,
                                 tokenconf=args.TOKEN,
                                 set_of_test_types={"apache-rp"},
                                 score_parser=ApacheParser)
    apache_stat.create()
    parsec_stat = BaseStatistics(stat_title="Parsec",
                                 username=args.USER,
                                 tokenconf=args.TOKEN,
                                 set_of_test_types={"parsec_impact-fs", "parsec_impact-fs-aud-off"},
                                 comparison_list=[["parsec_impact-fs", "parsec_impact-fs-aud-off"]],
                                 score_parser=ParsecParser)
    parsec_stat.create()

    freeipa_stat = FreeIpaStatistics(stat_title="FreeIPA",
                                     username=args.USER,
                                     tokenconf=args.TOKEN,
                                     set_of_test_types={'FreeIPA_auth'},
                                     )
    freeipa_stat.create()
    virt_stat = VirtStatistics(stat_title="Qemu/KVM/Libvirt",
                               username=args.USER,
                               tokenconf=args.TOKEN,
                               set_of_test_types={"FIO", "vPingPong", "vUnixBench", "steal_time"})
    virt_stat.create()
