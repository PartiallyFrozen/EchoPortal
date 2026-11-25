package com.echoportal

import android.content.SharedPreferences
import android.os.Build

/** Persisted shell preferences (thin wrapper so faces/shell never touch SharedPreferences directly). */
class ShellSettings(private val p: SharedPreferences) {
    /** Stable per-device id, generated once and reused across restarts/reinstalls. */
    val deviceId: String
        get() {
            val saved = p.getString("device_id", "") ?: ""
            if (saved.isNotEmpty()) return saved
            val generated = java.util.UUID.randomUUID().toString()
            p.edit().putString("device_id", generated).apply()
            return generated
        }

    /** Human-readable label for this Spot; falls back to the hardware model. */
    var deviceName: String
        get() = (p.getString("device_name", "") ?: "").ifEmpty { Build.MODEL }
        set(v) = p.edit().putString("device_name", v.trim()).apply()

    var server: String
        get() = p.getString("server", "") ?: ""
        set(v) = p.edit().putString("server", v).apply()

    /** Media/API base for the same PC, e.g. `http://192.168.0.147:8766`. Null if the server is unset. */
    val mediaBase: String?
        get() {
            val s = server.trim()
            if (s.isEmpty()) return null
            return try {
                val uri = java.net.URI(if (s.contains("://")) s else "ws://$s")
                uri.host?.let { "http://$it:8766" }
            } catch (_: Exception) { null }
        }

    /** Shared token for the PC hub/API; empty means the PC trusts the LAN. */
    var apiToken: String
        get() = p.getString("api_token", "") ?: ""
        set(v) = p.edit().putString("api_token", v).apply()

    /** File inside the PC's media/ folder that the Video Clock loops. */
    var videoClip: String
        get() = p.getString("video_clip", "fireplace.mp4") ?: "fireplace.mp4"
        set(v) = p.edit().putString("video_clip", v).apply()

    var lastFace: String
        get() = p.getString("last_face", "clock") ?: "clock"
        set(v) = p.edit().putString("last_face", v).apply()
    var brightness: Float
        get() = p.getFloat("brightness", 0.8f)
        set(v) = p.edit().putFloat("brightness", v).apply()
    var ambientBrightness: Float
        get() = p.getFloat("ambient_brightness", 0.06f)
        set(v) = p.edit().putFloat("ambient_brightness", v).apply()
    var ambientEnabled: Boolean
        get() = p.getBoolean("ambient_enabled", true)
        set(v) = p.edit().putBoolean("ambient_enabled", v).apply()
    var idleMinutes: Int
        get() = p.getInt("idle_minutes", 10)
        set(v) = p.edit().putInt("idle_minutes", v).apply()
    var nightMode: Boolean
        get() = p.getBoolean("night_mode", true)
        set(v) = p.edit().putBoolean("night_mode", v).apply()
    var nightStart: Int
        get() = p.getInt("night_start", 23)
        set(v) = p.edit().putInt("night_start", v).apply()
    var nightEnd: Int
        get() = p.getInt("night_end", 7)
        set(v) = p.edit().putInt("night_end", v).apply()
    var sleepEnabled: Boolean
        get() = p.getBoolean("sleep_enabled", true)
        set(v) = p.edit().putBoolean("sleep_enabled", v).apply()
    var alwaysOn: Boolean        // keep the panel lit and un-dimmed while the PC agent is connected
        get() = p.getBoolean("always_on", true)
        set(v) = p.edit().putBoolean("always_on", v).apply()
    var wakeWord: Boolean        // listen for "Jarvis" on the device (needs a Picovoice AccessKey)
        get() = p.getBoolean("wake_word", false)
        set(v) = p.edit().putBoolean("wake_word", v).apply()
    var picovoiceKey: String
        get() = p.getString("picovoice_key", "") ?: ""
        set(v) = p.edit().putString("picovoice_key", v).apply()
    var sleepStart: Int          // legacy hour picker, kept for old preferences
        get() = p.getInt("sleep_start", 20)
        set(v) = p.edit().putInt("sleep_start", v).apply()
    var sleepEnd: Int            // legacy hour picker, kept for old preferences
        get() = p.getInt("sleep_end", 7)
        set(v) = p.edit().putInt("sleep_end", v).apply()

    // Minute-of-day windows. Off hours apply only when the PC is not keeping the Spot awake.
    var sleepWeekdayStart: Int
        get() = p.getInt("sleep_weekday_start", 22 * 60 + 30)
        set(v) = p.edit().putInt("sleep_weekday_start", v).apply()
    var sleepWeekdayEnd: Int
        get() = p.getInt("sleep_weekday_end", 7 * 60 + 30)
        set(v) = p.edit().putInt("sleep_weekday_end", v).apply()
    var sleepWeekendStart: Int
        get() = p.getInt("sleep_weekend_start", 23 * 60 + 30)
        set(v) = p.edit().putInt("sleep_weekend_start", v).apply()
    var sleepWeekendEnd: Int
        get() = p.getInt("sleep_weekend_end", 9 * 60)
        set(v) = p.edit().putInt("sleep_weekend_end", v).apply()

    // Weekday morning override: fully on even if the PC is offline or the screen would idle-dim.
    var awakeWeekdayStart: Int
        get() = p.getInt("awake_weekday_start", 7 * 60 + 30)
        set(v) = p.edit().putInt("awake_weekday_start", v).apply()
    var awakeWeekdayEnd: Int
        get() = p.getInt("awake_weekday_end", 9 * 60 + 15)
        set(v) = p.edit().putInt("awake_weekday_end", v).apply()
    var bgMode: String           // Background.BLACK / COLOR / SKY / IMAGE
        get() = p.getString("bg_mode", "black") ?: "black"
        set(v) = p.edit().putString("bg_mode", v).apply()
    var bgColor: Int
        get() = p.getInt("bg_color", 0xFF0B1E3A.toInt())
        set(v) = p.edit().putInt("bg_color", v).apply()
    var clockStyle: Int
        get() = p.getInt("clock_style", 0)
        set(v) = p.edit().putInt("clock_style", v).apply()
    var calDx: Float
        get() = p.getFloat("cal_dx", -2f)
        set(v) = p.edit().putFloat("cal_dx", v).apply()
    var calDy: Float
        get() = p.getFloat("cal_dy", 26f)
        set(v) = p.edit().putFloat("cal_dy", v).apply()
    var calScale: Float
        get() = p.getFloat("cal_scale", 1.07f)
        set(v) = p.edit().putFloat("cal_scale", v).apply()
}
