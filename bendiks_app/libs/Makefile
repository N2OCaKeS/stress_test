CC=gcc
CFLAGS=-fPIC -Wall -Wextra -O2
LDFLAGS=-shared -lcurl
RM=rm -f
TARGET_LIB=datlib.so

SRCS=datlib.c
OBJS=$(SRCS:.c=.o)

.PHONY: all
all: ${TARGET_LIB}

$(TARGET_LIB): $(OBJS)
	$(CC) $(LDFLAGS) -o $@ $^

$(OBJS): $(SRCS)
	$(CC) $(CFLAGS) -c $< -o $@

.PHONY: clean
clean:
	-$(RM) ${TARGET_LIB} $(OBJS)
