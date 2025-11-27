#include <assert.h>
#include <unistd.h>
#include <fcntl.h>
#include <time.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <pthread.h>
#include <zlib.h>
#include <sys/types.h>
#include <sys/stat.h>
   

#define err(...) fprintf(stderr, __VA_ARGS__)


char *dir;
char text_buffer_var[1 << 20];


void random_text(char *text_buffer, size_t len) {
    static const char alphabet[] = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
    size_t alphabet_len = sizeof(alphabet) -1;

    for(size_t i=0; i<len; i++) {
        text_buffer[i] = alphabet[rand() % alphabet_len];
    }
    text_buffer[len -1] = '\0';
}



void *thread_archive(void *arg) {
    unsigned long src_len, dest_len;
    Bytef *dest_buf;
    int arch_result, archloop, i;

    archloop = (int)(intptr_t)arg;

    for (i=0; i<archloop; i++ ) {
        random_text(text_buffer_var, sizeof(text_buffer_var)); // Наполняем текстом

        src_len = sizeof(text_buffer_var);
        dest_len = compressBound(src_len); // Вычисляем максимальный размер выходного буфера
        dest_buf = malloc(dest_len);
        if (!dest_buf) {
            perror("Ошибка выделения памяти");
            exit(EXIT_FAILURE);
        }

        arch_result = compress(dest_buf, &dest_len, (Bytef*)text_buffer_var, src_len); // Архивируем переменную
        if (arch_result != Z_OK) {
            fprintf(stderr, "Ошибка сжатия: %d\\n", arch_result);
            free(dest_buf);
            continue;
        }

        free(dest_buf);
    }
    return NULL;
}



void *thread_function(void *arg)
{
    int nloop, i, fd, j;
    char rndstr[16];
    char path[255];
    Bytef *src_buf;

    nloop = (int)(intptr_t)arg;


    for (i=0; i<nloop; i++) {
        memset(rndstr, 0, sizeof(rndstr));
        for (j=0; j<15; j++)
            rndstr[j] = 'a' + rand() % 26;

        snprintf(path, 254, "%s/%s", dir, rndstr);

        fd = open(path, O_CREAT | O_RDWR | O_SYNC, 0644);
        assert(fd > 0);

        const size_t file_size = 1 << 10;  // ~1KB данных

        src_buf = malloc(file_size);
        if (!src_buf) {
            perror("Ошибка выделения памяти");
            free(src_buf);
            exit(EXIT_FAILURE);
        }
        for (size_t k=0; k<file_size; ++k)
            src_buf[k] = rand() % 256;

        write(fd, src_buf, file_size);
        close(fd);  
        unlink(path);
    }
    return 0;
}



int main(int argc, char ** argv)
{
    int nworkers, nloop, i, archloop, archworkers;
    pthread_t *worker_threads;
    pthread_t *archive_threads;

    if (argc != 6) {
        fprintf(stderr, "Usage: %s directory num_workers loop_count archive_worker_count archive_loop_count\\n", argv[0]);
        exit(EXIT_FAILURE);
    }

    srand(time(NULL));

    dir = argv[1];
    nworkers = atoi(argv[2]);
    nloop = atoi(argv[3]);
    archworkers = atoi(argv[4]);
    archloop = atoi(argv[5]);

    worker_threads = calloc(nworkers, sizeof(pthread_t)); // Создание рабочего массива потоков
    if (!worker_threads) {
        perror("calloc failed");
        exit(EXIT_FAILURE);
    }
    archive_threads = calloc(archworkers, sizeof(pthread_t)); 
    if (!archive_threads) {
        perror("calloc failed");
        exit(EXIT_FAILURE);
    }

    for (i=0; i<nworkers; i++) { // Создание потоков для файлов и врхивирования
        pthread_create(&worker_threads[i], NULL, thread_function, (void*)(intptr_t)nloop);
    }
    for (i=0; i<archworkers; i++) {
        pthread_create(&archive_threads[i], NULL, thread_archive, (void*)(intptr_t)archloop);
    }

    for (i=0; i<nworkers; i++) { // Ждём завершения всех потоков
        pthread_join(worker_threads[i], NULL);
    }
    for (i=0; i<archworkers; i++) {
        pthread_join(archive_threads[i], NULL);
    }


    free(worker_threads);
    free(archive_threads);

    printf("All threads finished.\n");
    return 0;
}