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
#include <sys/wait.h>

#define err(...) fprintf(stderr, __VA_ARGS__)
#define PATH_MAX 4096

char *dir;

void *thread_function(int nloop)
{
    int i, fd, j;
    char rndstr[16], path[PATH_MAX], symlink_name[PATH_MAX];
    char **n_path;

    const size_t file_size = 1 << 12;  // примерно 4 КБ

    Bytef *src_buf = malloc(file_size);
    if (!src_buf) {
        perror("Ошибка выделения памяти");
        exit(EXIT_FAILURE);
    }

    n_path = malloc(nloop * sizeof(char*));

    srand(time(NULL)); 

    

    // Основной цикл создания файлов и директорий
    for (i=0; i<nloop; i++) {
        memset(rndstr, 0, sizeof(rndstr));
        for (j=0; j<15; j++)
            rndstr[j] = 'a' + rand() % 26;

        // создание вложенных каталогов
        char nested_dir[PATH_MAX];
        snprintf(nested_dir, PATH_MAX, "%s/nesteddir%d", dir, i);
        mkdir(nested_dir, 0755);
        snprintf(path, PATH_MAX-1, "%s/%s", nested_dir, rndstr);

        // сохранение пути файла
        n_path[i] = strdup(path);

        // открытие файла на запись
        fd = open(path, O_CREAT | O_RDWR | O_SYNC, 0644); 
        assert(fd > 0);

        // Запись небольшого объема данных в файл
        // src_buf = malloc(file_size);
        // if (!src_buf) {
        //    perror("Ошибка выделения памяти");
        //    free(src_buf);
        //    exit(EXIT_FAILURE);
        // }
        for (size_t k=0; k<file_size; ++k)
            src_buf[k] = rand() % 256;

        write(fd, src_buf, file_size); 
        close(fd);

        // создание символической ссылки на файл
        snprintf(symlink_name, PATH_MAX, "%s/symlink_%d", dir, i);
        symlink(path, symlink_name);
    }

    // Чтение созданных файлов
    for (i=0; i<nloop; i++) {
        // Открываем файл на чтение
        fd = open(n_path[i], O_RDONLY);
        assert(fd >= 0);   // проверяем успешность открытия файла

        // Чтение содержимого файла 
        ssize_t bytes_read = read(fd, src_buf, file_size);
        printf("Read %zd bytes from file '%s'\\n", bytes_read, n_path[i]);

        close(fd);  // закрываем файл
    }

    // Удаляем созданные файлы и ссылки
    for (i=0; i<nloop; i++) {
        unlink(n_path[i]);      // удаляем сам файл
        free(n_path[i]);        // освобождаем память, выделенную под имя файла
        snprintf(symlink_name, PATH_MAX, "%s/symlink_%d", dir, i);
        unlink(symlink_name);   // удаляем символическую ссылку
    }

    free(n_path);
    free(src_buf);
    return NULL;
}



int main(int argc, char **argv)
{
    int nworkers, nloop, i, wpid, status;

    if (argc != 4) {
        fprintf(stderr, "Usage: %s <directory> <num_threads> <num_loops>\n", argv[0]);
        exit(1);
    }

    dir = argv[1];
    nworkers = atoi(argv[2]);
    nloop = atoi(argv[3]);

    srand(time(NULL));

    int pid;
    for (i=0; i<nworkers; i++) {
        switch(pid=fork()) {
        case -1:
            perror("Fork process error");
            exit(1);
        case 0:
            // дочерний процесс
            thread_function(nloop);
            exit(0);
        default:
            printf("PARENT: my child:%d\n", pid);
            break;
        }
    }

    printf("PARENT: done.\n");
    while ((wpid = wait(&status)) > 0) {
        printf("%d child done with status %d\n", wpid, status);
    }

    printf("END\n");
    return 0;
}
