package ru.mozgnet.app;

import android.content.Context;

import com.jcraft.jsch.ChannelExec;
import com.jcraft.jsch.JSch;
import com.jcraft.jsch.Session;

import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Properties;

/**
 * Вращение MAC платы с телефона — то же, что делает лаунчер на компьютере:
 * по SSH кладём на плату n12_mac.sh и запускаем его
 * (show / rotate / restore / spin <сек> / stop). Нужны права root: если у
 * пользователя платы нет sudo без пароля, тот же пароль подаётся sudo через
 * stdin (в командную строку он не попадает).
 *
 * MAC самого телефона приложение менять не может (это только root или
 * «Случайный MAC» в настройках Wi-Fi Android).
 */
public class BoardMac {
    static final String SCRIPT = "~/ArduinoApps/n12_mac.sh";

    static class Res {
        int code = -1;
        String out = "";
    }

    private static Session open(String host, String user, String pass) throws Exception {
        JSch j = new JSch();
        Session s = j.getSession(user, host, 22);
        s.setPassword(pass);
        Properties p = new Properties();
        p.put("StrictHostKeyChecking", "no");          // плата в своей сети, ключ меняется при перепрошивке
        p.put("PreferredAuthentications", "password,keyboard-interactive");
        s.setConfig(p);
        s.setServerAliveInterval(5000);
        s.connect(8000);
        return s;
    }

    private static Res exec(Session s, String cmd, byte[] stdin, int timeoutMs) throws Exception {
        ChannelExec ch = (ChannelExec) s.openChannel("exec");
        ch.setCommand(cmd);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        ch.setOutputStream(out);
        ch.setErrStream(out);
        ch.setInputStream(stdin == null ? null : new ByteArrayInputStream(stdin));
        ch.connect(8000);
        long t0 = System.currentTimeMillis();
        while (!ch.isClosed() && System.currentTimeMillis() - t0 < timeoutMs) Thread.sleep(100);
        Res r = new Res();
        r.code = ch.isClosed() ? ch.getExitStatus() : -2;
        r.out = new String(out.toByteArray(), StandardCharsets.UTF_8);
        ch.disconnect();
        return r;
    }

    private static byte[] asset(Context c, String name) throws Exception {
        try (InputStream in = c.getAssets().open(name)) {
            return CloudService.readAll(in);
        }
    }

    /** Пары KEY:VALUE из ответа скрипта (WAS, NEW, STEP, IFACE, ERR, SPIN…). */
    static JSONObject kv(String out) {
        JSONObject o = new JSONObject();
        for (String ln : out.split("\n")) {
            int i = ln.indexOf(':');
            if (i > 0 && i < 12 && ln.substring(0, i).matches("[A-Z_]+")) {
                try { o.put(ln.substring(0, i), ln.substring(i + 1).trim()); } catch (Exception ignored) { }
            }
        }
        return o;
    }

    /**
     * mode: show | rotate | restore | spin | stop; arg — промежуток спина, с.
     * Ответ JSON: ok, out, kv, sudo (понадобился ли пароль для sudo).
     */
    static JSONObject run(Context c, String host, String user, String pass, String mode, String arg) {
        JSONObject r = new JSONObject();
        Session s = null;
        try {
            if (!mode.matches("show|rotate|restore|spin|stop")) throw new Exception("неизвестная команда " + mode);
            String a = mode.equals("spin") ? " " + (arg == null || !arg.matches("[0-9.]+") ? "0" : arg) + " 4" : "";
            s = open(host, user, pass);
            // скрипт на плату — всегда свежий (как в лаунчере)
            Res up = exec(s, "mkdir -p ~/ArduinoApps && cat > " + SCRIPT + ".new && mv -f " + SCRIPT + ".new " + SCRIPT,
                    asset(c, "board/n12_mac.sh"), 15000);
            if (up.code != 0) throw new Exception("не скопировал скрипт: " + up.out.trim());
            Res x = exec(s, "bash " + SCRIPT + " " + mode + a, null, 25000);
            boolean sudo = false;
            if (x.out.contains("ERR:SUDO")) {
                sudo = true;
                byte[] pw = (pass + "\n").getBytes(StandardCharsets.UTF_8);
                x = exec(s, "sudo -S -p '' bash " + SCRIPT + " " + mode + a, pw, 30000);
            }
            String out = x.out.replace(pass, "•••");
            r.put("ok", out.contains("OK:") || mode.equals("show") && !out.contains("ERR:"));
            r.put("out", out.trim());
            r.put("kv", kv(out));
            r.put("sudo", sudo);
            if (out.contains("incorrect password") || out.contains("неверный пароль")) {
                r.put("ok", false);
                r.put("out", "sudo не принял пароль — пользователь платы должен быть в группе sudo");
            }
        } catch (Exception e) {
            try {
                r.put("ok", false);
                String m = String.valueOf(e.getMessage());
                if (m.contains("Auth fail") || m.contains("Auth cancel")) m = "неверный логин или пароль платы";
                // смена MAC рвёт связь — это нормально, если команда уже ушла
                if ((mode.equals("rotate") || mode.equals("restore")) && (m.contains("closed") || m.contains("reset"))) {
                    r.put("ok", true);
                    m = "связь оборвалась на смене MAC — так и должно быть";
                }
                r.put("out", m);
            } catch (Exception ignored) {
            }
        } finally {
            if (s != null) s.disconnect();
        }
        return r;
    }
}
