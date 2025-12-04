#include <assert.h>
#include <unistd.h>
#include <fcntl.h>
#include <time.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <pthread.h>

#define err(...) fprintf(stderr, __VA_ARGS__)

char *dir;

void *thread_function(void *arg)
{
    int nloop, i, fd, rnd, j;
    char rndstr[16];
    char path[255];

    nloop = (int)(intptr_t)arg;


    for (i = 0; i < nloop; i++) {
        memset(rndstr, 0, sizeof(rndstr));
        for (j = 0; j < 15; j++)
            rndstr[j] = 'a' + rand() % 26;
        snprintf(path, 254, "%s/%s", dir, rndstr);
        fd = open(path, O_CREAT | O_RDWR | O_SYNC);
        assert(fd > 0);
        rnd = rand();
        //write(fd, &rnd, 1);
        close(fd);
        unlink(path);
    }
    return 0;
}

int main(int argc, char ** argv)
{
    int nworkers, nloop, i;
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