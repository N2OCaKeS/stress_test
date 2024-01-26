CC=gcc
CFLAGS=-Wall -Wextra -O2
LDFLAGS=-lcurl -pthread
RM=rm -f
TARGET=load_parsec

SRCS=load_parsec.c
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

