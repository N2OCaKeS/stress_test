#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <zlib.h>
#include <assert.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>

#define err(...) fprintf(stderr, __VA_ARGS__)

char *dir;

// Объявляем спинлок как атомарную переменную
volatile bool spinlock = false;

void acquire_spinlock()
{
    while (__sync_bool_compare_and_swap(&spinlock, false, true)); // Ждет, пока блокировка доступна
}

void release_spinlock()
{
    __sync_bool_compare_and_swap(&spinlock, true, false); // Освобождение блокировки
}

void* thread_function(void* arg)
{
    int nloop = *(int*)arg;
    int i, fd, ret;
    char rndstr[16], path[255], archive_path[255];
    unsigned long src_len, dest_len;
    Bytef *src_buf, *dest_buf;

    for(i = 0; i < nloop; i++) {
        // Генерация имени файла
        memset(rndstr, 0, sizeof(rndstr));
        for(int j = 0; j < 15; j++)
            rndstr[j] = 'a' + rand() % 26;

        snprintf(path, 254, "%s/%s", dir, rndstr);

        // Открытие временного файла
        fd = open(path, O_CREAT | O_RDWR | O_SYNC, 0644);
        assert(fd > 0);

        // Выделение буферов
        const size_t file_size = 1 << 20;  // ~1 MB данных
        src_buf = malloc(file_size);
        if(!src_buf) {
            perror("Ошибка выделения памяти");
            exit(EXIT_FAILURE);
        }
        for(size_t k = 0; k < file_size; ++k)
            src_buf[k] = rand() % 256;

        // Запись данных в файл
        write(fd, src_buf, file_size);
        close(fd);

        // Здесь добавляем спинлок для защиты общих ресурсов
        acquire_spinlock(); // Захватываем блокировку

        // Работа с общим ресурсом (сжатием)
        src_len = file_size;
        dest_len = compressBound(src_len);
        dest_buf = malloc(dest_len);
        if(!dest_buf) {
            free(src_buf);
            perror("Ошибка выделения памяти");
            exit(EXIT_FAILURE);
        }

        ret = compress(dest_buf, &dest_len, src_buf, src_len);
        if(ret != Z_OK) {
            fprintf(stderr, "Ошибка сжатия: %d\n", ret);
            free(src_buf);
            free(dest_buf);
            continue;
        }

        snprintf(archive_path, 254, "%s/%s.gz", dir, rndstr);
        fd = open(archive_path, O_CREAT | O_WRONLY | O_TRUNC, 0644);
        assert(fd >= 0);
        write(fd, dest_buf, dest_len);
        close(fd);

        release_spinlock(); // Освобождаем блокировку

        // Чистка временных файлов
        free(src_buf);
        free(dest_buf);
        unlink(path);
        unlink(archive_path);
    }

    return NULL;
}


int main(int argc, char ** argv)
{
    int nworkers, nloop, i;
    pthread_t *tcbs;

    if (argc != 4) {
        err("Invalid arguments!\nUsage: %s <directory> <num_workers> <loops_per_worker>\n", argv[0]);
        exit(EXIT_FAILURE);
    }

    srand(time(NULL));

    dir = argv[1];
    nworkers = atoi(argv[2]);
    nloop = atoi(argv[3]);

    // Проверяем существование и доступность каталога
    struct stat st = {0};
    if(stat(dir, &st) == -1 || !S_ISDIR(st.st_mode)) {
        err("Directory '%s' does not exist or is not accessible.\n", dir);
        exit(EXIT_FAILURE);
    }

    // Распределение памяти для массива дескрипторов потоков
    tcbs = calloc(nworkers, sizeof(pthread_t));
    if (!tcbs) {
        err("Memory allocation failed.\n");
        exit(EXIT_FAILURE);
    }

    // Создание потоков
    for (i = 0; i < nworkers; i++) {
        pthread_create(&tcbs[i], NULL, thread_function, (void*)(intptr_t)nloop);
    }

    // Дождаться завершения всех потоков
    for (i = 0; i < nworkers; i++) {
        pthread_join(tcbs[i], NULL);
    }

    free(tcbs);
    printf("END\n");
    return 0;
}


// int main()
// {
//     srand(time(NULL)); // Инициализация генератора случайных чисел
//     mkdir(DIR, 0755); // Создание временной папки

//     int nthreads = sysconf(_SC_NPROCESSORS_ONLN); // Количество ядер процессора
//     pthread_t tid[nthreads];                      // Массив потоков
//     int loop_count = 1000;                        // Кол-во итераций на поток

//     for(int i = 0; i < nthreads; i++) {
//         pthread_create(&tid[i], NULL, thread_function, &loop_count);
//     }

//     for(int i = 0; i < nthreads; i++) {
//         pthread_join(tid[i], NULL);
//     }

//     rmdir(DIR); // Убираем временную директорию
//     return 0;
// }