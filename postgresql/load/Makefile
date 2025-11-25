CC=gcc
CFLAGS=-Wall -Wextra -O2
LDFLAGS=-lcurl -pthread -lz
RM=rm -f
TARGET=load

SRCS=load.c
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