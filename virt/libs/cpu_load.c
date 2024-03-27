#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <pthread.h>            // -lpthread
#include <unistd.h>

/* #define CPUID_EAX            0x80000002 */
#define CPUID_EAX               0x29a
#define CPUID_ECX               0

#define TEST_EXEC_SECS          30      // in seconds
#define LOOPS_APPROX_RATE       1000000

#define RESULTS_FILE            "result.txt"

// This test produces a CPU load with simple operations

static inline void cpuid(unsigned int _eax, unsigned int _ecx)
{
        unsigned int regs[4] = {_eax, 0, _ecx, 0};

        asm __volatile__(
                "cpuid"
                : "=a" (regs[0]), "=b" (regs[1]), "=c" (regs[2]), "=d" (regs[3])
                :  "0" (regs[0]),  "1" (regs[1]),  "2" (regs[2]),  "3" (regs[3])
                : "memory");
}

double cpuid_rate_loops(int loops_num)
{
        int i;
        clock_t start_time, end_time;
        double spent_time, rate;

        start_time = clock();

        for (i = 0; i < loops_num; i++)
                cpuid((unsigned int)CPUID_EAX, (unsigned int)CPUID_ECX);

        end_time = clock();
        spent_time = (double)(end_time - start_time) / CLOCKS_PER_SEC;

        rate = (double)loops_num / spent_time;

        return rate;
}

void *steal_time_function(void *vargp)
{
    char buff[128];
    FILE *fs;
    FILE *file;
    time_t current_time;
    char time_buffer[9];  
    struct tm *tm_info;

    file = fopen(RESULTS_FILE, "a");

    if (file == NULL) {
        printf("Failed to open the file\n");
        return NULL;
    }

    while(1) {
        time(&current_time);
        tm_info = localtime(&current_time);

        strftime(time_buffer, sizeof(time_buffer), "%H:%M:%S", tm_info);

        fs = popen("iostat -c 1 2 | awk 'NR==4{print $5}'", "r");

        if (fgets(buff, 127, fs) != NULL) {
            fprintf(file, "Steal Time: %s - %s", time_buffer, buff);
        }

        pclose(fs);
        sleep(1);
    }

    fclose(file);
    return NULL;
}


int main(int argc, char* argv[])
{
    /* Add a thread to keep track of the steal time*/
    pthread_t thread_id;
    pthread_create(&thread_id, NULL, steal_time_function, NULL);

    double approx_rate, rate;
    int loops;
    FILE *file;

    file = fopen(RESULTS_FILE, "a");

    /* First we detect approximate CPUIDs rate. */
    approx_rate = cpuid_rate_loops(LOOPS_APPROX_RATE);

    printf("Approximate CPUIDs rate is %.2f \n", approx_rate);

    /*
     * How many loops there should be in order to run the test for
     * TEST_EXEC_SECS seconds?
     */
    loops = (int)(approx_rate * TEST_EXEC_SECS);

    /* Get the precise instructions rate. */
    rate = cpuid_rate_loops(loops);

    printf("CPUID instructions rate: %f instructions/second\n", rate);
    fprintf(file, "CPUID instructions rate: %.2f instructions/second\n", rate);
    fclose(file);

    sleep(1);
    pthread_cancel(thread_id);

    return 0;
}

