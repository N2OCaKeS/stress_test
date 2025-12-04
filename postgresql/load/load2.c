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



void *thread_archive(int archloop) {
    unsigned long src_len, dest_len;
    Bytef *dest_buf;
    int arch_result, i;

    random_text(text_buffer_var, sizeof(text_buffer_var)); // Наполняем текстом
    src_len = sizeof(text_buffer_var);
    dest_len = compressBound(src_len); // Вычисляем максимальный размер выходного буфера
    dest_buf = malloc(dest_len);
    if (!dest_buf) {
        perror("Ошибка выделения памяти");
        exit(EXIT_FAILURE);
    }

    for (i=0; i<archloop; i++ ) {  
        arch_result = compress(dest_buf, &dest_len, (Bytef*)text_buffer_var, src_len); // Архивируем переменную
        if (arch_result != Z_OK) {
            fprintf(stderr, "Ошибка сжатия: %d\\n", arch_result);
            //free(dest_buf);
            continue;
        } 
    }

    free(dest_buf);
    return NULL;
}



void *thread_function(int nloop)
{
    int i, fd, j;
    char rndstr[16];
    char path[255];
    Bytef *src_buf;
    
    char **n_path;
    n_path = malloc(nloop * sizeof(char*));

    // нужно новое зерно для генерации новых файлов.
    srand(time(NULL));
    
    for (i=0; i<nloop; i++) {
        memset(rndstr, 0, sizeof(rndstr));
        for (j=0; j<15; j++)
            rndstr[j] = 'a' + rand() % 26;

        snprintf(path, 254, "%s/%s", dir, rndstr);
        
        // запоминаем в n_path созданный файл
        n_path[i] = strdup(path);

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

        //write(fd, src_buf, file_size);
        close(fd);
    }

    for (i=0; i<nloop; i++) {
        unlink(n_path[i]);
        free(n_path[i]);
    }

    free(n_path);
    return 0;
}



int main(int argc, char ** argv)
{
    int nworkers, nloop, i, archloop, wpid, status;

    if (argc != 5) {
        fprintf(stderr, "Usage: %s directory num_workers loop_count archive_loop_count\\n", argv[0]);
        exit(EXIT_FAILURE);
    }

    srand(time(NULL));

    dir = argv[1];
    nworkers = atoi(argv[2]);
    nloop = atoi(argv[3]);
    archloop = atoi(argv[4]);

    int pid;
    for (i=0; i<nworkers; i++) {
        switch(pid=fork()) {
        case -1:
            perror("fuuuuuck");
            exit(1);
        case 0:
            // дочерний процесс
            thread_function(nloop);
            thread_archive(archloop);
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
    return 0;
}
