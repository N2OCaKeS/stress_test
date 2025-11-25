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

void *thread_function(void *arg)
{
    int nloop, i, fd, j, ret;
    char rndstr[16];
    char path[255];
    char archive_path[255];
    unsigned long src_len, dest_len;
    Bytef *src_buf, *dest_buf;

    nloop = (int)(intptr_t)arg;


    for (i = 0; i < nloop; i++) {
        memset(rndstr, 0, sizeof(rndstr));
        for (j = 0; j < 15; j++)
            rndstr[j] = 'a' + rand() % 26;

        snprintf(path, 254, "%s/%s", dir, rndstr);

        fd = open(path, O_CREAT | O_RDWR | O_SYNC, 0644);
        assert(fd > 0);

        const size_t file_size = 1 << 20;  // ~1MB данных

        src_buf = malloc(file_size);
        if (!src_buf) {
            perror("Ошибка выделения памяти");
            exit(EXIT_FAILURE);
        }
        for (size_t k = 0; k < file_size; ++k)
            src_buf[k] = rand() % 256;

        write(fd, src_buf, file_size);
        close(fd);

        //Архивируем файл
        src_len = file_size;
        dest_len = compressBound(src_len);
        dest_buf = malloc(dest_len);
        if (!dest_buf) {
            free(src_buf);
            perror("Ошибка выделения памяти");
            exit(EXIT_FAILURE);
        }

        ret = compress(dest_buf, &dest_len, src_buf, src_len);
        if (ret != Z_OK) {
            fprintf(stderr, "Ошибка сжатия: %d\\n", ret);
            free(src_buf);
            free(dest_buf);
            continue;
        }

        snprintf(archive_path, 254, "%s/%s.gz", dir, rndstr);
        fd = open(archive_path, O_CREAT | O_WRONLY | O_TRUNC, 0644);
        assert(fd >= 0);
        write(fd, dest_buf, dest_len);
        close(fd);
        
        free(src_buf);
        free(dest_buf);
        
        unlink(path);
        unlink(archive_path);
    }
    return 0;
}



int main(int argc, char ** argv)
{
    int nworkers, nloop, i;
    // pthread_t thread_id;
    pthread_t *tcbs;

    if (argc != 4) {
        err("Invalid args\n");
        exit(1);
    }

    srand(time(NULL));

    dir = argv[1];
    nworkers = atoi(argv[2]);
    nloop = atoi(argv[3]);

    tcbs = calloc(nworkers, sizeof(*tcbs));
    for (i = 0; i < nworkers; i++) {
        pthread_create(&tcbs[i], NULL, thread_function,
                (void *)(intptr_t)nloop);
    }

    for (i=0; i < nworkers; i++) {
        pthread_join(tcbs[i], NULL);
    }
    
    printf("END\n");
    return 0;
}