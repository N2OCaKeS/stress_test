#include <stdlib.h>
#include <stdio.h>


int main(int argc, char* argv[]) {
    for (;;) {
        if (malloc(1024) == NULL) {
            printf("Could not allocate memory\n");
            return 1;
        }
    }
}

// gcc -o fill fill.c