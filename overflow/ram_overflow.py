import array
import psutil
import sys

def fill_memory():
    counter = 0
    try:
        memory_blocks = []
        while True:
            memory_blocks.append(array.array('B', [0] * (1024 * 1024)))  # Создание массива размером 1 МБ
            print(psutil.virtual_memory().percent)
            if psutil.virtual_memory().percent >= 100:
                counter += 1
                if counter == 1000:
                    sys.exit(5)

    except MemoryError as e:
        print(f"Memory overflow or encountered an error: {e}")

if __name__ == "__main__":
    fill_memory()

