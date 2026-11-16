package ru.mozgnet.app;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.DownloadManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.PermissionRequest;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import java.net.InetAddress;

/**
 * mozgnet: одно окно, как у лаунчера. Стартовый экран (assets/home) — облако
 * телефона, скан сети, платы. Облако и платы открываются в этом же окне,
 * сверху — кнопка «⌂ mozgnet» назад к стартовому экрану.
 */
public class MainActivity extends Activity {
    static final String HOME = "file:///android_asset/home/index.html";
    WebView web;
    volatile boolean onHome = true;
    private ValueCallback<Uri[]> fileCb;

    // кнопка «назад к mozgnet» на страницах облака и плат
    private static final String BAR_JS =
            "(function(){if(document.getElementById('mozgnetHome'))return;" +
            "var b=document.createElement('div');b.id='mozgnetHome';b.textContent='⌂';" +
            "b.title='mozgnet';b.style.cssText='position:fixed;left:10px;bottom:84px;z-index:2147483647;" +
            "width:44px;height:44px;border-radius:22px;background:#2dd4bf;color:#0b0f17;font:700 22px/44px sans-serif;" +
            "text-align:center;box-shadow:0 4px 14px rgba(0,0,0,.45);opacity:.9;cursor:pointer';" +
            "b.onclick=function(){try{Mozg.home()}catch(e){location.href='" + HOME + "'}};" +
            "document.body.appendChild(b)})()";

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        web = new WebView(this);
        web.setLayoutParams(new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setSupportZoom(true);
        s.setBuiltInZoomControls(true);
        s.setDisplayZoomControls(false);
        s.setUserAgentString(s.getUserAgentString() + " mozgnet/" + BuildConfig.VERSION_NAME.split(" ")[0]);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true);

        web.addJavascriptInterface(new Bridge(this), "Mozg");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView v, String url, Bitmap fav) {
                onHome = url != null && url.startsWith("file:///android_asset/home/");
            }

            @Override
            public void onPageFinished(WebView v, String url) {
                onHome = url != null && url.startsWith("file:///android_asset/home/");
                if (!onHome) v.evaluateJavascript(BAR_JS, null);
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest req) {
                Uri u = req.getUrl();
                if (inside(u)) return false;
                // сайты из интернета — в браузере телефона, не в окне mozgnet
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (Exception e) {
                    Toast.makeText(MainActivity.this, "Не открыть: " + u, Toast.LENGTH_SHORT).show();
                }
                return true;
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> cb, FileChooserParams p) {
                if (fileCb != null) fileCb.onReceiveValue(null);
                fileCb = cb;
                try {
                    startActivityForResult(p.createIntent(), 7);
                } catch (Exception e) {
                    fileCb = null;
                    return false;
                }
                return true;
            }

            @Override
            public void onPermissionRequest(PermissionRequest r) {
                // микрофон/камера для облака и плат в локальной сети
                Uri o = r.getOrigin();
                if (o != null && inside(o)) {
                    askMedia();
                    r.grant(r.getResources());
                } else {
                    r.deny();
                }
            }
        });
        web.setDownloadListener((url, ua, cd, mime, len) -> {
            try {
                DownloadManager.Request rq = new DownloadManager.Request(Uri.parse(url));
                String cookie = CookieManager.getInstance().getCookie(url);
                if (cookie != null) rq.addRequestHeader("Cookie", cookie);
                rq.setMimeType(mime);
                String name = URLUtil.guessFileName(url, cd, mime);
                rq.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
                rq.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                ((DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE)).enqueue(rq);
                Toast.makeText(this, "Скачиваю: " + name, Toast.LENGTH_SHORT).show();
            } catch (Exception e) {
                Toast.makeText(this, "Не скачать: " + e.getMessage(), Toast.LENGTH_LONG).show();
            }
        });

        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 3);
        }
        SharedPreferences p = getSharedPreferences("mozgnet", MODE_PRIVATE);
        if (p.getBoolean("local_on", true)) startCloud();
        web.loadUrl(HOME);
    }

    /** Адрес из локальной сети (облако телефона, платы, облака в Wi-Fi) или APK. */
    static boolean inside(Uri u) {
        String sc = u.getScheme();
        if ("file".equals(sc) || "about".equals(sc) || "data".equals(sc) || "blob".equals(sc)) return true;
        String h = u.getHost();
        if (h == null) return true;
        if (h.equals("localhost")) return true;
        if (!h.matches("[0-9.]+")) return h.endsWith(".local") || h.endsWith(".lan");
        try {
            InetAddress a = InetAddress.getByName(h);
            return a.isSiteLocalAddress() || a.isLoopbackAddress() || a.isLinkLocalAddress();
        } catch (Exception e) {
            return false;
        }
    }

    void startCloud() {
        Intent i = new Intent(this, CloudService.class);
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(i); else startService(i);
    }

    void goHome() {
        web.loadUrl(HOME);
    }

    private void askMedia() {
        if (Build.VERSION.SDK_INT >= 23) {
            java.util.List<String> need = new java.util.ArrayList<>();
            for (String perm : new String[]{Manifest.permission.RECORD_AUDIO, Manifest.permission.CAMERA}) {
                if (checkSelfPermission(perm) != PackageManager.PERMISSION_GRANTED) need.add(perm);
            }
            if (!need.isEmpty()) runOnUiThread(() -> requestPermissions(need.toArray(new String[0]), 4));
        }
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        if (req == 7 && fileCb != null) {
            fileCb.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(res, data));
            fileCb = null;
            return;
        }
        super.onActivityResult(req, res, data);
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else if (!onHome) {
            goHome();
        } else {
            moveTaskToBack(true);   // облако продолжает работать в уведомлении
        }
    }
}
