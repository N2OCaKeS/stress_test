#include <stdio.h>
#include <time.h>

/* #define CPUID_EAX            0x80000002 */
#define CPUID_EAX               0x29a
#define CPUID_ECX               0

#define TEST_EXEC_SECS          30      // in seconds
#define LOOPS_APPROX_RATE       1000000

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

int main(int argc, char* argv[])
{
        double approx_rate, rate;
        int loops;
        FILE *file;

        file = fopen("result.txt", "w");

        /* First we detect approximate CPUIDs rate. */
        approx_rate = cpuid_rate_loops(LOOPS_APPROX_RATE);

        printf("Approximate CPUIDs rate is %.2f", approx_rate);

        /*
         * How many loops there should be in order to run the test for
         * TEST_EXEC_SECS seconds?
         */
        loops = (int)(approx_rate * TEST_EXEC_SECS);

        /* Get the precise instructions rate. */
        rate = cpuid_rate_loops(loops);

        printf( "CPUID instructions rate: %f instructions/second\n", rate);
        fprintf(file, "%.2f", rate);
        fclose(file);

        return 0;
}

