from fastapi import APIRouter


router = APIRouter(
    prefix="/arm",
    tags=["ARM"],
)


_ARM_CATALOG = {
    "1": {
        "grade": "VM Test WorkStation",
        "cpu": "vCPU (16 cores)",
        "ram": "128Gb",
        "storage": "100Gb",
    },
    "2": {
        "grade": "VM Test WorkStation",
        "cpu": "vCPU (16 cores)",
        "ram": "128Gb",
        "storage": "100Gb",
    },
    "3": {
        "grade": "LowServer",
        "cpu": "Intel(R) Xeon(R) Silver 4110 CPU @ 2.10GHz",
        "ram": "128Gb",
        "storage": "nvme0n1 3.2Tb / SAS SSD 3.8Tb",
    },
    "4": {
        "grade": "MiddleServer",
        "cpu": "Intel(R) Xeon(R) CPU E5-2697 v3 @ 2.60GHz",
        "ram": "256Gb",
        "storage": "nvme0n1 3.2Tb / SAS SSD 3.8Tb",
    },
    "5": {
        "grade": "HighServer",
        "cpu": "Intel(R) Xeon(R) Gold 5320 CPU @ 2.20GHz",
        "ram": "1024Gb",
        "storage": "nvme0n1 3.2Tb",
    },
    "6": {
        "grade": "VM TestStation",
        "cpu": "vCPU (16 cores)",
        "ram": "128Gb",
        "storage": "100Gb",
    },
    "7": {
        "grade": "VM TestStation",
        "cpu": "vCPU (16 cores)",
        "ram": "128Gb",
        "storage": "100Gb",
    },
    "8": {
        "grade": "VM TestStation",
        "cpu": "vCPU (16 cores)",
        "ram": "128Gb",
        "storage": "100Gb",
    },
    "9": {
        "grade": "VM TestStation",
        "cpu": "vCPU (16 cores)",
        "ram": "128Gb",
        "storage": "100Gb",
    },
    "10": {
        "grade": "LowServer",
        "cpu": "Intel(R) Xeon(R) Silver 4210 CPU @ 2.2GHz",
        "ram": "128Gb",
        "storage": "nvme0n1 3.2Tb / SAS SSD 3.8Tb",
    },
    "11": {
        "grade": "LowServer",
        "cpu": "Intel(R) Xeon(R) Silver 4210 CPU @ 2.2GHz",
        "ram": "128Gb",
        "storage": "nvme0n1 3.2Tb / SAS SSD 3.8Tb",
    },
    "12": {
        "grade": "LowServer",
        "cpu": "Intel(R) Xeon(R) Silver 4210 CPU @ 2.2GHz",
        "ram": "128Gb",
        "storage": "nvme0n1 3.2Tb / SAS SSD 3.8Tb",
    },
    "13": {
        "grade": "LowServer",
        "cpu": "Intel(R) Xeon(R) Silver 4210 CPU @ 2.2GHz",
        "ram": "128Gb",
        "storage": "nvme0n1 3.2Tb / SAS SSD 3.8Tb",
    },
}


@router.get(
    "/",
    summary="Публичный список ARM конфигураций",
)
def list_arm_catalog():
    """
    Возвращает полный каталог ARM в виде JSON-объекта.

    Endpoint публичный (без авторизации) — можно дернуть из внешней библиотеки,
    чтобы получить отображение ``{arm_id: {grade, cpu, ram, storage}}``.
    """
    return _ARM_CATALOG
