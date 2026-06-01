/*
 * load_generator2.c — Многопоточный нагрузчик HTTP с МРД-меткой
 *
 * Сборка:
 *   gcc load_generator2.c -I/usr/include/parsec -lpdp -lgssapi_krb5 -lpthread \
 *       -o load_generator2
 *
 * Запуск:
 *   kinit user@BALANCE.RBT
 *   KRB5CCNAME=$(klist -l 2>/dev/null | awk 'NR==3{print $NF}') \
 *   sudo -E execaps -c 0x804 -- ./load_generator2 \
 *       -h 10.0.2.20 -n web1.balance.rbt -l 2 -u /lev2.html \
 *       -w 4 -r 1000 -o results.json
 *
 *   Ключевые флаги:
 *     sudo -E  — передать переменные окружения (включая KRB5CCNAME) в sudo
 *     KRB5CCNAME=... — явно указать ccache, чтобы root нашёл билеты пользователя
 *
 * Параметры:
 *   -h <ip>       IP-адрес сервера (для connect)
 *   -n <hostname> DNS-имя сервера  (для Host: и SPN Kerberos)
 *   -p <port>     TCP-порт (по умолчанию 80)
 *   -u <url>      Путь запроса (по умолчанию /)
 *   -l <level>    МРД-уровень 0..3 (по умолчанию 0)
 *   -w <workers>  Число параллельных потоков (по умолчанию 4)
 *   -r <count>    Общее число запросов (по умолчанию 100)
 *   -o <file>     Путь к JSON-файлу с результатами (по умолчанию не пишется)
 */

#define _GNU_SOURCE

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <getopt.h>
#include <fcntl.h>
#include <time.h>
#include <sys/select.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <pthread.h>
#include <stdatomic.h>

#include <parsec/pdp_common.h>
#include <parsec/pdp.h>

#include <gssapi/gssapi.h>
#include <gssapi/gssapi_krb5.h>

/* ─────────────────────────────────────────────────────────────────────────
 * BASE64
 * ───────────────────────────────────────────────────────────────────────── */
static const char b64[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static char *base64_encode(const unsigned char *data, size_t len)
{
    size_t out_len = 4 * ((len + 2) / 3) + 1;
    char  *out = malloc(out_len);
    if (!out) return NULL;
    size_t i = 0, j = 0;
    for (; i < len;) {
        uint32_t a = i < len ? data[i++] : 0;
        uint32_t b = i < len ? data[i++] : 0;
        uint32_t c = i < len ? data[i++] : 0;
        uint32_t t = (a << 16) | (b << 8) | c;
        out[j++] = b64[(t >> 18) & 0x3F];
        out[j++] = b64[(t >> 12) & 0x3F];
        out[j++] = (i > len + 1) ? '=' : b64[(t >> 6) & 0x3F];
        out[j++] = (i > len)     ? '=' : b64[(t)      & 0x3F];
    }
    out[j] = '\0';
    return out;
}

/* ─────────────────────────────────────────────────────────────────────────
 * GSSAPI: получить Negotiate-токен
 *
 * verbose=1 — печатать прогресс (при старте)
 * verbose=0 — тихий режим (внутри запросов, ошибки идут в stderr)
 *
 * Kerberos-токен ОДНОРАЗОВЫЙ: Apache отклоняет повторное использование
 * одного и того же токена (replay protection). gss_init_sec_context()
 * берёт сервисный билет (ST) из ccache — к KDC не обращается, операция
 * локальная.
 *
 * KEYRING:persistent thread-safe — вызов НЕ нужно сериализовать мьютексом.
 * ───────────────────────────────────────────────────────────────────────── */
static char *get_negotiate_token_ex(const char *hostname, int verbose)
{
    OM_uint32       maj, min;
    gss_name_t      server_name  = GSS_C_NO_NAME;
    gss_ctx_id_t    ctx          = GSS_C_NO_CONTEXT;
    gss_buffer_desc input_token  = GSS_C_EMPTY_BUFFER;
    gss_buffer_desc output_token = GSS_C_EMPTY_BUFFER;
    char           *result       = NULL;

    char spn[512];
    snprintf(spn, sizeof(spn), "HTTP@%s", hostname);
    if (verbose) printf("[gss]  SPN: %s\n", spn);

    gss_buffer_desc name_buf = { strlen(spn), spn };
    maj = gss_import_name(&min, &name_buf,
                          GSS_C_NT_HOSTBASED_SERVICE, &server_name);
    if (maj != GSS_S_COMPLETE) {
        fprintf(stderr, "[gss]  gss_import_name() ошибка maj=0x%x min=0x%x\n",
                maj, min);
        return NULL;
    }

    maj = gss_init_sec_context(
        &min,
        GSS_C_NO_CREDENTIAL,
        &ctx,
        server_name,
        GSS_C_NO_OID,
        GSS_C_MUTUAL_FLAG | GSS_C_SEQUENCE_FLAG,
        0,
        GSS_C_NO_CHANNEL_BINDINGS,
        &input_token,
        NULL,
        &output_token,
        NULL,
        NULL
    );

    if (maj != GSS_S_COMPLETE && maj != GSS_S_CONTINUE_NEEDED) {
        OM_uint32 msg_ctx = 0, stat_min;
        gss_buffer_desc msg;
        fprintf(stderr, "[gss]  gss_init_sec_context() ошибка:\n");
        do {
            gss_display_status(&stat_min, maj, GSS_C_GSS_CODE,
                               GSS_C_NO_OID, &msg_ctx, &msg);
            fprintf(stderr, "       GSS: %.*s\n",
                    (int)msg.length, (char *)msg.value);
            gss_release_buffer(&stat_min, &msg);
        } while (msg_ctx);
        do {
            gss_display_status(&stat_min, min, GSS_C_MECH_CODE,
                               GSS_C_NO_OID, &msg_ctx, &msg);
            fprintf(stderr, "       KRB: %.*s\n",
                    (int)msg.length, (char *)msg.value);
            gss_release_buffer(&stat_min, &msg);
        } while (msg_ctx);
        fprintf(stderr, "       Если запуск через sudo: передайте KRB5CCNAME явно.\n"
                        "       Пример: KRB5CCNAME=$(klist -l | awk 'NR==3{print $NF}') sudo -E execaps ...\n");
        goto cleanup;
    }

    if (verbose)
        printf("[gss]  Токен получен, размер: %zu байт\n", output_token.length);
    result = base64_encode((unsigned char *)output_token.value,
                           output_token.length);

cleanup:
    gss_release_name(&min, &server_name);
    gss_release_buffer(&min, &output_token);
    if (ctx != GSS_C_NO_CONTEXT)
        gss_delete_sec_context(&min, &ctx, GSS_C_NO_BUFFER);
    return result;
}

static char *get_negotiate_token(const char *hostname)
{
    return get_negotiate_token_ex(hostname, 1);
}

/* ─────────────────────────────────────────────────────────────────────────
 * ЧТЕНИЕ ОТВЕТА до EOF с таймаутом
 * ───────────────────────────────────────────────────────────────────────── */
static ssize_t recv_all(int sockfd, char *buf, size_t bufsize, int timeout_sec)
{
    size_t  total = 0;
    ssize_t n;

    int flags = fcntl(sockfd, F_GETFL, 0);
    fcntl(sockfd, F_SETFL, flags | O_NONBLOCK);

    while (total < bufsize - 1) {
        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(sockfd, &rfds);
        struct timeval tv = { .tv_sec = timeout_sec, .tv_usec = 0 };

        int ready = select(sockfd + 1, &rfds, NULL, NULL, &tv);
        if (ready < 0)  { break; }
        if (ready == 0) { break; }

        n = recv(sockfd, buf + total, bufsize - 1 - total, 0);
        if (n < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) continue;
            break;
        }
        if (n == 0) break;
        total += (size_t)n;
    }
    buf[total] = '\0';
    return (ssize_t)total;
}

/* ─────────────────────────────────────────────────────────────────────────
 * Глобальное состояние
 * ───────────────────────────────────────────────────────────────────────── */

/*
 * Мьютекс покрывает ТОЛЬКО смену метки процесса + создание сокета:
 *   pdp_set_pid(new) → socket() → pdp_set_pid(orig)
 *
 * pdp_set_pid(0,...) меняет метку всего процесса, не отдельного потока.
 * Без мьютекса поток B мог бы создать сокет с меткой потока A.
 *
 * GSSAPI (get_negotiate_token_ex) выполняется ВНЕ мьютекса: ccache типа
 * KEYRING:persistent безопасен для конкурентного чтения из нескольких потоков.
 *
 * I/O (connect/send/recv) выполняется ВНЕ мьютекса — параллельно.
 */
static pthread_mutex_t g_label_mutex = PTHREAD_MUTEX_INITIALIZER;

static atomic_long g_dispatched    = 0;
static atomic_long g_total_sent    = 0;
static atomic_long g_ok            = 0;  /* 200 */
static atomic_long g_forbidden     = 0;  /* 403 */
static atomic_long g_unauthorized  = 0;  /* 401 */
static atomic_long g_err_gss       = 0;  /* сбой gss_init_sec_context */
static atomic_long g_err_label     = 0;  /* сбой pdp_get/set_pid или socket() */
static atomic_long g_err_conn      = 0;  /* ошибка connect() */
static atomic_long g_err_io        = 0;  /* ошибка send/recv или пустой ответ */
static atomic_long g_other         = 0;  /* прочие HTTP-статусы */
static atomic_long g_total_usec    = 0;

typedef struct {
    char ip[256];
    char hostname[256];
    int  port;
    char url[512];
    int  level;
    long total_requests;
    int  delay_ms;
    char json_output[512];
} Config;

static Config g_cfg;

/*
 * Коды возврата do_one_request():
 *   >= 0  HTTP-статус
 *   -1    сбой pdp_get/set_pid или socket()
 *   -2    ошибка connect()
 *   -3    ошибка send/recv или пустой ответ
 *   -4    сбой GSSAPI (gss_init_sec_context вернул NULL)
 */
#define ERR_LABEL   (-1)
#define ERR_CONNECT (-2)
#define ERR_IO      (-3)
#define ERR_GSS     (-4)

static int do_one_request(void)
{
    struct timespec ts_start, ts_end;
    clock_gettime(CLOCK_MONOTONIC, &ts_start);

    /*
     * 1. Kerberos-токен — получаем ВНЕ мьютекса.
     *    KEYRING:persistent thread-safe, конкурентные вызовы безопасны.
     *    Токен одноразовый: каждый запрос требует свежего AP-REQ.
     *    Возвращает NULL при сбое (ошибка уже напечатана в stderr).
     */
    char *token = get_negotiate_token_ex(g_cfg.hostname, 0);
    if (!token)
        return ERR_GSS;

    /*
     * 2. Критическая секция: смена метки процесса + создание сокета.
     *    pdp_set_pid(0,...) действует на весь процесс — мьютекс обязателен.
     */
    int sockfd;
    pthread_mutex_lock(&g_label_mutex);
    {
        PDPL_T *orig_label = pdp_get_pid(0);
        if (!orig_label) {
            pthread_mutex_unlock(&g_label_mutex);
            free(token);
            return ERR_LABEL;
        }

        PDP_ILEV_T cur_ilev = pdpl_ilev(orig_label);
        PDPL_T *new_label = pdpl_get_new_init_mac(
            (PDP_LEV_T)g_cfg.level, cur_ilev, 0,
            (PDP_CAT_T)0, (PDP_TYPE_T)0);
        if (!new_label) {
            pdpl_put(orig_label);
            pthread_mutex_unlock(&g_label_mutex);
            free(token);
            return ERR_LABEL;
        }

        if (pdp_set_pid(0, new_label) != 0) {
            pdpl_put(new_label);
            pdpl_put(orig_label);
            pthread_mutex_unlock(&g_label_mutex);
            free(token);
            return ERR_LABEL;
        }
        pdpl_put(new_label);

        sockfd = socket(AF_INET, SOCK_STREAM, 0);

        pdp_set_pid(0, orig_label);
        pdpl_put(orig_label);
    }
    pthread_mutex_unlock(&g_label_mutex);

    if (sockfd < 0) {
        free(token);
        return ERR_LABEL;
    }

    /* ── Далее — I/O без мьютекса, потоки работают параллельно ── */

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port   = htons((uint16_t)g_cfg.port);
    inet_pton(AF_INET, g_cfg.ip, &addr.sin_addr);

    if (connect(sockfd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        close(sockfd);
        free(token);
        return ERR_CONNECT;
    }

    char request[8192];
    snprintf(request, sizeof(request),
        "GET %s HTTP/1.0\r\n"
        "Host: %s\r\n"
        "Authorization: Negotiate %s\r\n"
        "X-MRD-Level: %d\r\n"
        "Connection: close\r\n"
        "\r\n",
        g_cfg.url, g_cfg.hostname, token, g_cfg.level);
    free(token);

    if (send(sockfd, request, strlen(request), 0) < 0) {
        close(sockfd);
        return ERR_IO;
    }

    char response[65536];
    ssize_t n = recv_all(sockfd, response, sizeof(response), 15);
    close(sockfd);

    if (n <= 0)
        return ERR_IO;

    int http_status = -1;
    sscanf(response, "HTTP/%*s %d", &http_status);

    clock_gettime(CLOCK_MONOTONIC, &ts_end);
    long usec = (ts_end.tv_sec  - ts_start.tv_sec)  * 1000000L
              + (ts_end.tv_nsec - ts_start.tv_nsec) / 1000L;
    atomic_fetch_add(&g_total_usec, usec);

    return http_status;
}

/* ─────────────────────────────────────────────────────────────────────────
 * Функция потока
 * ───────────────────────────────────────────────────────────────────────── */
static void *worker_thread(void *arg)
{
    (void)arg;

    while (1) {
        long slot = atomic_fetch_add(&g_dispatched, 1);
        if (slot >= g_cfg.total_requests)
            break;

        int status = do_one_request();
        atomic_fetch_add(&g_total_sent, 1);

        if (g_cfg.delay_ms > 0)
            usleep((useconds_t)g_cfg.delay_ms * 1000);

        if      (status == 200)         atomic_fetch_add(&g_ok, 1);
        else if (status == 403)         atomic_fetch_add(&g_forbidden, 1);
        else if (status == 401)         atomic_fetch_add(&g_unauthorized, 1);
        else if (status == ERR_GSS)     atomic_fetch_add(&g_err_gss, 1);
        else if (status == ERR_LABEL)   atomic_fetch_add(&g_err_label, 1);
        else if (status == ERR_CONNECT) atomic_fetch_add(&g_err_conn, 1);
        else if (status == ERR_IO)      atomic_fetch_add(&g_err_io, 1);
        else                            atomic_fetch_add(&g_other, 1);
    }

    return NULL;
}

/* ─────────────────────────────────────────────────────────────────────────
 * Запись результатов в JSON
 * ───────────────────────────────────────────────────────────────────────── */
static void write_json_results(
    const char *path,
    long sent, long ok, long forbidden, long unauthorized, long other,
    long err_gss, long err_label, long err_conn, long err_io,
    double wall_sec, double rps, double avg_ms,
    int workers)
{
    FILE *f = fopen(path, "w");
    if (!f) {
        fprintf(stderr, "[json] Не удалось открыть файл для записи: %s (%s)\n",
                path, strerror(errno));
        return;
    }

    time_t now = time(NULL);
    char ts[32];
    strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%S", localtime(&now));

    long errors = err_gss + err_label + err_conn + err_io;

    fprintf(f,
        "{\n"
        "  \"timestamp\": \"%s\",\n"
        "  \"config\": {\n"
        "    \"ip\": \"%s\",\n"
        "    \"hostname\": \"%s\",\n"
        "    \"port\": %d,\n"
        "    \"url\": \"%s\",\n"
        "    \"mrd_level\": %d,\n"
        "    \"workers\": %d,\n"
        "    \"total_requests\": %ld,\n"
        "    \"delay_ms\": %d\n"
        "  },\n"
        "  \"results\": {\n"
        "    \"sent\": %ld,\n"
        "    \"http_200\": %ld,\n"
        "    \"http_401\": %ld,\n"
        "    \"http_403\": %ld,\n"
        "    \"http_other\": %ld,\n"
        "    \"errors\": {\n"
        "      \"total\": %ld,\n"
        "      \"gssapi\": %ld,\n"
        "      \"label_socket\": %ld,\n"
        "      \"connect\": %ld,\n"
        "      \"send_recv\": %ld\n"
        "    }\n"
        "  },\n"
        "  \"performance\": {\n"
        "    \"wall_sec\": %.3f,\n"
        "    \"rps\": %.2f,\n"
        "    \"avg_latency_ms\": %.2f\n"
        "  }\n"
        "}\n",
        ts,
        g_cfg.ip, g_cfg.hostname, g_cfg.port, g_cfg.url,
        g_cfg.level, workers, g_cfg.total_requests, g_cfg.delay_ms,
        sent, ok, unauthorized, forbidden, other,
        errors, err_gss, err_label, err_conn, err_io,
        wall_sec, rps, avg_ms);

    fclose(f);
    printf("[json] Результаты записаны в: %s\n", path);
}

/* ─────────────────────────────────────────────────────────────────────────
 * MAIN
 * ───────────────────────────────────────────────────────────────────────── */
int main(int argc, char *argv[])
{
    memset(&g_cfg, 0, sizeof(g_cfg));
    g_cfg.port           = 80;
    strcpy(g_cfg.url, "/");
    g_cfg.level          = 0;
    g_cfg.total_requests = 100;

    int workers = 4;

    int opt;
    while ((opt = getopt(argc, argv, "h:n:p:u:l:w:r:d:o:")) != -1) {
        switch (opt) {
        case 'h': strncpy(g_cfg.ip,          optarg, sizeof(g_cfg.ip)-1);          break;
        case 'n': strncpy(g_cfg.hostname,    optarg, sizeof(g_cfg.hostname)-1);    break;
        case 'p': g_cfg.port              = atoi(optarg);                           break;
        case 'u': strncpy(g_cfg.url,         optarg, sizeof(g_cfg.url)-1);         break;
        case 'l': g_cfg.level             = atoi(optarg);                           break;
        case 'w': workers                 = atoi(optarg);                           break;
        case 'r': g_cfg.total_requests    = atol(optarg);                           break;
        case 'd': g_cfg.delay_ms          = atoi(optarg);                           break;
        case 'o': strncpy(g_cfg.json_output, optarg, sizeof(g_cfg.json_output)-1); break;
        default:
            fprintf(stderr,
                "Использование: %s -h <ip> -n <hostname> [-p <port>] [-u <url>]\n"
                "               [-l <level 0..3>] [-w <workers>] [-r <requests>]\n"
                "               [-d <delay_ms>] [-o <json_file>]\n\n"
                "  -d <ms>    задержка между запросами в каждом потоке (диагностика скорости)\n"
                "  -o <file>  записать итоговую статистику в JSON-файл\n\n"
                "Пример:\n"
                "  kinit user@BALANCE.RBT\n"
                "  KRB5CCNAME=$(klist -l | awk 'NR==3{print $NF}') \\\n"
                "  sudo -E execaps -c 0x804 -- %s \\\n"
                "      -h 10.0.2.20 -n web1.balance.rbt -l 2 -u /lev2.html \\\n"
                "      -w 4 -r 1000 -o results.json\n",
                argv[0], argv[0]);
            return 1;
        }
    }

    if (!g_cfg.ip[0]) {
        fprintf(stderr, "Ошибка: нужен -h <ip>  (узнать: host web1.balance.rbt)\n");
        return 1;
    }
    if (!g_cfg.hostname[0])
        strncpy(g_cfg.hostname, g_cfg.ip, sizeof(g_cfg.hostname)-1);
    if (g_cfg.level < 0 || g_cfg.level > 3) {
        fprintf(stderr, "Ошибка: уровень МРД должен быть 0..3\n");
        return 1;
    }
    if (workers < 1) workers = 1;
    if (g_cfg.total_requests < 1) {
        fprintf(stderr, "Ошибка: -r должен быть >= 1\n");
        return 1;
    }

    printf("══════════════════════════════════════════════\n");
    printf("  load_generator2: нагрузчик\n");
    printf("══════════════════════════════════════════════\n");
    printf("[conf] IP:          %s:%d\n", g_cfg.ip, g_cfg.port);
    printf("[conf] Hostname:    %s\n",    g_cfg.hostname);
    printf("[conf] URL:         %s\n",    g_cfg.url);
    printf("[conf] МРД уровень: %d\n",    g_cfg.level);
    printf("[conf] Потоков:     %d\n",    workers);
    printf("[conf] Запросов:    %ld\n",   g_cfg.total_requests);
    if (g_cfg.delay_ms > 0)
        printf("[conf] Задержка:    %d мс/запрос\n", g_cfg.delay_ms);
    if (g_cfg.json_output[0])
        printf("[conf] JSON-вывод:  %s\n", g_cfg.json_output);
    printf("──────────────────────────────────────────────\n");

    /* Диагностика: ccache под sudo */
    {
        const char *ccname = getenv("KRB5CCNAME");
        if (geteuid() == 0 && (!ccname || !*ccname)) {
            fprintf(stderr,
                "[warn] Запуск от root без KRB5CCNAME.\n"
                "       Если GSSAPI падает — передайте ccache явно:\n"
                "       KRB5CCNAME=$(klist -l | awk 'NR==3{print $NF}') sudo -E execaps ...\n\n");
        } else if (ccname) {
            printf("[krb5] KRB5CCNAME=%s\n", ccname);
        }
    }

    /* 1. Инициализация libpdp */
    if (pdp_init() != 0) {
        fprintf(stderr, "[pdp]  pdp_init() не удался: %s\n", strerror(errno));
        return 1;
    }
    {
        PDPL_T *self = pdp_get_pid(0);
        if (!self) {
            fprintf(stderr, "[pdp]  pdp_get_pid(0) не удался: %s\n", strerror(errno));
            pdp_release();
            return 1;
        }
        char *t = pdpl_get_text(self, PDPL_FMT_TXT);
        printf("[pdp]  Метка процесса (начальная): \"%s\"\n", t ? t : "?");
        free(t);

        PDP_ILEV_T cur_ilev = pdpl_ilev(self);
        PDPL_T *test_lbl = pdpl_get_new_init_mac(
            (PDP_LEV_T)g_cfg.level, cur_ilev, 0, (PDP_CAT_T)0, (PDP_TYPE_T)0);
        if (test_lbl && pdp_set_pid(0, test_lbl) == 0) {
            int tfd = socket(AF_INET, SOCK_STREAM, 0);
            pdp_set_pid(0, self);
            if (tfd >= 0) {
                PDPL_T *sl = pdp_get_fd(tfd);
                char *st = sl ? pdpl_get_text(sl, PDPL_FMT_TXT) : NULL;
                printf("[pdp]  Метка сокетов запросов: \"%s\"\n", st ? st : "?");
                free(st);
                if (sl) pdpl_put(sl);
                close(tfd);
            }
            pdpl_put(test_lbl);
        } else {
            pdp_set_pid(0, self);
            fprintf(stderr, "[pdp]  pdp_set_pid() не удался: %s\n"
                            "       Запускайте через: sudo execaps -c 0x804 --\n",
                    strerror(errno));
            pdpl_put(self);
            pdp_release();
            return 1;
        }
        pdpl_put(self);
    }

    /* 2. Проверка Kerberos */
    {
        char *probe = get_negotiate_token(g_cfg.hostname);
        if (!probe) {
            fprintf(stderr,
                "[err]  Kerberos токен не получен. Невозможно продолжить.\n"
                "       Выполните: kinit user@BALANCE.RBT\n"
                "       Если запуск через sudo: KRB5CCNAME=$(klist -l | awk 'NR==3{print $NF}') sudo -E execaps ...\n");
            pdp_release();
            return 1;
        }
        printf("[gss]  Kerberos OK. Каждый запрос получит свой токен.\n\n");
        free(probe);
    }

    printf("[load] Запускаю %d потоков, всего %ld запросов...\n",
           workers, g_cfg.total_requests);

    struct timespec ts_wall_start, ts_wall_end;
    clock_gettime(CLOCK_MONOTONIC, &ts_wall_start);

    /* 3. Запускаем воркеры */
    pthread_t *threads = malloc((size_t)workers * sizeof(pthread_t));
    if (!threads) {
        fprintf(stderr, "[load] malloc для массива потоков не удался\n");
        pdp_release();
        return 1;
    }

    for (int i = 0; i < workers; i++) {
        if (pthread_create(&threads[i], NULL, worker_thread, NULL) != 0) {
            fprintf(stderr, "[load] pthread_create #%d ошибка: %s\n",
                    i, strerror(errno));
            for (int j = 0; j < i; j++)
                pthread_join(threads[j], NULL);
            free(threads);
            pdp_release();
            return 1;
        }
    }

    /* 4. Ждём завершения всех потоков */
    for (int i = 0; i < workers; i++)
        pthread_join(threads[i], NULL);

    clock_gettime(CLOCK_MONOTONIC, &ts_wall_end);
    free(threads);

    /* 5. Итоговая статистика */
    long sent         = atomic_load(&g_total_sent);
    long ok           = atomic_load(&g_ok);
    long forbidden    = atomic_load(&g_forbidden);
    long unauthorized = atomic_load(&g_unauthorized);
    long err_gss      = atomic_load(&g_err_gss);
    long err_label    = atomic_load(&g_err_label);
    long err_conn     = atomic_load(&g_err_conn);
    long err_io       = atomic_load(&g_err_io);
    long other        = atomic_load(&g_other);
    long total_usec   = atomic_load(&g_total_usec);

    double wall_sec = (double)(ts_wall_end.tv_sec  - ts_wall_start.tv_sec)
                    + (double)(ts_wall_end.tv_nsec - ts_wall_start.tv_nsec) / 1e9;
    double rps    = (wall_sec > 0.0) ? (double)sent / wall_sec : 0.0;
    double avg_ms = (sent > 0)       ? (double)total_usec / (double)sent / 1000.0 : 0.0;

    printf("\n══════════════════════════════════════════════\n");
    printf("  РЕЗУЛЬТАТЫ\n");
    printf("══════════════════════════════════════════════\n");
    printf("[stat] Всего:             %ld\n", sent);
    printf("[stat] 200 OK:            %ld\n", ok);
    printf("[stat] 403 Forbidden:     %ld\n", forbidden);
    printf("[stat] 401 Unauthorized:  %ld\n", unauthorized);
    printf("[stat] Другие статусы:    %ld\n", other);
    if (err_gss + err_label + err_conn + err_io > 0) {
        printf("[stat] Ошибки итого:      %ld\n",
               err_gss + err_label + err_conn + err_io);
        printf("[stat]   GSSAPI (нет токена): %ld\n", err_gss);
        printf("[stat]   метка/сокет:         %ld\n", err_label);
        printf("[stat]   connect():           %ld\n", err_conn);
        printf("[stat]   send/recv:           %ld\n", err_io);
    }
    printf("──────────────────────────────────────────────\n");
    printf("[stat] Время выполнения:  %.2f сек\n", wall_sec);
    printf("[stat] Скорость:          %.1f req/s\n", rps);
    printf("[stat] Средняя latency:   %.1f мс\n",   avg_ms);
    printf("══════════════════════════════════════════════\n");

    if (g_cfg.json_output[0])
        write_json_results(g_cfg.json_output,
            sent, ok, forbidden, unauthorized, other,
            err_gss, err_label, err_conn, err_io,
            wall_sec, rps, avg_ms, workers);

    pdp_release();
    return 0;
}
