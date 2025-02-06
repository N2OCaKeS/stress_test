#dockerfile.load
ARG BASE_IMAGE=ubuntu:latest
FROM ${BASE_IMAGE}

RUN apt-get update && \
    apt-get install -y stress python3 && \
    apt-get clean

WORKDIR /opt
COPY static/summarize.py .
CMD ["python3", summarize.pys]