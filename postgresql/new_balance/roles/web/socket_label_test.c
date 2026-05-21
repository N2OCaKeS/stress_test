/*
 * socket_label_test.c — Один HTTP-запрос с подменой мандатной метки сокета
 * Astra Linux Special Edition, libpdp
 *
 * Сборка:
 *   gcc socket_label_test.c -I/usr/include/parsec -lpdp -o socket_label_test
 *
 * Привилегии:
 *   sudo setcap cap_mac_admin+ep ./socket_label_test
 *
 * Запуск:
 *   ./socket_label_test -h 192.168.1.10 -p 80 -u /data -l 2
 */

#define _GNU_SOURCE

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <getopt.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/socket.h>

#include <parsec/pdp_common.h>
#include <parsec/pdp.h>

static void print_usage(const char *prog)
{
    printf("Использование: %s -h <host> [-p <port>] [-u <url>] [-l <level>]\n"
           "\n"
           "  -h <host>    Адрес веб-сервера (обязательно)\n"
           "  -p <port>    Порт (по умолчанию: 80)\n"
           "  -u <url>     Путь запроса (по умолчанию: /)\n"
           "  -l <level>   Уровень МРД 0..3 (по умолчанию: 0)\n"
           "\n"
           "Пример:\n"
           "  ./socket_label_test -h 192.168.1.10 -l 2 -u /secret\n",
           prog);
}

int main(int argc, char *argv[])
{
    /* Параметры по умолчанию */
    char host[256] = "";
    int  port      = 80;
    char url[512]  = "/";
    int  level     = 0;

    /* Разбор аргументов */
    int opt;
    while ((opt = getopt(argc, argv, "h:p:u:l:")) != -1) {
        switch (opt) {
        case 'h': strncpy(host, optarg, sizeof(host)-1); break;
        case 'p': port  = atoi(optarg); break;
        case 'u': strncpy(url,  optarg, sizeof(url)-1);  break;
        case 'l': level = atoi(optarg); break;
        default:
            print_usage(argv[0]);
            return 1;
        }
    }

    if (strlen(host) == 0) {
        fprintf(stderr, "Ошибка: не задан адрес сервера (-h)\n\n");
        print_usage(argv[0]);
        return 1;
    }

    if (level < 0 || level > 3) {
        fprintf(stderr, "Ошибка: уровень МРД должен быть 0..3\n");
        return 1;
    }

    /* ── Шаг 1. Инициализация libpdp ─────────────────────────────────── */
    if (pdp_init() != 0) {
        fprintf(stderr, "pdp_init() не удался: %s\n", strerror(errno));
        return 1;
    }
    printf("[pdp]  libpdp инициализирована\n");

    PDP_ILEV_T max_ilev = pdp_get_max_ilev();
    printf("[pdp]  Максимальный уровень целостности в системе: %u\n", max_ilev);

    /* ── Шаг 2. Создаём TCP-сокет ────────────────────────────────────── */
    int sockfd = socket(AF_INET, SOCK_STREAM, 0);
    if (sockfd < 0) {
        perror("socket");
        pdp_release();
        return 1;
    }
    printf("[net]  Сокет создан (fd=%d)\n", sockfd);

    /* ── Шаг 3. Создаём мандатную метку ─────────────────────────────── */
    /*
     * pdpl_get_new_init_mac(lev, ilev, ilinear, cat, type)
     *   lev     — уровень конфиденциальности (0..3)
     *   ilev    — уровень целостности (берём максимальный системный)
     *   ilinear — линейная целостность (0 = не задана)
     *   cat     — категории (0 = без категорий)
     *   type    — флаги типа метки (0 = обычная)
     */
    PDPL_T *label = pdpl_get_new_init_mac(
        (PDP_LEV_T)level,
        max_ilev,
        0,
        (PDP_CAT_T)0,
        (PDP_TYPE_T)0
    );

    if (label == NULL) {
        fprintf(stderr, "[pdp]  pdpl_get_new_init_mac() вернул NULL: %s\n",
                strerror(errno));
        close(sockfd);
        pdp_release();
        return 1;
    }

    /* Печатаем метку в текстовом виде для проверки */
    char *label_text = pdpl_get_text(label, PDPL_FMT_TXT);
    printf("[pdp]  Метка создана: уровень=%d  текст=\"%s\"\n",
           level, label_text ? label_text : "(не удалось получить текст)");
    free(label_text);

    /* ── Шаг 4. Устанавливаем метку на сокет — ДО connect() ─────────── */
    /*
     * pdp_set_fd(fd, label) — ключевой вызов.
     * После него ядро Astra прочитает эту метку при connect()
     * и пометит исходящее соединение соответствующим уровнем МРД.
     * Apache2 в AstraMode увидит метку через accept() на своей стороне.
     */
    int ret = pdp_set_fd(sockfd, label);
    pdpl_put(label);   /* освобождаем — pdp_set_fd сделал внутреннюю копию */

    if (ret != 0) {
        fprintf(stderr, "[pdp]  pdp_set_fd() ошибка: %s\n", strerror(errno));
        fprintf(stderr, "       Подсказка: нужен cap_mac_admin — "
                        "запустите: sudo setcap cap_mac_admin+ep ./%s\n",
                argv[0]);
        close(sockfd);
        pdp_release();
        return 1;
    }
    printf("[pdp]  Метка установлена на сокет fd=%d\n", sockfd);

    /* Проверяем — читаем метку обратно с дескриптора */
    PDPL_T *check = pdp_get_fd(sockfd);
    if (check) {
        char *check_text = pdpl_get_text(check, PDPL_FMT_TXT);
        printf("[pdp]  Проверка метки на сокете: \"%s\"\n",
               check_text ? check_text : "?");
        free(check_text);
        pdpl_put(check);
    }

    /* ── Шаг 5. connect() — теперь метка зафиксирована ─────────────── */
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port   = htons((uint16_t)port);

    if (inet_pton(AF_INET, host, &addr.sin_addr) <= 0) {
        fprintf(stderr, "[net]  Неверный адрес: %s\n", host);
        close(sockfd);
        pdp_release();
        return 1;
    }

    if (connect(sockfd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        fprintf(stderr, "[net]  connect() к %s:%d не удался: %s\n",
                host, port, strerror(errno));
        close(sockfd);
        pdp_release();
        return 1;
    }
    printf("[net]  Подключено к %s:%d\n", host, port);

    /* ── Шаг 6. Отправляем HTTP GET ──────────────────────────────────── */
    char request[1024];
    snprintf(request, sizeof(request),
        "GET %s HTTP/1.0\r\n"
        "Host: %s\r\n"
        "X-MRD-Level: %d\r\n"
        "Connection: close\r\n"
        "\r\n",
        url, host, level);

    if (send(sockfd, request, strlen(request), 0) < 0) {
        perror("[net]  send");
        close(sockfd);
        pdp_release();
        return 1;
    }
    printf("[net]  Запрос отправлен: GET %s (X-MRD-Level: %d)\n", url, level);

    /* ── Шаг 7. Читаем ответ ─────────────────────────────────────────── */
    char response[8192];
    ssize_t n = recv(sockfd, response, sizeof(response) - 1, 0);
    if (n > 0) {
        response[n] = '\0';

        /* Статус из первой строки */
        int http_status = -1;
        sscanf(response, "HTTP/%*s %d", &http_status);

        /* Печатаем только заголовки ответа (до пустой строки) */
        char *body_start = strstr(response, "\r\n\r\n");
        if (body_start) *body_start = '\0';   /* обрезаем тело */

        printf("[http] Ответ (статус %d):\n", http_status);
        printf("─────────────────────────────────\n");
        printf("%s\n", response);
        printf("─────────────────────────────────\n");

        /* Интерпретация статуса с точки зрения МРД */
        switch (http_status) {
        case 200:
            printf("[мрд]  200 OK — сервер обслужил запрос с уровнем %d\n", level);
            break;
        case 403:
            printf("[мрд]  403 Forbidden — МРД заблокировал доступ "
                   "(уровень %d недостаточен для запрошенных данных)\n", level);
            break;
        case 401:
            printf("[мрд]  401 Unauthorized — требуется аутентификация\n");
            break;
        default:
            printf("[мрд]  Статус %d\n", http_status);
            break;
        }
    } else {
        perror("[net]  recv");
    }

    /* ── Завершение ──────────────────────────────────────────────────── */
    close(sockfd);
    pdp_release();
    printf("[pdp]  Готово.\n");

    return 0;
}