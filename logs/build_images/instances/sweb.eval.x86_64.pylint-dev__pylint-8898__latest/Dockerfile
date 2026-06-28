FROM --platform=linux/x86_64 sweb.env.py.x86_64.ed3b3b6b345a31fa74f72e:latest

COPY ./setup_repo.sh /root/
RUN sed -i -e 's/\r$//' /root/setup_repo.sh
RUN /bin/bash /root/setup_repo.sh

WORKDIR /testbed/
