#define _GNU_SOURCE

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <pthread.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <stdint.h>
#include <stdatomic.h>
#include <getopt.h>



#ifdef USE_PARSEC_API
#include <parsec/pdp_common.h>
#include <parsec/pdp.h> 
#else

typedef struct {
    uint8_t  level;
    uint8_t  integrity;
    uint64_t categories;
} parsec_label_t;

#  define SO_PARSEC_LABEL  64

#endif


#define MAX_THREADS     256
#define BUF_SIZE        16384
#define MAX_LEVELS      4 

typedef struct {
    char     host[256];
    int      port;
    char     url[512];
    int      total_requests;
    int      num_threads;
    int      fixed_level;
    int      verbose;
} config_t;

typedef struct {
    atomic_long sent;
    atomic_long ok;
    atomic_long fail;
    atomic_long label_errors;
    atomic_long per_level[MAX_LEVELS];
} stats_t;

static config_t  g_config;
static stats_t   g_stats;


static int do_request(int level, uint64_t categories)
{
    int sockfd;
    struct sockaddr_in addr;
    char request[1024];
    char response[BUF_SIZE];
    int  http_status = -1;

    sockfd = socket(AF_INET, SOCK_STREAM, 0);
    if (sockfd < 0) {
        if (g_config.verbose)
            perror("socket");
        return -1;
    }

    if (set_socket_label(sockfd, level, categories) < 0) {
        atomic_fetch_add(&g_stats.label_errors, 1);
        close(sockfd);
        return -1;
    }

    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port   = htons(g_config.port);
    if (inet_pton(AF_INET, g_config.host, &addr.sin_addr) <= 0) {
        fprintf(stderr, "Неверный адрес: %s\n", g_config.host);
        close(sockfd);
        return -1;
    }

    if (connect(sockfd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        if (g_config.verbose)
            perror("connect");
        close(sockfd);
        return -1;
    }

    snprintf(request, sizeof(request),
        "GET %s HTTP/1.0\r\n"
        "Host: %s\r\n"
        "X-MRD-Level: %d\r\n"
        "Connection: close\r\n"
        "\r\n",
        g_config.url, g_config.host, level);

    if (send(sockfd, request, strlen(request), 0) < 0) {
        if (g_config.verbose)
            perror("send");
        close(sockfd);
        return -1;
    }

    int  n = recv(sockfd, response, sizeof(response) - 1, 0);
    if (n > 0) {
        response[n] = '\0';
        if (sscanf(response, "HTTP/%*s %d", &http_status) != 1)
            http_status = -1;

        if (g_config.verbose) {
            char *nl = strchr(response, '\n');
            if (nl) *nl = '\0';
            printf("[level=%d] %s → %s\n",
                   level, g_config.url, response);
        }

    
    }

    close(sockfd);
    return http_status;
}



typedef struct {
    int thread_id;
    int requests_to_do;
} thread_arg_t;

static void *worker_thread(void *arg)
{
    thread_arg_t *ta = (thread_arg_t *)arg;
    int level;
    int status;

    for (int i = 0; i < ta->requests_to_do; i++) {


        if (g_config.fixed_level >= 0) {
            level = g_config.fixed_level;
        } else {
   
            level = (ta->thread_id + i) % MAX_LEVELS;
        }

        uint64_t categories = 0;

        status = do_request(level, categories);

        atomic_fetch_add(&g_stats.sent, 1);

        if (status == 200) {
            atomic_fetch_add(&g_stats.ok, 1);
            atomic_fetch_add(&g_stats.per_level[level], 1);
        } else {
            atomic_fetch_add(&g_stats.fail, 1);
            if (g_config.verbose)
                printf("[thread %d] level=%d status=%d\n",
                       ta->thread_id, level, status);
        }
    }

    free(ta);
    return NULL;
}

static void print_usage(const char *prog)
{
    printf("Использование: %s [опции]\n"
           "  -h <host>      адрес веб-сервера (обязательно)\n"
           "  -p <port>      порт (по умолчанию 80)\n"
           "  -u <url>       путь запроса (по умолчанию /)\n"
           "  -n <requests>  всего запросов (по умолчанию 100)\n"
           "  -c <threads>   число потоков (по умолчанию 4)\n"
           "  -l <level>     фиксированный уровень МРД 0..3\n"
           "                 (без этого флага уровни чередуются 0,1,2,3)\n"
           "  -v             подробный вывод\n"
           "\nПример:\n"
           "  sudo %s -h 192.168.1.10 -n 2000 -c 8\n"
           "  sudo %s -h 192.168.1.10 -n 500 -c 4 -l 2 -v\n",
           prog, prog, prog);
}

static long time_ms(void)
{
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec * 1000L + tv.tv_usec / 1000;
}

int main(int argc, char *argv[])
{
    memset(&g_config, 0, sizeof(g_config));
    strcpy(g_config.host, "127.0.0.1");
    g_config.port            = 80;
    strcpy(g_config.url, "/");
    g_config.total_requests  = 100;
    g_config.num_threads     = 4;
    g_config.fixed_level     = -1;
    g_config.verbose         = 0;

    int opt;
    while ((opt = getopt(argc, argv, "h:p:u:n:c:l:v")) != -1) {
        switch (opt) {
        case 'h': strncpy(g_config.host, optarg, sizeof(g_config.host)-1); break;
        case 'p': g_config.port = atoi(optarg); break;
        case 'u': strncpy(g_config.url,  optarg, sizeof(g_config.url)-1);  break;
        case 'n': g_config.total_requests = atoi(optarg); break;
        case 'c': g_config.num_threads    = atoi(optarg); break;
        case 'l': g_config.fixed_level    = atoi(optarg); break;
        case 'v': g_config.verbose = 1; break;
        default:
            print_usage(argv[0]);
            return 1;
        }
    }

    if (g_config.host[0] == '\0') {
        fprintf(stderr, "Ошибка: не задан адрес сервера (-h)\n");
        print_usage(argv[0]);
        return 1;
    }

    if (g_config.num_threads > MAX_THREADS)
        g_config.num_threads = MAX_THREADS;


    memset(&g_stats, 0, sizeof(g_stats));

    printf("─────────────────────────────────────────\n");
    printf("Нагрузочный тест МРД — Astra Linux\n");
    printf("Сервер:   %s:%d%s\n", g_config.host, g_config.port, g_config.url);
    printf("Запросов: %d  |  Потоков: %d\n",
           g_config.total_requests, g_config.num_threads);
    if (g_config.fixed_level >= 0)
        printf("Уровень МРД: фиксированный = %d\n", g_config.fixed_level);
    else
        printf("Уровень МРД: чередование 0→1→2→3\n");
    printf("─────────────────────────────────────────\n");

    pthread_t threads[MAX_THREADS];
    int requests_per_thread = g_config.total_requests / g_config.num_threads;
    int leftover            = g_config.total_requests % g_config.num_threads;

    long t_start = time_ms();

    for (int i = 0; i < g_config.num_threads; i++) {
        thread_arg_t *ta = malloc(sizeof(thread_arg_t));
        ta->thread_id      = i;
        ta->requests_to_do = requests_per_thread + (i == 0 ? leftover : 0);

        if (pthread_create(&threads[i], NULL, worker_thread, ta) != 0) {
            perror("pthread_create");
            free(ta);
        }
    }

    for (int i = 0; i < g_config.num_threads; i++)
        pthread_join(threads[i], NULL);

    long elapsed = time_ms() - t_start;

    long sent         = atomic_load(&g_stats.sent);
    long ok           = atomic_load(&g_stats.ok);
    long fail         = atomic_load(&g_stats.fail);
    long label_err    = atomic_load(&g_stats.label_errors);
    double rps        = (elapsed > 0) ? (sent * 1000.0 / elapsed) : 0;

    printf("\n═════════════════════════════════════════\n");
    printf("РЕЗУЛЬТАТЫ\n");
    printf("─────────────────────────────────────────\n");
    printf("Всего запросов:       %ld\n", sent);
    printf("Успешно (HTTP 200):   %ld\n", ok);
    printf("Ошибки:               %ld\n", fail);
    printf("Ошибки метки сокета:  %ld\n", label_err);
    printf("Время:                %ld мс\n", elapsed);
    printf("Пропускная способность: %.1f req/s\n", rps);
    printf("\nПо уровням МРД (успешные):\n");
    for (int l = 0; l < MAX_LEVELS; l++)
        printf("  Уровень %d: %ld\n", l, atomic_load(&g_stats.per_level[l]));
    printf("═════════════════════════════════════════\n");

    return (fail == 0 && label_err == 0) ? 0 : 1;
}