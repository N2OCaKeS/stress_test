#include <stdio.h>
#include <stdlib.h>

#define FUNC_HERE(FUNC_NAME, RET_VAL) int FUNC_NAME() {\
return RET_VAL;\
}
#define CHECK_FUNC(FUNC_NAME, RET_VAL) { \
int check_val = FUNC_NAME();\
if (check_val != RET_VAL) {\
printf("Erroneous value from " #FUNC_NAME ", expected %d, received %d\n",RET_VAL, check_val);\
exit(1);\
}\
}

FUNC_HERE(func0, 0)
FUNC_HERE(func1, 1)
FUNC_HERE(func2, 2)
FUNC_HERE(func3, 3)
FUNC_HERE(func4, 4)
FUNC_HERE(func5, 5)
FUNC_HERE(func6, 6)
FUNC_HERE(func7 , 7)
FUNC_HERE(func8, 8)
FUNC_HERE(func9, 9)
FUNC_HERE(func10, 10)
FUNC_HERE(func11, 11)
FUNC_HERE(func12, 12)
FUNC_HERE(func13, 13)
FUNC_HERE(func14, 14)
FUNC_HERE(func15, 15)
FUNC_HERE(func16, 16)
FUNC_HERE(func17, 17)
FUNC_HERE(func18, 18)
FUNC_HERE(func19, 19)
FUNC_HERE(func20, 20)
FUNC_HERE(func21, 21)
FUNC_HERE(func22, 22)
FUNC_HERE(func23, 23)
FUNC_HERE(func24, 24)
FUNC_HERE(func25, 25)
FUNC_HERE(func26, 26)
FUNC_HERE(func27, 27)
FUNC_HERE(func28, 28)
FUNC_HERE(func29, 29)

int main(int argc, char* argv[]) {
    
    printf("Program start\n");
    
    for(;;) {
        // Check functions
        CHECK_FUNC(func0, 0)
        CHECK_FUNC(func1, 1)
        CHECK_FUNC(func2, 2)
        CHECK_FUNC(func3, 3)
        CHECK_FUNC(func4, 4)
        CHECK_FUNC(func5, 5)
        CHECK_FUNC(func6, 6)
        CHECK_FUNC(func7, 7)
        CHECK_FUNC(func8, 8)
        CHECK_FUNC(func9, 9)
        CHECK_FUNC(func10, 10)
        CHECK_FUNC(func11, 11)
        CHECK_FUNC(func12, 12)
        CHECK_FUNC(func13, 13)
        CHECK_FUNC(func14, 14)
        CHECK_FUNC(func15, 15)
        CHECK_FUNC(func16, 16)
        CHECK_FUNC(func17, 17)
        CHECK_FUNC(func18, 18)

        CHECK_FUNC(func19, 19)
        CHECK_FUNC(func20, 20)
        CHECK_FUNC(func21, 21)
        CHECK_FUNC(func22, 22)
        CHECK_FUNC(func23, 23)
        CHECK_FUNC(func24, 24)
        CHECK_FUNC(func25, 25)
        CHECK_FUNC(func26, 26)
        CHECK_FUNC(func27, 27)
        CHECK_FUNC(func28, 28)
        CHECK_FUNC(func29, 29)
    }
    printf("Program end\n");
}


