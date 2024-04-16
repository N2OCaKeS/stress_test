#include <stdio.h>
#include <stdlib.h>
#include <pthread.h>
#include <time.h>

#define VLONG       unsigned long long 
#define NTHREADS    4
#define ICOUNT      5000


typedef struct {
    VLONG *dates;
    int count;
    } dates_args;


void *dates_processing(void *args) {
    dates_args *data = (dates_args*) args;
    VLONG *dates = data->dates;
    int count = data->count;
    struct timespec start, end;

    gettimeofday(&start, NULL);

    for (int i=0; i<count; i++) {
        for (int j=0; j<count; j++) {
            dates[i] = (dates[i] << (rand() % 32)) + j;
            dates[i] = (dates[i] >> 4) - j;
            dates[i] += (10/2) * 15 - 9 + j;
        }
    }

    gettimeofday(&end, NULL);
    double time_taken;
    time_taken = (end.tv_sec - start.tv_sec) * 1e6;
    time_taken = (time_taken + (end.tv_nsec - start.tv_nsec)) * 1e-6;
    printf("Time spent %.4f sec\n", time_taken);
}



int main(int argc, char *argv[]) {
    pthread_t threads[NTHREADS];
    //const int count = 500000;
    VLONG operations;
    VLONG *dates;
    dates_args args;
    int i, j;

    dates = malloc(ICOUNT * sizeof(VLONG));
    if (dates == NULL) {
        printf("Memory allocated error\n");
        return 1;
    }

    for (i=0; i<ICOUNT; i++) {
        dates[i] = rand();
    }

    args.dates = dates;
    args.count = ICOUNT;

    for (i=0; i<NTHREADS; i++) {
        pthread_create(&threads[i], NULL, dates_processing, &args);
    }
    
    operations = 13 * ICOUNT * ICOUNT;
    printf("Requested %d computational operations for %d threads\n", operations, NTHREADS);
    //for (i=0; i<ICOUNT; i++) {
    //    printf("%lu ", dates[i]);
    //}

    pthread_exit(NULL);  
    free(dates);

    return 0;
}

