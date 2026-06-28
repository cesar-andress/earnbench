FROM --platform=linux/x86_64 sweb.env.py.x86_64.042cc8c7538a70394bfed6:latest

COPY ./setup_repo.sh /root/
RUN sed -i -e 's/\r$//' /root/setup_repo.sh
RUN /bin/bash /root/setup_repo.sh

WORKDIR /testbed/
