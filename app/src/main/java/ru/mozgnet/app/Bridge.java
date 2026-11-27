package ru.mozgnet.app;

import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.widget.Toast;

import org.json.JSONObject;
import java.net.HttpURLConnection;

/** Мост стартового экрана (window.Mozg) к телефону: облако, скан, платы. */
public class Bridge {
    private final MainActivity act;
    private final SharedPreferences prefs;

    Bridge(MainActivity a) {
        act = a;
        prefs = a.getSharedPreferences("mozgnet", Context.MODE_PRIVATE);
    }

    /**
     * Мост открыт ТОЛЬКО стартовому экрану из APK. Страницы плат, облаков и
     * любые сайты, открытые в окне, не могут прочитать пароль и токен или
     * перенастроить плату (кроме кнопки «⌂ mozgnet» — home()).
     */
    private boolean ok() {
        return act.onHome;
    }

    private void js(String code) {
        act.runOnUiThread(() -> act.web.evaluateJavascript(code, null));
    }

    private static String q(String s) {
        return JSONObject.quote(s == null ? "" : s);
    }

    @JavascriptInterface
    public String info() {
        if (!ok()) return "{}";
        JSONObject o = new JSONObject();
        try {
            o.put("app", BuildConfig.VERSION_NAME);
            o.put("cloud_apk", BuildConfig.CLOUD_VERSION);
            o.put("repo", BuildConfig.REPO);
            o.put("ip", Scanner.myIp());
            o.put("port", CloudService.PORT);
            o.put("local_on", prefs.getBoolean("local_on", true));
            o.put("service", CloudService.running);
            o.put("remote_on", prefs.getBoolean("remote_on", false));
            o.put("remote_url", prefs.getString("remote_url", ""));
            o.put("error", CloudService.lastError);
            if (CloudService.running) {
                try {
                    JSONObject st = new JSONObject(CloudService.runner(act).callAttr("status").toString());
                    o.put("status", st);
                    if (st.optBoolean("ok")) {
                        o.put("password", CloudService.runner(act).callAttr("ensure_password").toString());
                        o.put("token", CloudService.runner(act).callAttr("token").toString());
                    }
                } catch (Throwable t) {
                    o.put("error", String.valueOf(t));
                }
            }
        } catch (Exception ignored) {
        }
        return o.toString();
    }

    @JavascriptInterface
    public void setLocal(boolean on) {
        if (!ok()) return;
        prefs.edit().putBoolean("local_on", on).apply();
        act.runOnUiThread(() -> {
            if (on) act.startCloud();
            else act.stopService(new Intent(act, CloudService.class));
        });
    }

    /** Открыть облако телефона уже с входом владельца (без пароля). */
    @JavascriptInterface
    public void openLocal() {
        if (!ok()) return;
        new Thread(() -> {
            String sid = "";
            try {
                sid = CloudService.runner(act).callAttr("session").toString();
            } catch (Throwable ignored) {
            }
            String s = sid;
            act.runOnUiThread(() -> {
                String base = "http://127.0.0.1:" + CloudService.PORT;
                if (!s.isEmpty()) {
                    CookieManager cm = CookieManager.getInstance();
                    cm.setCookie(base, "n12sid=" + s + "; Path=/");
                    cm.flush();
                }
                act.web.loadUrl(base + "/");
            });
        }, "open-local").start();
    }


    // ---------------- облако на компьютере/сервере (телефон — клиент) ----------------

    /** Состояние подключения к внешнему облаку (без пароля/токена целиком). */
    @JavascriptInterface
    public String remoteInfo() {
        if (!ok()) return "{}";
        JSONObject o = new JSONObject();
        try {
            o.put("url", prefs.getString("remote_url", ""));
            o.put("on", prefs.getBoolean("remote_on", false));
            o.put("has_pw", !prefs.getString("remote_pw", "").isEmpty());
            o.put("token", prefs.getString("remote_token", ""));
            o.put("fp", prefs.getString("remote_fp", ""));
        } catch (Exception ignored) {
        }
        return o.toString();
    }

    /**
     * Подключить телефон к облаку на компьютере: адрес (http://192.168.1.5:8077
     * или https://…:8443), пароль панели, мозг-токен. Облако в самом телефоне
     * при этом выключается — телефон ничего не раздаёт. Результат — в
     * window.onRemote(ok, текст).
     */
    @JavascriptInterface
    public void connectCloud(String url, String password, String token) {
        if (!ok()) return;
        new Thread(() -> {
            String res = "ok";
            try {
                if (Remote.isLoopback(url)) throw new Exception(Remote.LOOPBACK_MSG);
                String u = Remote.norm(url);
                if (u.isEmpty()) throw new Exception("укажи адрес облака");
                String oldU = prefs.getString("remote_url", "");
                String fp = u.equals(oldU) ? prefs.getString("remote_fp", "") : "";
                String[] seen = new String[1];
                JSONObject h = Remote.json(Remote.request("GET", u + "/api/health", null, fp, seen, null, 4000));
                if (!"neuro12-cloud".equals(h.optString("service"))) throw new Exception("по этому адресу не облако neuro12");
                SharedPreferences.Editor e = prefs.edit().putString("remote_url", u).putBoolean("remote_on", true)
                        .putBoolean("local_on", false);
                if (u.toLowerCase().startsWith("https://") && seen[0] != null) e.putString("remote_fp", seen[0]);
                else e.putString("remote_fp", "");
                if (password != null && !password.isEmpty()) e.putString("remote_pw", password);
                if (token != null && !token.trim().isEmpty()) e.putString("remote_token", token.trim());
                e.apply();
                Remote.load(prefs);
                act.runOnUiThread(() -> act.stopService(new Intent(act, CloudService.class)));
            } catch (Exception ex) {
                res = String.valueOf(ex.getMessage());
            }
            js("window.onRemote&&onRemote(" + q(res.equals("ok") ? "1" : "") + "," + q(res) + ")");
        }, "connect-cloud").start();
    }

    /** Отключиться от облака компьютера (облако телефона не включается само). */
    @JavascriptInterface
    public void disconnectCloud() {
        if (!ok()) return;
        prefs.edit().putBoolean("remote_on", false).remove("remote_pw").remove("remote_fp").apply();
        Remote.load(prefs);
        js("window.onRemote&&onRemote('','отключено')");
    }

    /** Открыть облако компьютера в окне приложения (вход паролем панели). */
    @JavascriptInterface
    public void openRemote() {
        if (!ok()) return;
        new Thread(() -> {
            String u = prefs.getString("remote_url", "");
            if (u.isEmpty()) return;
            String fp = prefs.getString("remote_fp", "");
            String pw = prefs.getString("remote_pw", "");
            String[] cookie = new String[1];
            String err = "";
            if (!pw.isEmpty()) {
                try {
                    JSONObject b = new JSONObject();
                    b.put("password", pw);
                    Remote.request("POST", u + "/api/auth/login", b.toString(), fp, null, cookie, 5000);
                } catch (Exception ex) {
                    err = String.valueOf(ex.getMessage());
                }
            }
            String e2 = err;
            act.runOnUiThread(() -> {
                if (cookie[0] != null) {
                    CookieManager cm = CookieManager.getInstance();
                    cm.setCookie(u, cookie[0] + "; Path=/");
                    cm.flush();
                }
                if (!e2.isEmpty()) Toast.makeText(act, "Вход не удался: " + e2, Toast.LENGTH_LONG).show();
                act.web.loadUrl(u + "/");
            });
        }, "open-remote").start();
    }

    private static String botCall(String url, String body, String tok, String fp) throws Exception {
        HttpURLConnection c = Remote.open(url, fp, null);
        c.setConnectTimeout(4000);
        c.setReadTimeout(8000);
        c.setRequestMethod("POST");
        c.setRequestProperty("X-Mozg-Token", tok);
        c.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        c.setDoOutput(true);
        try (java.io.OutputStream o = c.getOutputStream()) {
            o.write(body.getBytes("UTF-8"));
        }
        int code = c.getResponseCode();
        java.io.InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
        String txt = in == null ? "" : new String(CloudService.readAll(in), "UTF-8");
        c.disconnect();
        if (code >= 400) throw new Exception("HTTP " + code + " " + txt.substring(0, Math.min(120, txt.length())));
        return txt;
    }

    // ---------------- бот МАКС: кнопка «включить/выключить» ----------------

    /**
     * Включить/выключить бота (maxbot_on) в ТЕКУЩЕМ облаке: если телефон
     * подключён к облаку компьютера — в нём, иначе в облаке телефона
     * (localToken — мозг-токен локального облака). on<0 — только узнать
     * состояние. Результат: window.onBot(включён, токен_задан, текст).
     */
    @JavascriptInterface
    public void botSet(int on, String localToken) {
        if (!ok()) return;
        new Thread(() -> {
            String err = "";
            boolean enabled = false, tokenSet = false;
            try {
                boolean remote = prefs.getBoolean("remote_on", false);
                String base = remote ? prefs.getString("remote_url", "") : "http://127.0.0.1:" + CloudService.PORT;
                String tok = remote ? prefs.getString("remote_token", "") : String.valueOf(localToken);
                String fp = remote ? prefs.getString("remote_fp", "") : "";
                if (base.isEmpty()) throw new Exception("облако не выбрано");
                if (on >= 0) {
                    JSONObject b = new JSONObject();
                    b.put("maxbot_on", on > 0);
                    botCall(base + "/api/maxbot/config", b.toString(), tok, fp);
                }
                JSONObject r = new JSONObject(botCall(base + "/api/maxbot/status", "{}", tok, fp));
                JSONObject m = r.optJSONObject("maxbot");
                if (m == null) m = r;
                enabled = m.optBoolean("enabled", false);
                tokenSet = m.optBoolean("token_set", false);
            } catch (Exception ex) {
                err = String.valueOf(ex.getMessage());
            }
            js("window.onBot&&onBot(" + enabled + "," + tokenSet + "," + q(err) + ")");
        }, "bot-set").start();
    }

    @JavascriptInterface
    public void open(String url) {
        if (!ok()) return;
        act.runOnUiThread(() -> act.web.loadUrl(url));
    }

    @JavascriptInterface
    public void browser(String url) {
        if (!ok()) return;
        act.runOnUiThread(() -> act.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url))));
    }

    @JavascriptInterface
    public void copy(String text) {
        if (!ok()) return;
        act.runOnUiThread(() -> {
            ClipboardManager cb = (ClipboardManager) act.getSystemService(Context.CLIPBOARD_SERVICE);
            if (cb != null) cb.setPrimaryClip(ClipData.newPlainText("mozgnet", text));
            Toast.makeText(act, "Скопировано", Toast.LENGTH_SHORT).show();
        });
    }

    @JavascriptInterface
    public String pref(String key) {
        if (!ok()) return "{}";
        return prefs.getString("p_" + key, "");
    }

    @JavascriptInterface
    public void setPref(String key, String val) {
        if (!ok()) return;
        prefs.edit().putString("p_" + key, val).apply();
    }

    /** Скан сети: результаты приходят в window.onScan(done, total, host|null, finished). */
    @JavascriptInterface
    public void scan() {
        if (!ok()) return;
        new Thread(() -> {
            Scanner.scan((d, t, h) -> js("window.onScan&&onScan(" + d + "," + t + "," + (h == null ? "null" : h.toString()) + ",false)"));
            js("window.onScan&&onScan(0,0,null,true)");
        }, "scan").start();
    }

    /** Проверить один адрес (вписан вручную). */
    @JavascriptInterface
    public void probe(String ip) {
        if (!ok()) return;
        if (Remote.isLoopback(ip)) {
            js("window.onProbe&&onProbe(" + q(ip) + ",null,'loop')");
            return;
        }
        new Thread(() -> {
            JSONObject h = Scanner.probe(ip.trim());
            js("window.onProbe&&onProbe(" + q(ip) + "," + (h == null ? "null" : h.toString()) + ")");
        }, "probe").start();
    }

    /**
     * Подключить плату к облаку: POST {board}/api/settings с адресом облака и
     * мозг-токеном (так же, как настройки платы в её окне).
     */
    @JavascriptInterface
    public void connectBoard(String boardBase, String cloudUrl, String token) {
        if (!ok()) return;
        new Thread(() -> {
            String res;
            try {
                JSONObject b = new JSONObject();
                b.put("cloud_url", cloudUrl);
                if (token != null && !token.trim().isEmpty()) b.put("token", token.trim());
                String base = boardBase.endsWith("/") ? boardBase.substring(0, boardBase.length() - 1) : boardBase;
                Scanner.http("POST", base + "/api/settings", b.toString(), 4000);
                res = "ok";
            } catch (Exception e) {
                res = String.valueOf(e.getMessage());
            }
            js("window.onConnect&&onConnect(" + q(boardBase) + "," + q(res) + ")");
        }, "connect").start();
    }

    /** v1.1: логин платы для SSH (вращение MAC) — хранится в приложении. */
    @JavascriptInterface
    public String boardUser() {
        if (!ok()) return "{}";
        JSONObject o = new JSONObject();
        try {
            o.put("user", prefs.getString("b_user", "arduino"));
            o.put("has_pass", !prefs.getString("b_pass", "").isEmpty());
        } catch (Exception ignored) {
        }
        return o.toString();
    }

    @JavascriptInterface
    public void setBoardUser(String user, String pass) {
        if (!ok()) return;
        SharedPreferences.Editor e = prefs.edit().putString("b_user", user == null || user.trim().isEmpty() ? "arduino" : user.trim());
        if (pass != null && !pass.isEmpty()) e.putString("b_pass", pass);
        e.apply();
    }

    /** MAC платы: show | rotate | restore | spin <сек> | stop -> window.onMac(ip, ответ). */
    @JavascriptInterface
    public void mac(String ip, String mode, String arg) {
        if (!ok()) return;
        String user = prefs.getString("b_user", "arduino"), pass = prefs.getString("b_pass", "");
        new Thread(() -> {
            JSONObject r = BoardMac.run(act, ip, user, pass, mode, arg);
            js("window.onMac&&onMac(" + q(ip) + "," + q(mode) + "," + r.toString() + ")");
        }, "mac").start();
    }

    /** v1.2: MAC телефона (root). Ответ — window.onPhone(состояние). */
    @JavascriptInterface
    public void phoneInfo() {
        if (!ok()) return;
        new Thread(() -> js("window.onPhone&&onPhone(" + PhoneMac.info(act) + ")"), "phone-info").start();
    }

    /** rotate | mirror (arg — MAC платы) | restore | spin (arg — секунды) | stop -> window.onPhoneMac. */
    @JavascriptInterface
    public void phoneMac(String mode, String arg) {
        if (!ok()) return;
        new Thread(() -> {
            JSONObject r = PhoneMac.run(act, mode, arg);
            js("window.onPhoneMac&&onPhoneMac(" + q(mode) + "," + r + ")");
            js("window.onPhone&&onPhone(" + PhoneMac.info(act) + ")");
        }, "phone-mac").start();
    }

    /** Открыть настройки Android: dev — для разработчиков, wifi — панель Wi-Fi, about — о телефоне. */
    @JavascriptInterface
    public void settings(String which) {
        if (!ok()) return;
        act.runOnUiThread(() -> {
            String a = "dev".equals(which) ? android.provider.Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS
                    : "about".equals(which) ? android.provider.Settings.ACTION_DEVICE_INFO_SETTINGS
                    : (android.os.Build.VERSION.SDK_INT >= 29 ? android.provider.Settings.Panel.ACTION_WIFI
                    : android.provider.Settings.ACTION_WIFI_SETTINGS);
            try {
                act.startActivity(new Intent(a));
            } catch (Exception e) {
                act.startActivity(new Intent(android.provider.Settings.ACTION_SETTINGS));
            }
        });
    }

    @JavascriptInterface
    public void home() {
        act.runOnUiThread(act::goHome);
    }
}
