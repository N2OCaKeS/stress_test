ARG BASE_IMAGE=ubuntu:latest
FROM ${BASE_IMAGE}

RUN apt-get update && \
    apt-get install -y stress python3 && \
    apt-get clean

WORKDIR /opt
COPY scripts_for_containers/summarize.py .
RUN python3 summarize.py