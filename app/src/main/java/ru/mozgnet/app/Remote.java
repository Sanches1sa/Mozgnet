package ru.mozgnet.app;

import android.content.SharedPreferences;
import android.net.Uri;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.cert.X509Certificate;

import javax.net.ssl.HostnameVerifier;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

/**
 * Облако на компьютере/сервере, к которому подключается ТЕЛЕФОН (вместо того
 * чтобы раздавать облако с телефона). Адрес, пароль панели, мозг-токен и
 * отпечаток сертификата (для https с самоподписанным сертификатом) хранятся
 * в настройках приложения. Самоподписанный сертификат закрепляется при
 * подключении (SHA-256), дальше принимается только он.
 */
final class Remote {
    private Remote() {
    }

    static volatile String host = "";

    static String norm(String u) {
        u = u == null ? "" : u.trim();
        if (u.isEmpty()) return "";
        if (!u.matches("(?i)^https?://.*")) u = "http://" + u;
        while (u.endsWith("/")) u = u.substring(0, u.length() - 1);
        if (!u.matches("(?i)^https?://[^/]+:\\d+$") && !u.matches("(?i)^https?://[^/]+/.+")) {
            Uri p = Uri.parse(u);
            if (p.getPort() < 0 && p.getScheme() != null && p.getScheme().equalsIgnoreCase("http")) u = u + ":8077";
        }
        return u;
    }

    /** 127.x.x.x, localhost, 0.0.0.0, ::1 — на телефоне это он сам: облако
     *  компьютера по такому адресу не откроется. */
    static boolean isLoopback(String s) {
        if (s == null) return false;
        String h = s.trim().toLowerCase();
        h = h.replaceFirst("^[a-z]+://", "");
        h = h.replaceFirst("[/?#].*$", "");
        if (h.startsWith("[")) h = h.substring(1, Math.max(1, h.indexOf(']') < 0 ? h.length() : h.indexOf(']')));
        else h = h.replaceFirst(":\\d+$", "");
        return h.equals("localhost") || h.equals("0.0.0.0") || h.equals("::1") || h.matches("^127(\\.\\d{1,3}){3}$");
    }

    static final String LOOPBACK_MSG = "127.0.0.1 / localhost — это сам телефон, облако по такому адресу не загрузится. Укажи IP компьютера в Wi-Fi, например 192.168.1.5";

    static void load(SharedPreferences p) {
        String u = p.getString("remote_url", "");
        host = u.isEmpty() ? "" : String.valueOf(Uri.parse(u).getHost());
    }

    static boolean isHost(Uri u) {
        return !host.isEmpty() && host.equalsIgnoreCase(u.getHost());
    }

    static String sha256(byte[] der) {
        try {
            byte[] d = MessageDigest.getInstance("SHA-256").digest(der);
            StringBuilder sb = new StringBuilder();
            for (byte b : d) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    /** Соединение, которое для https принимает только сертификат с отпечатком fp
     *  (пустой fp — запомнить первый увиденный в seen[0]). */
    static HttpURLConnection open(String url, String fp, String[] seen) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        if (c instanceof HttpsURLConnection) {
            TrustManager tm = new X509TrustManager() {
                public void checkClientTrusted(X509Certificate[] ch, String a) {
                }

                public void checkServerTrusted(X509Certificate[] ch, String a) throws java.security.cert.CertificateException {
                    String got = sha256(ch[0].getEncoded());
                    if (seen != null) seen[0] = got;
                    if (fp != null && !fp.isEmpty() && !fp.equalsIgnoreCase(got)) {
                        throw new java.security.cert.CertificateException("сертификат облака изменился");
                    }
                }

                public X509Certificate[] getAcceptedIssuers() {
                    return new X509Certificate[0];
                }
            };
            SSLContext sc = SSLContext.getInstance("TLS");
            sc.init(null, new TrustManager[]{tm}, null);
            HttpsURLConnection h = (HttpsURLConnection) c;
            h.setSSLSocketFactory(sc.getSocketFactory());
            HostnameVerifier ok = (n, s) -> true;      // проверяем отпечаток, а не имя
            h.setHostnameVerifier(ok);
        }
        return c;
    }

    static String request(String method, String url, String body, String fp, String[] seen,
                          String[] cookieOut, int ms) throws Exception {
        HttpURLConnection c = open(url, fp, seen);
        c.setConnectTimeout(ms);
        c.setReadTimeout(ms * 2);
        c.setRequestMethod(method);
        c.setRequestProperty("Accept", "application/json");
        if (body != null) {
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            try (OutputStream o = c.getOutputStream()) {
                o.write(body.getBytes(StandardCharsets.UTF_8));
            }
        }
        int code = c.getResponseCode();
        if (cookieOut != null) {
            String sc = c.getHeaderField("Set-Cookie");
            if (sc != null) cookieOut[0] = sc.split(";")[0];
        }
        java.io.InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
        String txt = in == null ? "" : new String(CloudService.readAll(in), StandardCharsets.UTF_8);
        c.disconnect();
        if (code >= 400) throw new Exception("HTTP " + code + " " + txt.substring(0, Math.min(160, txt.length())));
        return txt;
    }

    static JSONObject json(String s) {
        try {
            return new JSONObject(s);
        } catch (Exception e) {
            return new JSONObject();
        }
    }
}
