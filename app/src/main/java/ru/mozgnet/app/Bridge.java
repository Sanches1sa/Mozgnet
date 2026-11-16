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

    @JavascriptInterface
    public void home() {
        act.runOnUiThread(act::goHome);
    }
}
