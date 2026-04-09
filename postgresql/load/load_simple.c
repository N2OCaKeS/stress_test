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
#include <sys/stat.h>

#define err(...) fprintf(stderr, __VA_ARGS__)
#define PATH_MAX 4096

char *dir;

void *thread_function(void *arg)
{
    int nloop = *(int *) arg;
    int i, fd, j;
    char rndstr[16];
    char path[PATH_MAX];     // создаем буфер для длинных путей
    char symlink_name[PATH_MAX];
    Bytef *src_buf;
    char **n_path;

    n_path = malloc(nloop * sizeof(char*));

    srand(time(NULL)); // новое зерно для каждой итерации

    for (i=0; i<nloop; i++) {
        memset(rndstr, 0, sizeof(rndstr));
        for (j=0; j<15; j++)
            rndstr[j] = 'a' + rand() % 26;

        // создаем вложенные каталоги
        char nested_dir[PATH_MAX];
        snprintf(nested_dir, PATH_MAX, "%s/nesteddir%d", dir, i);
        mkdir(nested_dir, 0755);
        snprintf(path, PATH_MAX-1, "%s/%s", nested_dir, rndstr);

        // сохраняем созданный файл
        n_path[i] = strdup(path);

        fd = open(path, O_CREAT | O_RDWR | O_SYNC, 0644); 
        assert(fd > 0);

        const size_t small_file_size = 1 << 12;  // размер файла (~4096 байт)
        src_buf = malloc(small_file_size);
        if (!src_buf) {
            perror("Ошибка выделения памяти");
            free(src_buf);
            exit(EXIT_FAILURE);
        }
        for (size_t k=0; k<small_file_size; ++k)
            src_buf[k] = rand() % 256;

        write(fd, src_buf, small_file_size); 
        close(fd);

        // создаём символьную ссылку на файл
        snprintf(symlink_name, PATH_MAX, "%s/symlink_%d", dir, i);
        symlink(path, symlink_name);
    }

    for (i=0; i<nloop; i++) {
        unlink(n_path[i]);        
        free(n_path[i]);
        unlink(symlink_name);  
    }

    free(n_path);
    return 0;
}

int main(int argc, char **argv)
{
    int nworkers, nloop, i;
    pthread_t *tcbs;

    if (argc != 4) {
        fprintf(stderr, "Usage: %s <directory> <num_threads> <num_loops>\n", argv[0]);
        exit(1);
    }

    dir = argv[1];
    nworkers = atoi(argv[2]);
    nloop = atoi(argv[3]);

    srand(time(NULL));

    tcbs = calloc(nworkers, sizeof(pthread_t));
    for (i = 0; i < nworkers; i++) {
        pthread_create(&tcbs[i], NULL, thread_function, &nloop);
    }

    for (i=0; i < nworkers; i++) {
        pthread_join(tcbs[i], NULL);
    }

    printf("END\n");
    return 0;
}