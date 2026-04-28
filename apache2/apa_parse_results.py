import re
import pandas as pd
import sys

from apa_conf import NOPAM_RESULTS, PAM_RESULTS

FILES = {
    "no-pam": NOPAM_RESULTS, # "summary_no-pam.txt",
    "pam": PAM_RESULTS,  # "summary_pam.txt",
}

BLOCK_SEPARATOR = re.compile(r"This is ApacheBench")


def parse_block(block):
    doc_path = re.search(r"Document Path:\s+(\S+)", block)
    concurrency = re.search(r"Concurrency Level:\s+(\d+)", block)
    rps = re.search(r"Requests per second:\s+([\d.]+)", block)
    waiting = re.search(r"Waiting:\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", block)

    if not all([doc_path, concurrency, rps, waiting]):
        return None

    path = doc_path.group(1).lstrip("/").replace(".html", "")

    return {
        "document_path": path,
        "concurrency_level": int(concurrency.group(1)),
        "requests_per_second": float(rps.group(1)),
        "waiting_median_ms": float(waiting.group(4)),
    }


def parse_file(filepath, category):
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    raw_blocks = BLOCK_SEPARATOR.split(content)

    seen = set()
    records = []
    for raw in raw_blocks:
        record = parse_block(raw)
        if record is None:
            continue
        key = (record["document_path"], record["concurrency_level"])
        if key in seen:
            continue
        seen.add(key)
        record["category"] = category
        records.append(record)

    return records


def build_dataframe():
    all_records = []
    for category, filepath in FILES.items():
        all_records.extend(parse_file(filepath, category))

    df = pd.DataFrame(all_records, columns=[
        "category",
        "document_path",
        "concurrency_level",
        "requests_per_second",
        "waiting_median_ms",
    ])

    df = df.sort_values(["category", "document_path", "concurrency_level"]).reset_index(drop=True)
    return df


### Создает таблиу с индексом concurrency_level, столбцами в виде category/document_path и знач. метрики в ячейках
# ---------- #
def pivot_metric(df, metric):
    pivot = df.pivot_table(
        index="concurrency_level",
        columns=["category", "document_path"],
        values=metric,
    )
    pivot.columns = [f"{cat}/{path}" for cat, path in pivot.columns]
    pivot.index.name = "concurrency_level"
    return pivot


def build_tables_separately_for_each_metric():
    df = build_dataframe()
    rps = pivot_metric(df, "requests_per_second")
    waiting = pivot_metric(df, "waiting_median_ms")
    return rps, waiting
# ---------- #


### Создает таблицу для конкретного пути (lev0, lev2, lev2catA) с индексом concurrency_level и столбцами category (no-pam, pam) и знач. метрики в ячейках
# ******** #
def table_for_path(df, path):
    subset = (
        df[df["document_path"] == path]
        .set_index("concurrency_level")[["requests_per_second", "waiting_median_ms"]]
    )
    subset.index.name = "concurrency_level"
    return subset

def build_tables_separately_for_each_lvl_or_category():
    df = build_dataframe()
    lev0 = table_for_path(df, "lev0")
    lev2 = table_for_path(df, "lev2")
    lev2catA = table_for_path(df, "lev2catA")
    return lev0, lev2, lev2catA
# ******** #


if __name__ == "__main__":
    pd.set_option("display.width", 120)
    pd.set_option("display.float_format", "{:.2f}".format)
    
    if len(sys.argv) != 2:
        print("Usage: python parse_results.py [metric|lvl_or_category]")
        sys.exit(1)

    if sys.argv[1] == "metric":
        rps, waiting = build_tables_separately_for_each_metric()

        print("=== Requests per second [#/sec] ===")
        print(rps.to_string())
        print()
        print("=== Waiting median [ms] ===")
        print(waiting.to_string())
        print()
    elif sys.argv[1] == "lvl_or_category":
        lev0, lev2, lev2catA = build_tables_separately_for_each_lvl_or_category()

        for name, table in [("lev0", lev0), ("lev2", lev2), ("lev2catA", lev2catA)]:
            print(f"=== {name} ===")
            print(table.to_string())
            print()
    else:
        print("Usage: python parse_results.py [metric|lvl_or_category]")
