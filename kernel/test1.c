#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>


int main(int argc, char* argv[]) {
    char* filename="xfs.mnt/test_file";
    int fd = open(filename, O_RDONLY);
    
    if (fd == -1) {
        printf("Could not open file %s\n", filename);
        exit(EXIT_FAILURE);
    }
    
    char* map = mmap(NULL, 4096 * 100, PROT_READ, MAP_PRIVATE, fd, 0);
    if (map == MAP_FAILED) {
        close(fd);
        printf("Could not map file content %s\n", filename);
        exit(EXIT_FAILURE);
    }

    printf("Starting test...\n");

    /* Test body */

    for(;;) {
        for (int i = 0; i < 100; i++) {
            char c = map[i*4096];
            if (c != 1) {
                printf("Got invalid value on page %i = %i\n", i, c);
            }
        }
    }

    munmap(map, 4096 * 100);
    close(fd);
    return EXIT_SUCCESS;
}

// gcc -o test1 test1.c