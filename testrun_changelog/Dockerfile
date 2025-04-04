FROM python:3.12

RUN mkdir /testrun_create_changelog

WORKDIR /testrun_create_changelog

COPY req.txt .

RUN pip install -r req.txt

COPY . .