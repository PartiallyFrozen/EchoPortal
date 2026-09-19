package com.echoportal

import android.content.SharedPreferences

/** Persisted shell preferences (thin wrapper so faces/shell never touch SharedPreferences directly). */
class ShellSettings(private val p: SharedPreferences) {
    var server: String
        get() = p.getString("server", "") ?: ""
        set(v) = p.edit().putString("server", v).apply()
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
    var sleepStart: Int          // hour 0-23, screen + connection off from here...
        get() = p.getInt("sleep_start", 20)
        set(v) = p.edit().putInt("sleep_start", v).apply()
    var sleepEnd: Int            // ...until here
        get() = p.getInt("sleep_end", 7)
        set(v) = p.edit().putInt("sleep_end", v).apply()
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
