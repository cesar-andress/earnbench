FROM --platform=linux/x86_64 sweb.env.py.x86_64.acfa5e4e0617f8ce3b06fd:latest

COPY ./setup_repo.sh /root/
RUN sed -i -e 's/\r$//' /root/setup_repo.sh
RUN /bin/bash /root/setup_repo.sh

WORKDIR /testbed/
