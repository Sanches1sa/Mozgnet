package ru.mozgnet.app;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.InterfaceAddress;
import java.net.NetworkInterface;
import java.net.Socket;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Скан локальной сети, как в лаунчере: платы neuro_12 (порт 7000, /api/alive)
 * и облака «Star Gate» (порт 8077, /api/health) во всей подсети телефона.
 */
public class Scanner {
    public interface Progress {
        void step(int done, int total, JSONObject found);
    }

    static final int[] PORTS = {7000, 8077, 8078, 8080};

    /** IPv4 телефона в локальной сети (Wi-Fi или точка доступа). */
    static String myIp() {
        List<String> all = myIps();
        return all.isEmpty() ? null : all.get(0);
    }

    static List<String> myIps() {
        List<String> out = new ArrayList<>();
        try {
            for (NetworkInterface ni : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!ni.isUp() || ni.isLoopback()) continue;
                String nm = ni.getName();
                if (nm.startsWith("rmnet") || nm.startsWith("ccmni") || nm.startsWith("dummy") || nm.startsWith("tun")) continue;
                for (InterfaceAddress ia : ni.getInterfaceAddresses()) {
                    InetAddress a = ia.getAddress();
                    if (a instanceof Inet4Address && a.isSiteLocalAddress()) {
                        String s = a.getHostAddress();
                        if (nm.startsWith("wlan")) out.add(0, s); else out.add(s);
                    }
                }
            }
        } catch (Exception ignored) {
        }
        return out;
    }

    static boolean portOpen(String ip, int port, int ms) {
        try (Socket s = new Socket()) {
            s.connect(new InetSocketAddress(ip, port), ms);
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    static String http(String method, String url, String body, int ms) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
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
        InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
        String txt = in == null ? "" : new String(CloudService.readAll(in), StandardCharsets.UTF_8);
        c.disconnect();
        if (code >= 400) throw new Exception("HTTP " + code + " " + txt.substring(0, Math.min(200, txt.length())));
        return txt;
    }

    static JSONObject getJson(String url, int ms) {
        try {
            return new JSONObject(http("GET", url, null, ms));
        } catch (Exception e) {
            return null;
        }
    }

    /** Что за хост: плата, облако или просто открытый порт. null — ничего. */
    static JSONObject probe(String ip) {
        JSONObject h = null;
        for (int p : PORTS) {
            if (!portOpen(ip, p, 350)) continue;
            try {
                if (h == null) {
                    h = new JSONObject();
                    h.put("ip", ip);
                    h.put("ports", new JSONArray());
                }
                h.getJSONArray("ports").put(p);
                String base = "http://" + ip + ":" + p;
                JSONObject a = getJson(base + "/api/alive", 2000);
                if (a != null && a.has("ver")) {
                    h.put("board", base + "/");
                    h.put("board_ver", a.optString("ver"));
                    h.put("sketch", a.optBoolean("sketch_alive") ? "скетч жив"
                            : (a.optBoolean("sketch_seen") ? "скетч молчит" : "скетч не отвечает"));
                    continue;
                }
                JSONObject c = getJson(base + "/api/health", 2000);
                if (c != null && "neuro12-cloud".equals(c.optString("service"))) {
                    h.put("cloud", base + "/");
                    h.put("cloud_ver", c.optString("version"));
                    continue;
                }
                if (p == 7000 && getJson(base + "/api/state", 2000) != null) {
                    h.put("board", base + "/");
                    h.put("board_ver", "старая");
                }
            } catch (Exception ignored) {
            }
        }
        return h;
    }

    /** Скан /24 каждой своей сети. Вызывает progress для каждой находки и в конце. */
    static JSONArray scan(Progress progress) {
        List<String> targets = new ArrayList<>();
        List<String> mine = myIps();
        for (String ip : mine) {
            String pre = ip.substring(0, ip.lastIndexOf('.') + 1);
            for (int i = 1; i < 255; i++) {
                String t = pre + i;
                if (!targets.contains(t)) targets.add(t);
            }
        }
        JSONArray found = new JSONArray();
        if (targets.isEmpty()) return found;
        ExecutorService ex = Executors.newFixedThreadPool(48);
        AtomicInteger done = new AtomicInteger();
        int total = targets.size();
        for (String t : targets) {
            ex.submit(() -> {
                JSONObject h = probe(t);
                int d = done.incrementAndGet();
                if (h != null) {
                    try { h.put("me", mine.contains(t)); } catch (Exception ignored) { }
                    synchronized (found) { found.put(h); }
                }
                if (progress != null && (h != null || d % 16 == 0 || d == total)) progress.step(d, total, h);
            });
        }
        ex.shutdown();
        try {
            ex.awaitTermination(3, TimeUnit.MINUTES);
        } catch (InterruptedException ignored) {
        }
        return found;
    }
}
