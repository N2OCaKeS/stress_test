#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <curl/curl.h>
#include <unistd.h>
#include <time.h>

#define SERVER "lowserver.stress-testing.local"
#define ADMIN_PASS "12345678"
#define NUM_USERS 10000
#define NUM_THREADS 20

double get_time() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

size_t write_cb(void *data, size_t size, size_t nmemb, void *userdata) {
    size_t realsize = size * nmemb;
    char **response = (char **)userdata;
    *response = realloc(*response, realsize + 1);
    memcpy(*response, data, realsize);
    (*response)[realsize] = 0;
    return realsize;
}

char *csrf_token = NULL;

void *worker(void *arg) {
    CURL *curl = curl_easy_init();
    char *resp = NULL;

    curl_easy_setopt(curl, CURLOPT_URL, "https://" SERVER "/ipa/ui/");
    curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);
    curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 0L);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_cb);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &resp);
    curl_easy_setopt(curl, CURLOPT_COOKIEJAR, "cookies.txt");
    curl_easy_perform(curl);
    free(resp); resp = NULL;

    struct curl_slist *h = NULL;
    h = curl_slist_append(h, "referer: https://" SERVER "/ipa/ui/");
    curl_easy_setopt(curl, CURLOPT_URL, "https://" SERVER "/ipa/session/login_password");
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, "user=admin&password=" ADMIN_PASS);
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, h);
    curl_easy_perform(curl);
    curl_slist_free_all(h);

    struct curl_slist *cookies = NULL;
    curl_easy_getinfo(curl, CURLINFO_COOKIELIST, &cookies);
    struct curl_slist *tmp = cookies;
    while (tmp) {
        if (strstr(tmp->data, "ipa_session")) {
            char *p = strstr(tmp->data, "csrf=");
            if (p) {
                p += 5;
                char *end = strchr(p, '\t');
                if (end) *end = 0;
                csrf_token = strdup(p);
                if (end) *end = '\t';
                break;
            }
        }
        tmp = tmp->next;
    }
    curl_slist_free_all(cookies);

    for (int i = *(int*)arg; i <= NUM_USERS; i += NUM_THREADS) {
        char username[32];
        snprintf(username, sizeof(username), "user%d", i);

        char json[1024];
        snprintf(json, sizeof(json),
            "{\"method\":\"user_add\",\"params\":[[\"%s\"],{"
            "\"givenname\":\"Test\",\"sn\":\"Тестов\","
            "\"userpassword\":\"123456\",\"random\":false}],\"id\":%d}",
            username, i);

        struct curl_slist *headers = NULL;
        headers = curl_slist_append(headers, "Content-Type: application/json");
        headers = curl_slist_append(headers, "Referer: https://" SERVER "/ipa/ui/");
        if (csrf_token) {
            char csrf_header[256];
            snprintf(csrf_header, sizeof(csrf_header), "X-CSRF-Token: %s", csrf_token);
            headers = curl_slist_append(headers, csrf_header);
        }

        curl_easy_setopt(curl, CURLOPT_URL, "https://" SERVER "/ipa/session/json");
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, json);
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        curl_easy_setopt(curl, CURLOPT_COOKIEFILE, "cookies.txt");
        curl_easy_setopt(curl, CURLOPT_COOKIEJAR, "cookies.txt");

        CURLcode res = curl_easy_perform(curl);
        long code = 0;
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &code);

        if (res == CURLE_OK && code == 200) {
            printf("\033[32m+ %s\033[0m\n", username);
        } else {
            printf("\033[31m- %s (HTTP %ld)\033[0m\n", username, code);
        }

        curl_slist_free_all(headers);
        usleep(60000);
    }

    curl_easy_cleanup(curl);
    return NULL;
}

int main() {
    curl_global_init(CURL_GLOBAL_ALL);
    unlink("cookies.txt");

    double start = get_time();

    pthread_t th[NUM_THREADS];
    int ids[NUM_THREADS];
    for (int i = 0; i < NUM_THREADS; i++) {
        ids[i] = i + 1;
        pthread_create(&th[i], NULL, worker, &ids[i]);
    }
    for (int i = 0; i < NUM_THREADS; i++) pthread_join(th[i], NULL);

    if (csrf_token) free(csrf_token);

    printf("\nГотово за %.2f секунд\n", get_time() - start);

    curl_global_cleanup();
    return 0;
}

// gcc -O2 -o ipa_create_users_test ipa_create_users_test.c -lcurl -lpthread