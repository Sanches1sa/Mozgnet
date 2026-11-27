package ru.mozgnet.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.content.res.AssetManager;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;

/**
 * Облако «Star Gate» в телефоне: служба переднего плана держит Python-сервер
 * (тот же код облака, что на компьютере) — платы в Wi-Fi подключаются к
 * http://IP-телефона:8077, как к облаку на компьютере.
 */
public class CloudService extends Service {
    static final String TAG = "mozgnet";
    static final String CH = "cloud";
    static final int PORT = 8077;
    static volatile boolean running = false;
    static volatile String lastError = "";
    private WifiManager.WifiLock wifiLock;

    static File cloudRoot(Context c) {
        return new File(c.getFilesDir(), "cloud");
    }

    static synchronized PyObject runner(Context c) {
        if (!Python.isStarted()) Python.start(new AndroidPlatform(c.getApplicationContext()));
        return Python.getInstance().getModule("mozgnet_runner");
    }

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26 && nm != null) {
            NotificationChannel ch = new NotificationChannel(CH, "Облако mozgnet", NotificationManager.IMPORTANCE_LOW);
            ch.setDescription("Облако «Star Gate» работает в телефоне");
            nm.createNotificationChannel(ch);
        }
    }

    private Notification note(String text) {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pi = PendingIntent.getActivity(this, 0, open,
                PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0));
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CH) : new Notification.Builder(this);
        return b.setSmallIcon(R.drawable.ic_stat)
                .setContentTitle("mozgnet: облако работает")
                .setContentText(text)
                .setContentIntent(pi)
                .setOngoing(true)
                .build();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String ip = Scanner.myIp();
        Notification n = note("Платы подключаются к http://" + (ip == null ? "IP-телефона" : ip) + ":" + PORT);
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(1, n);
        }
        try {
            WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wm != null && wifiLock == null) {
                wifiLock = wm.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "mozgnet:cloud");
                wifiLock.acquire();
            }
        } catch (Exception e) {
            Log.w(TAG, "wifi lock", e);
        }
        if (!running) {
            running = true;
            new Thread(() -> {
                try {
                    install(this);
                    runner(this).callAttr("start", cloudRoot(this).getAbsolutePath(), PORT);
                    lastError = "";
                } catch (Throwable t) {
                    lastError = String.valueOf(t);
                    running = false;
                    Log.e(TAG, "cloud start", t);
                }
            }, "cloud-start").start();
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        if (wifiLock != null && wifiLock.isHeld()) wifiLock.release();
        super.onDestroy();
        // Python-сервер живёт до конца процесса: остановить его можно, только
        // закрыв приложение (Настройки → Остановить) — так надёжнее для данных.
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    // ------------------------------------------------ установка облака из APK
    static String readText(File f) {
        try (InputStream in = new FileInputStream(f)) {
            return new String(readAll(in), StandardCharsets.UTF_8).trim();
        } catch (IOException e) {
            return "";
        }
    }

    static byte[] readAll(InputStream in) throws IOException {
        java.io.ByteArrayOutputStream bo = new java.io.ByteArrayOutputStream();
        byte[] buf = new byte[65536];
        int n;
        while ((n = in.read(buf)) > 0) bo.write(buf, 0, n);
        return bo.toByteArray();
    }

    /** Сравнение версий «3.177.0» > «3.176.2». */
    static int cmpVer(String a, String b) {
        String[] x = a.split("\\."), y = b.split("\\.");
        for (int i = 0; i < Math.max(x.length, y.length); i++) {
            int p = 0, q = 0;
            try { p = i < x.length ? Integer.parseInt(x[i].replaceAll("\\D", "")) : 0; } catch (NumberFormatException ignored) { }
            try { q = i < y.length ? Integer.parseInt(y[i].replaceAll("\\D", "")) : 0; } catch (NumberFormatException ignored) { }
            if (p != q) return p < q ? -1 : 1;
        }
        return 0;
    }

    /**
     * Код облака из APK -> files/cloud, если там его нет или он старее.
     * data/ (память, база, модель) НЕ трогается никогда. Облако, обновлённое
     * через свою панель («Обновление»), новее APK — его не перезаписываем.
     */
    static synchronized void install(Context c) throws IOException {
        File root = cloudRoot(c);
        String have = readText(new File(root, "VERSION"));
        String apk;
        try (InputStream in = c.getAssets().open("cloud/VERSION")) {
            apk = new String(readAll(in), StandardCharsets.UTF_8).trim();
        }
        if (!have.isEmpty() && new File(root, "server/app.py").exists() && cmpVer(apk, have) <= 0) return;
        Log.i(TAG, "ставлю облако " + apk + " (было " + (have.isEmpty() ? "нет" : have) + ")");
        copyDir(c.getAssets(), "cloud", root);
        new File(root, "data").mkdirs();
    }

    private static void copyDir(AssetManager am, String path, File dst) throws IOException {
        String[] list = am.list(path);
        if (list == null || list.length == 0) {          // файл
            if (path.endsWith("/data") || path.contains("/data/")) return;
            File parent = dst.getParentFile();
            if (parent != null) parent.mkdirs();
            File tmp = new File(dst.getPath() + ".tmp");
            InputStream in;
            try {
                in = am.open(path);
            } catch (IOException e) {                    // пустая папка в APK — не файл
                return;
            }
            try (InputStream src = in; OutputStream out = new FileOutputStream(tmp)) {
                byte[] buf = new byte[65536];
                int n;
                while ((n = src.read(buf)) > 0) out.write(buf, 0, n);
            }
            if (!tmp.renameTo(dst)) {
                dst.delete();
                tmp.renameTo(dst);
            }
            return;
        }
        if (path.equals("cloud/data")) return;           // память не трогаем
        dst.mkdirs();
        for (String name : list) copyDir(am, path + "/" + name, new File(dst, name));
    }
}
