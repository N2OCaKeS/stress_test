import os
from time import sleep

from fastapi import FastAPI, Depends, HTTPException, Response, Query
from fastapi.responses import FileResponse
from starlette.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Set
from atlassian.errors import ApiPermissionError

from statistics_st import BaseStatistics, FreeIpaStatistics, VirtStatistics, ParsecStatistics, PostgreSQLStatistics
from parsers import BaseParser, ApacheParser, ParsecParser

from utils import UtilForReadLogs
from logging_conf import main_logger

app = FastAPI(
    title="Statistics"
)

class Statistics(BaseModel):
    title_statistics: str
    username: str
    token: str
    set_of_test_types: Set
    comparison_list:  Optional[List] = None
    comparison_kernel_list:  Optional[List] = None

class Auth(BaseModel):
    username: str
    token: str

@app.post("/base-statistics")
def base_statistics_api(body: Statistics):
    if body.title_statistics == "Apache":
        parser = ApacheParser
    elif body.title_statistics == "Parsec":
        parser = ParsecParser
    else:
        parser = BaseParser
    base_stat = BaseStatistics(stat_title=body.title_statistics,
                               username=body.username,
                               tokenconf=body.token,
                               set_of_test_types=body.set_of_test_types,
                               comparison_list=body.comparison_list,
                               comparison_kernel_list=body.comparison_kernel_list,
                               score_parser=parser)
    try:
        base_stat.create()
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        base_stat.create()
    return {"OK"}

@app.post("/freeipa-statistics")
def freeipa_statistics_api(body: Statistics):
    freeipa_stat = FreeIpaStatistics(stat_title=body.title_statistics,
                                     username=body.username,
                                     tokenconf=body.token,
                                     set_of_test_types=body.set_of_test_types,
                                     )
    try:
        freeipa_stat.create()
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        freeipa_stat.create()
    return {"OK"}

@app.post("/virt-statistics")
def virt_statistics_api(body: Statistics):
    virt_stat = VirtStatistics(stat_title=body.title_statistics,
                               username=body.username,
                               tokenconf=body.token,
                               set_of_test_types=body.set_of_test_types)
    try:
        virt_stat.create()
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        virt_stat.create()
    return {"ОК"}

@app.post("/parsec-statistics")
def parsec_statistics_api(body: Statistics):
    parsec_stat = ParsecStatistics(stat_title=body.title_statistics,
                                   username=body.username,
                                   tokenconf=body.token,
                                   set_of_test_types=body.set_of_test_types)
    try:
        parsec_stat.create()
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        parsec_stat.create()
    return {"ОК"}

@app.post("/postgresql-statistics")
def postgresql_statistics_api(body: Statistics):
    postgresql_stat = PostgreSQLStatistics(stat_title=body.title_statistics,
                                           username=body.username,
                                           tokenconf=body.token,
                                           set_of_test_types=body.set_of_test_types,
                                           comparison_list=body.comparison_list,
                                           comparison_kernel_list=body.comparison_kernel_list)
    try:
        postgresql_stat.create()
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        postgresql_stat.create()

@app.post("/all-statistics")
def all_statistics(body: Auth):
    unix_stat = BaseStatistics(stat_title="UnixBench", 
                               username=body.username, 
                               tokenconf=body.token,
                               set_of_test_types={"unix", "unix_parsec"}
                               )
    try:
        unix_stat.create()
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        unix_stat.create()

    system_services_stat = BaseStatistics(stat_title="Системные службы",
                                          username=body.username, 
                                          tokenconf=body.token,
                                          set_of_test_types={"auditd-p", "auditd-f", "auditd-u", "syslog-ng"}
                                          )
    try:
        system_services_stat.create()
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        system_services_stat.create()

    file_systems_stat = BaseStatistics(stat_title="Файловые системы",
                                       username=body.username, 
                                       tokenconf=body.token,
                                       set_of_test_types={"EXFAT", "EXT2", "EXT4", "EXT4_parsec", "FAT", "NTFS", "XFS", "XFS_parsec", "OCFS2"},
                                       comparison_list=[["EXT4", "XFS"], ["EXT4", "EXT4_parsec"]])
    try:
        file_systems_stat.create()
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        file_systems_stat.create()

    postresql_stat = PostgreSQLStatistics(stat_title="PostgreSQL",
                                          username=body.username, 
                                          tokenconf=body.token,set_of_test_types={"postgresql", "postgresql-sm", "postgresql-aud-off", "psql_parsec", "psql_vanilla", "tantor_vanilla", "psql_balance"},
                                          comparison_list=[
                                              ["postgresql", "postgresql-sm"], 
                                              ["postgresql", "postgresql-aud-off"], 
                                              ["postgresql", "psql_parsec"], 
                                              ["postgresql", "psql_vanilla"]],
                                          comparison_kernel_list=["postgresql"])
    try:
        postresql_stat.create()
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        postresql_stat.create()

    apache_stat = BaseStatistics(stat_title="Apache",
                                 username=body.username, 
                                 tokenconf=body.token,
                                 set_of_test_types={"apache-rp"},
                                 score_parser=ApacheParser)
    try:
        apache_stat.create()
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        apache_stat.create()

    parsec_stat = BaseStatistics(stat_title="Parsec",
                                 username=body.username, 
                                 tokenconf=body.token,
                                 set_of_test_types={"parsec_impact-fs", "parsec_impact-fs-aud-off"},
                                 comparison_list=[["parsec_impact-fs", "parsec_impact-fs-aud-off"]],
                                 score_parser=ParsecParser)
    try:
        parsec_stat.create()
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        parsec_stat.create()

    freeipa_stat = FreeIpaStatistics(stat_title="FreeIPA",
                                     username=body.username, 
                                     tokenconf=body.token,
                                     set_of_test_types={'FreeIPA_auth'},
                                     )
    try:
        freeipa_stat.create()
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        freeipa_stat.create()

    virt_stat = VirtStatistics(stat_title="Qemu/KVM/Libvirt",
                               username=body.username, 
                               tokenconf=body.token,
                               set_of_test_types={"FIO", "vPingPong", "vUnixBench", "steal_time", "steal_time-sm"})
    try:
        virt_stat.create()
    except ApiPermissionError:
        main_logger.error("confluence тупит пробуем еще раз")
        sleep(60)
        virt_stat.create()

    return {"Вся статистика обновлена"}

@app.get("/logs")
def read_logs(last_lines: Optional[int] = Query(None, description="Number of last lines to read"), reverse: bool = False, debug: bool = False, logs_download: bool = False):
    filename = "main_statistics_logger.log" if not debug else "debug_statistics_logger.log"
    logs = UtilForReadLogs.read_last_lines(filename, last_lines, reverse=reverse)
    
    if logs_download:
        if os.path.exists(filename):
            return FileResponse(filename, media_type='application/octet-stream', filename="logs.txt")
        else:
            return {"error": "File not found"}
    
    style = """
    <style>
        body {
            background-color: #222128;
            color: white;
        }
    </style>
    """

    content = f'<html><head>{style}</head><body>'
    for line in logs:
        if "CRITICAL" in line or "ERROR" in line:
            content += f'<div style="color:red;">{line}</div>'
        elif "WARNING" in line:
            content += f'<div style="color:yellow;">{line}</div>'
        else:
            content += f'<p>{line}</p>'
    content += '</body></html>'

    return HTMLResponse(content=content)