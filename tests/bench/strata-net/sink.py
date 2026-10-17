"""sink.py — log what a locked-down container tries to reach, and let none of it through.

Runs on an --internal Docker network (no route out) as the only DNS server the watched container knows:
  DNS (udp/53)  every query is logged; A queries are answered with this sink's own IP, everything else gets an
                empty NOERROR answer, so a lookup "works" and the connection that follows comes here.
  TCP           every port is redirected (iptables in this container's own namespace) to one listener, which logs
                the original port, the TLS SNI or the HTTP request line + Host, then closes. Nothing is answered.
Log: one JSON object per line on stdout and in SINK_LOG.
"""
import json
import os
import socket
import struct
import subprocess
import threading
import time

SINK_IP = os.environ["SINK_IP"]
LOG = os.environ.get("SINK_LOG", "/log/sink.jsonl")
TCP_PORT = 15000
SO_ORIGINAL_DST = 80
lock = threading.Lock()


def log(**rec):
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **rec}
    line = json.dumps(rec, ensure_ascii=False)
    with lock:
        print(line, flush=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def qname(data, off=12):
    labels = []
    while data[off]:
        n = data[off]
        labels.append(data[off + 1:off + 1 + n].decode("ascii", "replace"))
        off += n + 1
    return ".".join(labels), off + 1


def dns():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 53))
    while True:
        data, src = s.recvfrom(4096)
        try:
            name, end = qname(data)
            qtype = struct.unpack("!H", data[end:end + 2])[0]
            log(kind="dns", src=src[0], name=name, qtype={1: "A", 28: "AAAA"}.get(qtype, qtype))
            question = data[12:end + 4]
            answer = b""
            if qtype == 1:
                answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 5, 4) + socket.inet_aton(SINK_IP)
            hdr = data[:2] + struct.pack("!HHHHH", 0x8180, 1, 1 if answer else 0, 0, 0)
            s.sendto(hdr + question + answer, src)
        except Exception as e:                          # a malformed packet: log it, keep serving
            log(kind="dns-error", src=src[0], error=repr(e), bytes=len(data))


def sni(b):
    """The server name from a TLS ClientHello, or None."""
    try:
        if b[0] != 0x16 or b[5] != 0x01:
            return None
        p = 43
        p += 1 + b[p]                                   # session id
        p += 2 + struct.unpack("!H", b[p:p + 2])[0]     # cipher suites
        p += 1 + b[p]                                   # compression
        end = p + 2 + struct.unpack("!H", b[p:p + 2])[0]
        p += 2
        while p + 4 <= end:
            et, el = struct.unpack("!HH", b[p:p + 4])
            if et == 0:
                n = struct.unpack("!H", b[p + 7:p + 9])[0]
                return b[p + 9:p + 9 + n].decode("ascii", "replace")
            p += 4 + el
    except (IndexError, struct.error):
        pass
    return None


def tcp_conn(c, src):
    try:
        od = c.getsockopt(socket.SOL_IP, SO_ORIGINAL_DST, 16)
        port = struct.unpack("!H", od[2:4])[0]
        c.settimeout(3)
        try:
            b = c.recv(4096)
        except socket.timeout:
            b = b""
        rec = {"kind": "tcp", "src": src[0], "port": port, "bytes": len(b)}
        name = sni(b)
        if name:
            rec["sni"] = name
        elif b[:16].split(b" ")[0] in (b"GET", b"POST", b"HEAD", b"PUT", b"DELETE", b"OPTIONS", b"CONNECT", b"PATCH"):
            lines = b.split(b"\r\n")
            rec["http"] = lines[0].decode("latin-1")[:300]
            rec["host"] = next((l[5:].strip().decode("latin-1") for l in lines if l.lower().startswith(b"host:")), None)
        elif b:
            rec["head"] = b[:64].hex()
        log(**rec)
    except Exception as e:
        log(kind="tcp-error", src=src[0], error=repr(e))
    finally:
        c.close()


def tcp():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", TCP_PORT))
    s.listen(64)
    while True:
        c, src = s.accept()
        threading.Thread(target=tcp_conn, args=(c, src), daemon=True).start()


if __name__ == "__main__":
    subprocess.run(["iptables", "-t", "nat", "-A", "PREROUTING", "-p", "tcp", "-d", SINK_IP,
                    "-j", "REDIRECT", "--to-ports", str(TCP_PORT)], check=True)
    log(kind="start", sink_ip=SINK_IP)
    threading.Thread(target=dns, daemon=True).start()
    tcp()
