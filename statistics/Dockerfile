FROM python:3.12

RUN mkdir /fastapi_app_stat

WORKDIR /fastapi_app_stat

COPY req.txt .

RUN pip install -r req.txt

COPY . .

# CMD gunicorn main_api:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind=0.0.0.0:8000