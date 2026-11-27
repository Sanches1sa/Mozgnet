# tls.py — v3.210: HTTPS ДЛЯ ОБЛАКА (сертификат безопасности)
#
# Облако по-прежнему слушает HTTP (порт 8077 — платы, лаунчер, приложение),
# а рядом поднимает HTTPS (порт 8077+366 = 8443 по умолчанию, NEURO_TLS_PORT).
# Сертификат:
#   1) свой: data/tls/cert.pem + data/tls/key.pem (например Let's Encrypt —
#      см. cloud/launcher/linux/neuro12-ssl.sh) — берётся как есть;
#   2) иначе самоподписанный: создаётся один раз (openssl или cryptography),
#      SAN = localhost, имя компьютера, все его IP; живёт 10 лет и
#      обновляется за 30 дней до конца срока.
# Отпечаток SHA-256 виден в /api/tls — плата/браузер могут «закрепить» его.
import datetime
import hashlib
import os
import socket
import ssl
import subprocess
import threading

import store

_ST = {"on": False, "port": 0, "kind": "", "fp": "", "err": "", "until": ""}


def tls_dir():
    d = os.path.join(store.DATA_DIR, "tls")
    os.makedirs(d, exist_ok=True)
    return d


def paths():
    d = tls_dir()
    return os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")


def local_names():
    """(DNS-имена, IP) этого компьютера для SAN."""
    dns, ips = ["localhost"], ["127.0.0.1"]
    try:
        h = socket.gethostname()
        if h and h not in dns:
            dns.append(h)
        for ai in socket.getaddrinfo(h, None, socket.AF_INET):
            ip = ai[4][0]
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    try:                                    # IP исходящего интерфейса
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        if ip not in ips:
            ips.append(ip)
    except Exception:
        pass
    extra = os.environ.get("NEURO_TLS_NAMES", "")
    for n in [x.strip() for x in extra.split(",") if x.strip()]:
        (ips if n.replace(".", "").isdigit() else dns).append(n)
    return dns, ips


def fingerprint(cert_path=None):
    cert_path = cert_path or paths()[0]
    try:
        with open(cert_path, "r") as f:
            der = ssl.PEM_cert_to_DER_cert(f.read())
        return hashlib.sha256(der).hexdigest()
    except Exception:
        return ""


def _not_after(cert_path):
    try:
        d = ssl._ssl._test_decode_cert(cert_path)
        return datetime.datetime.strptime(d["notAfter"], "%b %d %H:%M:%S %Y %Z")
    except Exception:
        return None


def _gen_openssl(cert, key, dns, ips, days):
    san = ",".join(["DNS:%s" % d for d in dns] + ["IP:%s" % i for i in ips])
    cmd = ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256",
           "-nodes", "-days", str(days), "-keyout", key, "-out", cert,
           "-subj", "/CN=neuro12-cloud/O=neuro12",
           "-addext", "subjectAltName=" + san,
           "-addext", "basicConstraints=critical,CA:FALSE",
           "-addext", "extendedKeyUsage=serverAuth"]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)


def _gen_crypto(cert, key, dns, ips, days):
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "neuro12-cloud"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "neuro12")])
    now = datetime.datetime.utcnow()
    san = [x509.DNSName(d) for d in dns] + \
          [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
         .public_key(k.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now - datetime.timedelta(days=1))
         .not_valid_after(now + datetime.timedelta(days=days))
         .add_extension(x509.SubjectAlternativeName(san), False)
         .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
         .add_extension(x509.ExtendedKeyUsage(
             [ExtendedKeyUsageOID.SERVER_AUTH]), False)
         .sign(k, hashes.SHA256()))
    with open(cert, "wb") as f:
        f.write(c.public_bytes(serialization.Encoding.PEM))
    with open(key, "wb") as f:
        f.write(k.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.TraditionalOpenSSL,
                                serialization.NoEncryption()))


def ensure_cert(days=3650):
    """Вернёт (cert, key, kind): 'custom' | 'self-signed'. Бросает исключение,
    если сертификат не удалось ни взять, ни создать."""
    cert, key = paths()
    custom = os.path.join(tls_dir(), "custom")
    if os.path.exists(os.path.join(custom)):      # маркер «не трогать»
        return cert, key, "custom"
    if os.path.exists(cert) and os.path.exists(key):
        na = _not_after(cert)
        if na is None or na - datetime.datetime.utcnow() > \
                datetime.timedelta(days=30):
            kind = "self-signed" if os.path.exists(
                os.path.join(tls_dir(), "selfsigned")) else "custom"
            return cert, key, kind
    dns, ips = local_names()
    err = None
    for gen in (_gen_openssl, _gen_crypto):
        try:
            gen(cert, key, dns, ips, days)
            break
        except Exception as e:
            err = e
    else:
        raise RuntimeError("не удалось создать сертификат: %s" % err)
    try:
        os.chmod(key, 0o600)
    except Exception:
        pass
    with open(os.path.join(tls_dir(), "selfsigned"), "w") as f:
        f.write("1")
    return cert, key, "self-signed"


def make_context():
    cert, key, kind = ensure_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    return ctx, kind


def reload_context(ctx):
    """Подхватить обновлённый сертификат без перезапуска."""
    cert, key = paths()
    ctx.load_cert_chain(cert, key)
    _ST["fp"] = fingerprint(cert)


def want_port(http_port):
    try:
        v = os.environ.get("NEURO_TLS_PORT")
        if v is not None:
            return int(v)
        s = store.settings().get("tls_port")
        if s is not None:
            return int(s)
    except Exception:
        pass
    return 8443


def enabled():
    if os.environ.get("NEURO_TLS", "") in ("0", "off", "no"):
        return False
    return store.settings().get("tls_on", True) is not False


def start(server_cls, handler, http_port):
    """Поднять HTTPS-слушатель рядом с HTTP. Любая ошибка — не фатальна:
    облако остаётся на HTTP. Возврат: httpd | None."""
    try:
        if not enabled():
            _ST["err"] = "отключён (tls_on=false / NEURO_TLS=0)"
            return None
        port = want_port(http_port)
        if port <= 0:
            return None
        ctx, kind = make_context()
        httpd = None
        for p in (port, port + 1, port + 2, 0):
            try:
                httpd = server_cls(("0.0.0.0", p), handler,
                                   bind_and_activate=False)
                httpd.allow_reuse_address = True
                httpd.server_bind()
                httpd.server_activate()
                break
            except OSError:
                try:
                    httpd.server_close()
                except Exception:
                    pass
                httpd = None
        if httpd is None:
            _ST["err"] = "нет свободного порта HTTPS"
            return None
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True,
                                       do_handshake_on_connect=False)
        httpd.daemon_threads = True
        real = httpd.server_address[1]
        _ST.update(on=True, port=real, kind=kind, err="",
                   fp=fingerprint())
        try:
            with open(os.path.join(store.DATA_DIR, "tls_port.txt"), "w") as f:
                f.write(str(real))
        except Exception:
            pass
        threading.Thread(target=httpd.serve_forever, daemon=True,
                         name="https").start()
        threading.Thread(target=_watch, args=(ctx,), daemon=True,
                         name="tls-watch").start()
        store.log("СТАРТ", "HTTPS открыт на порту %d (%s), SHA-256 %s…" %
                  (real, kind, _ST["fp"][:16]))
        return httpd
    except Exception as e:
        _ST["err"] = str(e)[:200]
        try:
            store.log("СТАРТ", "HTTPS не поднят: %s" % e)
        except Exception:
            pass
        return None


def _watch(ctx):
    """Раз в сутки: сертификат сменился (certbot продлил) — перечитать."""
    last = fingerprint()
    import time
    while True:
        time.sleep(3600)
        try:
            fp = fingerprint()
            if fp and fp != last:
                reload_context(ctx)
                last = fp
                store.log("СТАРТ", "HTTPS: сертификат обновлён")
        except Exception:
            pass


def status():
    cert, _k = paths()
    na = _not_after(cert) if os.path.exists(cert) else None
    d = dict(_ST)
    d["fp"] = _ST["fp"] or fingerprint()
    d["until"] = na.strftime("%Y-%m-%d") if na else ""
    d["names"] = local_names()
    return d
