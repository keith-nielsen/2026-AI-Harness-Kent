# strata-netaux: the helper image for Strata's locked-down network (tests/bench/stratanet.sh).
#   sink  = sink.py: the only DNS server Strata sees; logs every query and every TCP connection that follows.
#   relay = socat: carries 127.0.0.1:18080 on the host to Strata's port, the one way in.
FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4
RUN apk add --no-cache iptables socat
COPY sink.py /sink.py
