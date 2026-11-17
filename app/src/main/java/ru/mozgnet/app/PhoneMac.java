package ru.mozgnet.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.Iterator;
import java.util.Locale;
import java.util.concurrent.TimeUnit;

/**
 * MAC САМОГО ТЕЛЕФОНА (облака mozgnet) — для телефона с root (Magisk).
 *
 * Android не даёт обычному приложению ни прочитать, ни сменить MAC. С root
 * mozgnet делает это сам, теми же правилами, что лаунчер и n12_mac.sh:
 *   rotate  — переворот октетов aa:bb:cc:dd:ee:ff -> ff:ee:dd:cc:bb:aa
 *             («оба 62»: нечётный последний октет +1, чтобы первый был unicast);
 *   mirror  — телефон получает перевёрнутый MAC платы (как компьютер в лаунчере);
 *   spin    — переворот каждые N секунд, пока не нажали «стоп»;
 *   restore — родной адрес (первый, что телефон показал mozgnet).
 * Смена: wlan0 down -> address -> up; затем проверка, что драйвер адрес
 * не сбросил. Сменился IP телефона — платам из списка уходит новый адрес
 * облака (иначе они потеряют облако).
 */
public class PhoneMac {
    static final String IF = "wlan0";
    private static volatile Thread spin;
    private static volatile boolean spinStop;
    static volatile String spinInfo = "";

    // ------------------------------------------------------------ root
    static class Out {
        int code = -1;
        String text = "";
    }

    static Out su(String cmd, int timeoutS) {
        Out o = new Out();
        try {
            Process p = new ProcessBuilder("su", "-c", cmd).redirectErrorStream(true).start();
            StringBuilder sb = new StringBuilder();
            try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8))) {
                String ln;
                while ((ln = r.readLine()) != null) sb.append(ln).append('\n');
            }
            if (p.waitFor(timeoutS, TimeUnit.SECONDS)) o.code = p.exitValue();
            else p.destroy();
            o.text = sb.toString().trim();
        } catch (Exception e) {
            o.text = String.valueOf(e.getMessage());
        }
        return o;
    }

    static String prop(String k) {
        try {
            Process p = new ProcessBuilder("getprop", k).start();
            try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
                String s = r.readLine();
                return s == null ? "" : s.trim();
            }
        } catch (Exception e) {
            return "";
        }
    }

    static boolean installed(Context c, String pkg) {
        try {
            c.getPackageManager().getPackageInfo(pkg, 0);
            return true;
        } catch (PackageManager.NameNotFoundException e) {
            return false;
        }
    }

    static boolean hasRoot() {
        Out o = su("id", 15);           // Magisk спросит разрешение в первый раз
        return o.code == 0 && o.text.contains("uid=0");
    }

    // ------------------------------------------------------------ MAC
    static boolean validMac(String m) {
        return m != null && m.toLowerCase(Locale.ROOT).matches("([0-9a-f]{2}:){5}[0-9a-f]{2}");
    }

    /** Переворот октетов по правилу n12_mac.sh («оба 62»). */
    static String flip(String mac) {
        String[] o = mac.toLowerCase(Locale.ROOT).split(":");
        int last = Integer.parseInt(o[5], 16);
        if ((last & 1) == 1) o[5] = String.format(Locale.ROOT, "%02x", (last + 1) & 0xfe);
        StringBuilder sb = new StringBuilder();
        for (int i = 5; i >= 0; i--) sb.append(o[i]).append(i > 0 ? ":" : "");
        return sb.toString();
    }

    static String current() {
        Out o = su("cat /sys/class/net/" + IF + "/address", 10);
        String m = o.text.trim().toLowerCase(Locale.ROOT);
        return validMac(m) ? m : "";
    }

    /** Поставить адрес и проверить, что драйвер его не сбросил. */
    static JSONObject setMac(Context c, String mac) throws Exception {
        JSONObject r = new JSONObject();
        String before = current(), ipBefore = Scanner.myIp();
        r.put("was", before);
        String cmd = "ip link set " + IF + " down && ip link set " + IF + " address " + mac +
                " ; ip link set " + IF + " up";
        Out o = su(cmd, 20);
        Thread.sleep(6000);                       // переподключение к Wi-Fi
        String now = current();
        r.put("now", now);
        r.put("ok", mac.equalsIgnoreCase(now));
        if (!mac.equalsIgnoreCase(now)) {
            r.put("why", now.isEmpty() ? "Wi-Fi не поднялся: " + o.text
                    : "драйвер Wi-Fi вернул адрес " + now + " (на части MediaTek так бывает). " +
                    "Попробуй: настройки этой сети Wi-Fi → Конфиденциальность → «MAC-адрес устройства».");
        }
        // ждём адрес в сети и при смене IP — сообщаем платам новый адрес облака
        String ip = null;
        for (int i = 0; i < 20 && ip == null; i++) {
            ip = Scanner.myIp();
            if (ip == null) Thread.sleep(1000);
        }
        r.put("ip", ip == null ? "" : ip);
        if (ip != null && ipBefore != null && !ip.equals(ipBefore)) r.put("boards", tellBoards(c, ip));
        return r;
    }

    /** Новый IP облака телефона -> всем платам из последнего скана. */
    static JSONArray tellBoards(Context c, String ip) {
        JSONArray done = new JSONArray();
        try {
            SharedPreferences p = c.getSharedPreferences("mozgnet", Context.MODE_PRIVATE);
            JSONObject hosts = new JSONObject(p.getString("p_hosts", "{}"));
            String tok = CloudService.runner(c).callAttr("token").toString();
            JSONObject b = new JSONObject();
            b.put("cloud_url", "http://" + ip + ":" + CloudService.PORT);
            b.put("token", tok);
            for (Iterator<String> it = hosts.keys(); it.hasNext(); ) {
                JSONObject h = hosts.optJSONObject(it.next());
                if (h == null || h.optString("board").isEmpty()) continue;
                String base = h.optString("board").replaceAll("/$", "");
                try {
                    Scanner.http("POST", base + "/api/settings", b.toString(), 4000);
                    done.put(h.optString("ip"));
                } catch (Exception ignored) {
                }
            }
        } catch (Throwable ignored) {
        }
        return done;
    }

    static String orig(Context c) {
        return c.getSharedPreferences("mozgnet", Context.MODE_PRIVATE).getString("phone_orig_mac", "");
    }

    /** Состояние: загрузчик, root, Magisk, MAC, спин. */
    static JSONObject info(Context c) {
        JSONObject r = new JSONObject();
        try {
            String vbs = prop("ro.boot.verifiedbootstate"), fl = prop("ro.boot.flash.locked");
            boolean unlocked = "orange".equals(vbs) || "0".equals(fl);
            r.put("bootloader", unlocked ? "unlocked" : (vbs.isEmpty() && fl.isEmpty() ? "?" : "locked"));
            r.put("model", prop("ro.product.marketname").isEmpty() ? prop("ro.product.model") : prop("ro.product.marketname"));
            r.put("os", prop("ro.mi.os.version.incremental"));
            r.put("magisk", installed(c, "com.topjohnwu.magisk"));
            boolean root = hasRoot();
            r.put("root", root);
            if (root) {
                String cur = current();
                r.put("mac", cur);
                SharedPreferences p = c.getSharedPreferences("mozgnet", Context.MODE_PRIVATE);
                if (!cur.isEmpty() && p.getString("phone_orig_mac", "").isEmpty()) {
                    p.edit().putString("phone_orig_mac", cur).apply();
                }
                r.put("orig", orig(c));
            }
            r.put("spin", spin != null && spin.isAlive());
            r.put("spin_info", spinInfo);
        } catch (Exception ignored) {
        }
        return r;
    }

    /** mode: rotate | mirror | restore | spin | stop; arg: MAC платы (mirror) или секунды (spin). */
    static JSONObject run(Context c, String mode, String arg) {
        JSONObject r = new JSONObject();
        try {
            if (!hasRoot()) {
                r.put("ok", false);
                r.put("why", "нет root: разблокируй загрузчик и поставь Magisk (кнопка «Как получить root»), " +
                        "затем разреши mozgnet в Magisk");
                return r;
            }
            String cur = current();
            if (cur.isEmpty()) {
                r.put("ok", false);
                r.put("why", "Wi-Fi выключен или нет интерфейса " + IF);
                return r;
            }
            info(c);                                   // запомнить родной адрес
            switch (mode) {
                case "rotate":
                    return setMac(c, flip(cur));
                case "mirror":
                    if (!validMac(arg)) throw new Exception("сначала «MAC: показать» у платы — нужен её адрес");
                    return setMac(c, flip(arg));
                case "restore":
                    stopSpin();
                    String o = orig(c);
                    if (!validMac(o)) throw new Exception("родной адрес неизвестен");
                    return setMac(c, o);
                case "spin":
                    double gap;
                    try { gap = Math.max(5, Double.parseDouble(arg)); } catch (Exception e) { gap = 60; }
                    startSpin(c, gap);
                    r.put("ok", true);
                    r.put("why", "вращение каждые " + (int) gap + " с");
                    return r;
                case "stop":
                    stopSpin();
                    r.put("ok", true);
                    return r;
                default:
                    throw new Exception("неизвестная команда " + mode);
            }
        } catch (Exception e) {
            try {
                r.put("ok", false);
                r.put("why", String.valueOf(e.getMessage()));
            } catch (Exception ignored) {
            }
        }
        return r;
    }

    static synchronized void startSpin(Context c, double gapS) {
        stopSpin();
        spinStop = false;
        Context app = c.getApplicationContext();
        spin = new Thread(() -> {
            int n = 0;
            while (!spinStop) {
                try {
                    String cur = current();
                    if (!cur.isEmpty()) {
                        JSONObject x = setMac(app, flip(cur));
                        n++;
                        spinInfo = "оборотов " + n + ", сейчас " + x.optString("now") +
                                (x.optBoolean("ok") ? "" : " (" + x.optString("why") + ")");
                    }
                    long until = System.currentTimeMillis() + (long) (gapS * 1000);
                    while (!spinStop && System.currentTimeMillis() < until) Thread.sleep(250);
                } catch (InterruptedException e) {
                    return;
                } catch (Exception e) {
                    spinInfo = "ошибка: " + e.getMessage();
                }
            }
        }, "phone-mac-spin");
        spin.start();
    }

    static synchronized void stopSpin() {
        spinStop = true;
        Thread t = spin;
        if (t != null) t.interrupt();
        spin = null;
    }
}
