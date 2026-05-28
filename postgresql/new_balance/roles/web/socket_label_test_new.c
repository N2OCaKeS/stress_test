/*
 * socket_label_test_new.c — HTTP-запрос с МРД-меткой через метку процесса
 *                           + Kerberos GSSAPI Negotiate аутентификация
 *
 * Подход: вместо pdp_set_fd() (явная метка на сокет) изменяем метку
 * самого процесса перед socket(). Сокет наследует метку процесса.
 * Метка процесса восстанавливается сразу после socket().
 *
 * Это устраняет проблему: pdp_set_fd() заставляет ядро клиента
 * требовать PARSEC IP-options в ответных пакетах (SYN-ACK), которые
 * сервер не шлёт — TCP-handshake зависал. При наследовании метки
 * через процесс такой принудительной проверки входящих пакетов нет.
 *
 * Установка необходимых библиотек:
 *   sudo apt-get install -y libpdp-dev libkrb5-dev
 * 
 * Сборка:
 *   gcc socket_label_test_new.c -I/usr/include/parsec -lpdp -lgssapi_krb5 \
 *       -o socket_label_test_new
 *
 * API процессных меток (из pdp.h / pdp_common.h):
 *   pdp_get_pid(0)   — получить метку текущего процесса (аналог pdp_get_current)
 *   pdp_set_pid(0,l) — установить метку текущего процесса (аналог pdp_set_current)
 *   pdpl_ilev(l)     — извлечь уровень целостности из метки
 *
 * Запуск:
 *   kinit user@BALANCE.RBT
 *   sudo execaps -c 0x804 -- ./socket_label_test_new \
 *       -h 10.0.2.20 -n web1.balance.rbt -l 2 -u /lev1.html
 *
 * Параметры:
 *   -h <ip>       IP-адрес сервера (для connect)       — смотреть: host web1.balance.rbt
 *   -n <hostname> DNS-имя сервера  (для Host: и SPN)   — web1.balance.rbt
 *   -p <port>     Порт (по умолчанию 80)
 *   -u <url>      Путь (по умолчанию /)
 *   -l <level>    Уровень МРД 0..3 (по умолчанию 0)
 */

#define _GNU_SOURCE

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <getopt.h>
#include <fcntl.h>
#include <sys/select.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/socket.h>

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
 * GSSAPI: получить Negotiate-токен (аналог curl --negotiate)
 * ───────────────────────────────────────────────────────────────────────── */
static char *get_negotiate_token(const char *hostname)
{
    OM_uint32       maj, min;
    gss_name_t      server_name   = GSS_C_NO_NAME;
    gss_ctx_id_t    ctx           = GSS_C_NO_CONTEXT;
    gss_buffer_desc input_token   = GSS_C_EMPTY_BUFFER;
    gss_buffer_desc output_token  = GSS_C_EMPTY_BUFFER;
    char           *result        = NULL;

    /* SPN формат: HTTP@web1.balance.rbt */
    char spn[512];
    snprintf(spn, sizeof(spn), "HTTP@%s", hostname);
    printf("[gss]  SPN: %s\n", spn);

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
        fprintf(stderr, "       Выполните: kinit <user>@<REALM>\n");
        goto cleanup;
    }

    printf("[gss]  Токен получен, размер: %zu байт\n", output_token.length);
    result = base64_encode((unsigned char *)output_token.value,
                           output_token.length);
    if (result)
        printf("[gss]  base64: %.40s...\n", result);

cleanup:
    gss_release_name(&min, &server_name);
    gss_release_buffer(&min, &output_token);
    if (ctx != GSS_C_NO_CONTEXT)
        gss_delete_sec_context(&min, &ctx, GSS_C_NO_BUFFER);
    return result;
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
        if (ready < 0)  { perror("[net]  select"); break; }
        if (ready == 0) { printf("[net]  Таймаут %d сек\n", timeout_sec); break; }

        n = recv(sockfd, buf + total, bufsize - 1 - total, 0);
        if (n < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) continue;
            perror("[net]  recv");
            break;
        }
        if (n == 0) {
            printf("[net]  EOF — сервер закрыл соединение\n");
            break;
        }
        total += (size_t)n;
    }
    buf[total] = '\0';
    return (ssize_t)total;
}

/* ─────────────────────────────────────────────────────────────────────────
 * ОДИН HTTP-ЗАПРОС с МРД-меткой через метку процесса
 *
 * Стратегия установки метки:
 *   1. pdp_get_pid(0)                   — сохранить текущую метку процесса
 *   2. pdpl_ilev(orig)                  — взять целостность из метки процесса
 *   3. pdpl_get_new_init_mac(level, …)  — создать метку с нужным уровнем конф.
 *   4. pdp_set_pid(0, new_lbl)          — применить к процессу
 *   5. socket()                         — сокет НАСЛЕДУЕТ метку процесса
 *   6. pdp_set_pid(0, orig_lbl)         — сразу восстановить метку процесса
 *   7. connect() / send() / recv()      — сокет уже несёт нужную метку
 *
 * pdp_set_fd() НЕ используется — именно оно вызывало зависание connect()
 * (ядро ожидало PARSEC IP-options в SYN-ACK, которых сервер не шлёт).
 * ───────────────────────────────────────────────────────────────────────── */
static int do_http_request(const char *ip, int port, const char *hostname,
                           const char *url, int level, const char *negotiate_token)
{
    /* 1. Сохраняем текущую метку процесса */
    PDPL_T *orig_label = pdp_get_pid(0);   /* 0 = текущий процесс */
    if (!orig_label) {
        fprintf(stderr, "[pdp]  pdp_get_pid(0) не удался: %s\n", strerror(errno));
        return -1;
    }
    {
        char *t = pdpl_get_text(orig_label, PDPL_FMT_TXT);
        printf("[pdp]  Метка процесса (исходная): \"%s\"\n", t ? t : "?");
        free(t);
    }

    /* 2. Строим новую метку: уровень конф. = level, целостность = как у процесса */
    PDP_ILEV_T cur_ilev = pdpl_ilev(orig_label);   /* pdpl_ilev из pdp_common.h */
    PDPL_T *new_label = pdpl_get_new_init_mac(
        (PDP_LEV_T)level, cur_ilev, 0, (PDP_CAT_T)0, (PDP_TYPE_T)0);
    if (!new_label) {
        fprintf(stderr, "[pdp]  pdpl_get_new_init_mac() NULL: %s\n", strerror(errno));
        pdpl_put(orig_label);
        return -1;
    }
    {
        char *t = pdpl_get_text(new_label, PDPL_FMT_TXT);
        printf("[pdp]  Метка процесса (новая):    \"%s\"\n", t ? t : "?");
        free(t);
    }

    /* 3. Применяем новую метку к процессу */
    if (pdp_set_pid(0, new_label) != 0) {   /* 0 = текущий процесс */
        fprintf(stderr, "[pdp]  pdp_set_pid() ошибка: %s\n", strerror(errno));
        fprintf(stderr, "       Запускайте через: sudo execaps -c 0x804 -- ./socket_label_test_new\n");
        pdpl_put(new_label);
        pdpl_put(orig_label);
        return -1;
    }
    pdpl_put(new_label);

    /* 4. Создаём сокет — наследует метку процесса (pdp_set_fd НЕ нужен) */
    int sockfd = socket(AF_INET, SOCK_STREAM, 0);
    if (sockfd < 0) {
        perror("[net]  socket");
        pdp_set_pid(0, orig_label);
        pdpl_put(orig_label);
        return -1;
    }
    printf("[net]  Сокет создан (fd=%d)\n", sockfd);

    /* 5. Сразу восстанавливаем метку процесса — сокет уже получил нужную */
    if (pdp_set_pid(0, orig_label) != 0)
        fprintf(stderr, "[pdp]  Предупреждение: не удалось восстановить метку процесса\n");
    pdpl_put(orig_label);
    orig_label = NULL;

    /* 6. Проверяем метку сокета (диагностика) */
    PDPL_T *sock_label = pdp_get_fd(sockfd);
    if (sock_label) {
        char *t = pdpl_get_text(sock_label, PDPL_FMT_TXT);
        printf("[pdp]  Метка сокета (унаследована): \"%s\"\n", t ? t : "?");
        free(t);
        pdpl_put(sock_label);
    }

    /* 7. connect() по IP */
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port   = htons((uint16_t)port);
    if (inet_pton(AF_INET, ip, &addr.sin_addr) <= 0) {
        fprintf(stderr, "[net]  Неверный IP: %s\n", ip);
        close(sockfd);
        return -1;
    }

    printf("[net]  Подключаюсь к %s:%d ...\n", ip, port);
    if (connect(sockfd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        fprintf(stderr, "[net]  connect() ошибка: %s\n", strerror(errno));
        close(sockfd);
        return -1;
    }
    printf("[net]  Подключено\n");

    /* 8. Формируем HTTP GET с Authorization: Negotiate */
    char request[8192];
    if (negotiate_token) {
        snprintf(request, sizeof(request),
            "GET %s HTTP/1.0\r\n"
            "Host: %s\r\n"
            "Authorization: Negotiate %s\r\n"
            "X-MRD-Level: %d\r\n"
            "Connection: close\r\n"
            "\r\n",
            url, hostname, negotiate_token, level);
        printf("[net]  Отправляю GET %s (с Negotiate токеном, уровень МРД=%d)\n",
               url, level);
    } else {
        snprintf(request, sizeof(request),
            "GET %s HTTP/1.0\r\n"
            "Host: %s\r\n"
            "X-MRD-Level: %d\r\n"
            "Connection: close\r\n"
            "\r\n",
            url, hostname, level);
        printf("[net]  Отправляю GET %s (без аутентификации, уровень МРД=%d)\n",
               url, level);
    }

    if (send(sockfd, request, strlen(request), 0) < 0) {
        perror("[net]  send");
        close(sockfd);
        return -1;
    }

    /* 9. Читаем ответ до EOF */
    char response[65536];
    ssize_t total = recv_all(sockfd, response, sizeof(response), 15);
    close(sockfd);

    if (total <= 0) {
        fprintf(stderr, "[net]  Пустой ответ\n");
        return -1;
    }

    /* 10. Парсим и выводим */
    int http_status = -1;
    sscanf(response, "HTTP/%*s %d", &http_status);

    char *body = strstr(response, "\r\n\r\n");
    char  headers[4096] = "";
    if (body) {
        size_t hlen = (size_t)(body - response);
        if (hlen >= sizeof(headers)) hlen = sizeof(headers) - 1;
        strncpy(headers, response, hlen);
        headers[hlen] = '\0';
        body += 4;
    } else {
        strncpy(headers, response, sizeof(headers) - 1);
        body = NULL;
    }

    printf("\n[http] ── Статус: %d ──────────────────────────\n", http_status);
    printf("%s\n", headers);
    if (body && *body) {
        printf("── Тело (первые 512 байт) ────────────────────\n");
        printf("%.512s\n", body);
    }
    printf("──────────────────────────────────────────────\n");

    switch (http_status) {
    case 200:
        printf("[мрд]  ✓ 200 OK — данные получены, уровень доступа %d\n", level);
        break;
    case 401:
        printf("[мрд]  401 — требуется аутентификация\n"
               "       Проверьте: klist  (билет Kerberos есть?)\n"
               "                  kinit user@BALANCE.RBT\n");
        break;
    case 403:
        printf("[мрд]  ✗ 403 — МРД заблокировал: уровень %d "
               "не даёт доступа к этим данным\n", level);
        break;
    default:
        printf("[мрд]  Статус %d\n", http_status);
    }

    return http_status;
}

/* ─────────────────────────────────────────────────────────────────────────
 * MAIN
 * ───────────────────────────────────────────────────────────────────────── */
int main(int argc, char *argv[])
{
    char ip[256]       = "";
    char hostname[256] = "";
    int  port          = 80;
    char url[512]      = "/";
    int  level         = 0;

    int opt;
    while ((opt = getopt(argc, argv, "h:n:p:u:l:")) != -1) {
        switch (opt) {
        case 'h': strncpy(ip,       optarg, sizeof(ip)-1);       break;
        case 'n': strncpy(hostname, optarg, sizeof(hostname)-1); break;
        case 'p': port  = atoi(optarg); break;
        case 'u': strncpy(url,  optarg, sizeof(url)-1);          break;
        case 'l': level = atoi(optarg); break;
        default:
            fprintf(stderr,
                "Использование: %s -h <ip> -n <hostname> [-p <port>] "
                "[-u <url>] [-l <level 0..3>]\n\n"
                "Пример:\n"
                "  kinit user@BALANCE.RBT\n"
                "  sudo execaps -c 0x804 -- %s \\\n"
                "      -h 10.0.2.20 -n web1.balance.rbt -l 2 -u /lev1.html\n",
                argv[0], argv[0]);
            return 1;
        }
    }

    if (!ip[0]) {
        fprintf(stderr, "Ошибка: нужен -h <ip>  (узнать: host web1.balance.rbt)\n");
        return 1;
    }
    if (!hostname[0]) {
        strncpy(hostname, ip, sizeof(hostname)-1);
        printf("[warn] -n не задан, используем IP как hostname.\n"
               "       Kerberos работает только с DNS-именем: -n web1.balance.rbt\n\n");
    }
    if (level < 0 || level > 3) {
        fprintf(stderr, "Ошибка: уровень МРД должен быть 0..3\n");
        return 1;
    }

    printf("══════════════════════════════════════════════\n");
    printf("  socket_label_test_new: МРД через метку процесса\n");
    printf("══════════════════════════════════════════════\n");
    printf("[conf] IP:          %s:%d\n", ip, port);
    printf("[conf] Hostname:    %s\n", hostname);
    printf("[conf] URL:         %s\n", url);
    printf("[conf] МРД уровень: %d\n", level);
    printf("──────────────────────────────────────────────\n");

    /* 1. Инициализация libpdp */
    if (pdp_init() != 0) {
        fprintf(stderr, "[pdp]  pdp_init() не удался: %s\n", strerror(errno));
        return 1;
    }
    /* Проверяем что метка текущего процесса доступна — ранняя диагностика */
    {
        PDPL_T *self = pdp_get_pid(0);
        if (!self) {
            fprintf(stderr, "[pdp]  pdp_get_pid(0) не удался: %s\n", strerror(errno));
            return 1;
        }
        char *t = pdpl_get_text(self, PDPL_FMT_TXT);
        printf("[pdp]  libpdp OK, метка процесса: \"%s\"\n\n", t ? t : "?");
        free(t);
        pdpl_put(self);
    }

    /* 2. Получаем Kerberos токен */
    char *negotiate_token = get_negotiate_token(hostname);
    if (!negotiate_token) {
        printf("[warn] Kerberos токен не получен — запрос пойдёт без аутентификации\n"
               "       (сервер вероятно вернёт 401)\n\n");
    }

    /* 3. Выполняем запрос */
    printf("\n");
    do_http_request(ip, port, hostname, url, level, negotiate_token);

    free(negotiate_token);
    pdp_release();
    printf("\n[pdp]  Готово.\n");
    return 0;
}
