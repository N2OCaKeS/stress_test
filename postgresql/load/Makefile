CC=gcc
CFLAGS=-Wall -Wextra -O2
LDFLAGS=-lcurl -pthread -lz
RM=rm -f
TARGET=load_simple_with_open

SRCS=load_simple_with_open.c
OBJS=$(SRCS:.c=.o)

.PHONY: all
all: ${TARGET}

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)

$(OBJS): $(SRCS)
	$(CC) $(CFLAGS) -c $< -o $@

.PHONY: clean
clean:
	-$(RM) $(TARGET) $(OBJS)