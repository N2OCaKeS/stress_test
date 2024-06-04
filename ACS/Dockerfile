FROM python:3.12

RUN mkdir /fastapi_app

WORKDIR /fastapi_app

COPY requirements.txt .

RUN pip install -r requirements.txt

COPY . .

COPY /home/u/ilo.json /fastapi_app/src/add_tunning

RUN chmod a+x docker/*.sh