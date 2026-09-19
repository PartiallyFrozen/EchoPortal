package com.echoportal

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.util.Log
import com.echoportal.Draw.fmt
import com.echoportal.Draw.label
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale
import java.util.concurrent.TimeUnit
import kotlin.math.cos
import kotlin.math.sin

/**
 * Clock + weather. Time/date large in the middle, current conditions below, and a 12-hour
 * forecast around the rim (temperature arc + rain-probability ticks). Also the ambient face.
 * Weather comes straight from Open-Meteo so it keeps working when the PC is off.
 */
class ClockFace(ctx: Context, private val lat: Double, private val lon: Double, private val tz: String) : Face(ctx) {
    override val id = "clock"
    override val name = "Clock"
    override val color = Color.parseColor("#FFD54F")
    override val channels = setOf("stats", "ticker", "comfy")

    /** Styles are pages; page 0 is the original EchoPortal look. The chosen page is remembered by the shell. */
    private val styles: List<ClockStyle> = listOf(ClassicStyle(), BoldStyle(), PixelStyle(), NeonStyle())
    override val pageCount get() = 1 + styles.size
    var onStyleChanged: (Int) -> Unit = {}
    private var curStyle = 0
    override fun onPage(page: Int) { curStyle = page; onStyleChanged(page) }

    // complication data from the other channels
    private var cpuPct: Double? = null
    private var gpuUtil: Double? = null
    private var gpuTemp: Int? = null
    private var btcPrice: Double? = null
    private var btcChange: Double? = null
    private var btcCurrency = "USD"
    private var jobsToday: Int? = null

    override fun onEvent(channel: String, data: JSONObject) {
        when (channel) {
            "stats" -> {
                cpuPct = data.optJSONObject("cpu")?.optDouble("total")
                data.optJSONObject("gpu")?.let { g -> gpuUtil = g.optDouble("util_3d"); gpuTemp = if (g.isNull("temp_c")) null else g.optInt("temp_c") }
            }
            "ticker" -> {
                val arr = data.optJSONArray("items") ?: return
                for (i in 0 until arr.length()) {
                    val o = arr.getJSONObject(i)
                    if (o.optString("sym") == "BTC") {
                        btcPrice = if (o.isNull("price")) null else o.optDouble("price")
                        btcChange = if (o.isNull("change_pct")) null else o.optDouble("change_pct")
                        btcCurrency = o.optString("currency", "USD")
                    }
                }
            }
            "comfy" -> jobsToday = data.optInt("jobs_today")
        }
    }

    private fun snapshot(now: LocalDateTime): ClockData {
        val w = weather
        return ClockData(now, w?.temp, w?.let { describe(it.code) }, w?.code, isNight(now, w), w?.hi, w?.lo,
            cpuPct, gpuUtil, gpuTemp, btcPrice, btcChange, btcCurrency, jobsToday)
    }

    private data class Hour(val hour: Int, val temp: Double, val rainPct: Int, val code: Int)
    private data class Weather(
        val temp: Double, val feels: Double, val code: Int, val wind: Double, val humidity: Int,
        val hi: Double, val lo: Double, val sunrise: String, val sunset: String, val hours: List<Hour>,
        val fetched: Long,
    )

    @Volatile private var weather: Weather? = null
    @Volatile private var fetching = false
    private var lastError: String? = null
    private val http = OkHttpClient.Builder().connectTimeout(8, TimeUnit.SECONDS).readTimeout(8, TimeUnit.SECONDS).build()
    private val timeFmt = DateTimeFormatter.ofPattern("h:mm", Locale.US)
    private val ampmFmt = DateTimeFormatter.ofPattern("a", Locale.US)
    private val dateFmt = DateTimeFormatter.ofPattern("EEEE, MMMM d", Locale.US)
    private val rect = RectF()
    private val path = Path()

    override fun onShow() { refreshIfStale() }
    override fun onTick() { refreshIfStale() }

    private fun refreshIfStale() {
        val w = weather
        if (fetching) return
        if (w != null && System.currentTimeMillis() - w.fetched < 15 * 60_000) return
        fetching = true
        Thread {
            try {
                val url = "https://api.open-meteo.com/v1/forecast?latitude=$lat&longitude=$lon" +
                    "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m" +
                    "&hourly=temperature_2m,precipitation_probability,weather_code" +
                    "&daily=temperature_2m_max,temperature_2m_min,sunrise,sunset&timezone=$tz&forecast_days=2"
                val body = http.newCall(Request.Builder().url(url).build()).execute().use { it.body?.string() ?: "" }
                weather = parse(JSONObject(body))
                lastError = null
            } catch (e: Exception) {
                Log.w("EchoPortal.Clock", "weather fetch failed: ${e.message}")
                lastError = e.message
                // retry sooner than the normal 15 min
                weather = weather?.copy(fetched = System.currentTimeMillis() - 13 * 60_000)
            } finally {
                fetching = false
                invalidate()
            }
        }.start()
    }

    private fun parse(o: JSONObject): Weather {
        val cur = o.getJSONObject("current")
        val hourly = o.getJSONObject("hourly")
        val times = hourly.getJSONArray("time")
        val temps = hourly.getJSONArray("temperature_2m")
        val rain = hourly.getJSONArray("precipitation_probability")
        val codes = hourly.getJSONArray("weather_code")
        val nowHour = LocalDateTime.now().hour
        val today = LocalDateTime.now().toLocalDate().toString()
        // find index of the current hour, then take the next 12
        var start = 0
        for (i in 0 until times.length()) {
            val t = times.getString(i)
            if (t.startsWith(today) && t.substring(11, 13).toInt() == nowHour) { start = i; break }
        }
        val hours = (start until minOf(start + 13, times.length())).map { i ->
            Hour(times.getString(i).substring(11, 13).toInt(), temps.getDouble(i), rain.optInt(i), codes.getInt(i))
        }
        val daily = o.getJSONObject("daily")
        return Weather(
            temp = cur.getDouble("temperature_2m"), feels = cur.getDouble("apparent_temperature"),
            code = cur.getInt("weather_code"), wind = cur.getDouble("wind_speed_10m"), humidity = cur.getInt("relative_humidity_2m"),
            hi = daily.getJSONArray("temperature_2m_max").getDouble(0), lo = daily.getJSONArray("temperature_2m_min").getDouble(0),
            sunrise = daily.getJSONArray("sunrise").getString(0).substring(11), sunset = daily.getJSONArray("sunset").getString(0).substring(11),
            hours = hours, fetched = System.currentTimeMillis(),
        )
    }

    override fun preview(): String {
        val w = weather ?: return "no weather yet"
        return fmt("%.0f°  %s  ·  %.0f° / %.0f°", w.temp, describe(w.code), w.hi, w.lo)
    }

    override fun bgLevel(page: Int): Int = 1

    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, page: Int, ambient: Boolean) {
        val now = LocalDateTime.now()
        val w = weather
        val night = isNight(now, w)

        if (ambient) {
            label(c, now.format(timeFmt), cx, cy + 4f * f, 96f * f, Draw.mutedColor, bold = true)
            label(c, now.format(dateFmt), cx, cy + 40f * f, 16f * f, Draw.gridColor)
            if (w != null) label(c, fmt("%.0f°  %s", w.temp, describe(w.code)), cx, cy + 72f * f, 18f * f, Draw.gridColor)
            return
        }

        if (page > 0) { styles[(page - 1).coerceIn(0, styles.size - 1)].draw(c, cx, cy, r, f, snapshot(now)); return }

        // rim: 12-hour temperature arc + rain ticks (from 7 o'clock round to 5 o'clock)
        if (w != null && w.hours.size >= 2) drawRim(c, cx, cy, r, f, w)

        // time
        label(c, now.format(timeFmt), cx - 14f * f, cy - 30f * f, 92f * f, Draw.textColor, bold = true)
        label(c, now.format(ampmFmt), cx + 118f * f, cy - 30f * f, 20f * f, Draw.mutedColor)
        label(c, now.format(dateFmt), cx, cy - 2f * f, 17f * f, Draw.mutedColor)

        if (w == null) {
            label(c, lastError?.let { "weather: $it" } ?: "fetching weather…", cx, cy + 60f * f, 14f * f, Draw.mutedColor)
            return
        }
        // current conditions
        drawWeatherIcon(c, cx - 96f * f, cy + 52f * f, 30f * f, w.code, night)
        label(c, fmt("%.0f°", w.temp), cx - 20f * f, cy + 66f * f, 52f * f, Draw.textColor, bold = true)
        label(c, describe(w.code), cx + 72f * f, cy + 44f * f, 16f * f, Draw.textColor)
        label(c, fmt("H %.0f°  L %.0f°", w.hi, w.lo), cx + 72f * f, cy + 66f * f, 14f * f, Draw.mutedColor)
        label(c, fmt("feels %.0f°  ·  %.0f km/h  ·  %d%%", w.feels, w.wind, w.humidity), cx, cy + 100f * f, 13f * f, Draw.mutedColor)
        label(c, "☀ ${w.sunrise}   ☽ ${w.sunset}", cx, cy + 122f * f, 13f * f, Draw.mutedColor)
        if (cpuPct != null || gpuTemp != null) {
            label(c, fmt("CPU %s   ·   GPU %s %s", cpuPct?.let { fmt("%.0f%%", it) } ?: "--", gpuUtil?.let { fmt("%.0f%%", it) } ?: "--", gpuTemp?.let { "$it°C" } ?: ""),
                cx, cy + 142f * f, 12f * f, Draw.cpuColor)
        }
    }

    /** Temperature line + rain ticks along an arc: hours 0..12 map to angles 210°..510° (7 o'clock round to 5). */
    private fun drawRim(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, w: Weather) {
        val hours = w.hours.take(13)
        val tMin = hours.minOf { it.temp }; val tMax = hours.maxOf { it.temp }
        val span = (tMax - tMin).coerceAtLeast(3.0)
        val rOuter = r - 20f * f; val rInner = r - 42f * f
        val a0 = 135.0; val a1 = 405.0   // 7:30 round the top to 4:30 - keeps clear of the panel cut at the bottom
        Draw.ring.strokeWidth = 1f * f; Draw.ring.color = Draw.gridColor
        rect.set(cx - rInner, cy - rInner, cx + rInner, cy + rInner)
        c.drawArc(rect, a0.toFloat(), (a1 - a0).toFloat(), false, Draw.ring)
        // smooth temperature curve: 8 sub-steps per hour, linearly interpolated.
        // Drawn as an area chart standing on the track circle (rInner) so it reads as a graph, not a ring.
        path.reset()
        val sub = 8
        val total = (hours.size - 1) * sub
        for (k in 0..total) {
            val i = (k / sub).coerceAtMost(hours.size - 2); val t = (k - i * sub) / sub.toFloat()
            val temp = hours[i].temp + (hours[i + 1].temp - hours[i].temp) * t
            val a = Math.toRadians(a0 + (a1 - a0) * k / total)
            val rr = rInner + 3f * f + (rOuter - rInner - 3f * f) * ((temp - tMin) / span).toFloat()
            val x = cx + (rr * cos(a)).toFloat(); val y = cy + (rr * sin(a)).toFloat()
            if (k == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
        // close the area back along the track
        val area = Path(path)
        rect.set(cx - rInner, cy - rInner, cx + rInner, cy + rInner)
        area.arcTo(rect, a1.toFloat(), (a0 - a1).toFloat(), false)
        area.close()
        Draw.fill.color = color; Draw.fill.alpha = 45
        c.drawPath(area, Draw.fill)
        Draw.fill.alpha = 255
        hours.forEachIndexed { i, h ->
            val a = Math.toRadians(a0 + (a1 - a0) * i / (hours.size - 1))
            // rain tick
            if (h.rainPct >= 20) {
                val len = 4f * f + 10f * f * h.rainPct / 100f
                Draw.line.strokeWidth = 3f * f; Draw.line.color = Draw.cpuColor
                c.drawLine(cx + ((rInner - 6f * f) * cos(a)).toFloat(), cy + ((rInner - 6f * f) * sin(a)).toFloat(),
                    cx + ((rInner - 6f * f - len) * cos(a)).toFloat(), cy + ((rInner - 6f * f - len) * sin(a)).toFloat(), Draw.line)
            }
            // hour labels every 3 h
            if (i % 3 == 0) {
                val lx = cx + ((rInner - 26f * f) * cos(a)).toFloat(); val ly = cy + ((rInner - 26f * f) * sin(a)).toFloat() + 4f * f
                label(c, if (i == 0) "now" else fmt("%d%s", if (h.hour % 12 == 0) 12 else h.hour % 12, if (h.hour < 12) "a" else "p"), lx, ly, 10f * f, Draw.mutedColor)
            }
        }
        Draw.line.strokeWidth = 2.5f * f; Draw.line.color = color
        c.drawPath(path, Draw.line)
        // high / low of the next 12 h, tucked just inside the arc ends
        label(c, fmt("%.0f°", tMax), cx - r * 0.46f, cy + r * 0.66f, 11f * f, color)
        label(c, fmt("%.0f°", tMin), cx + r * 0.46f, cy + r * 0.66f, 11f * f, Draw.cpuColor)
    }

    override fun drawIcon(c: Canvas, x: Float, y: Float, size: Float, selected: Boolean) {
        val col = if (selected) color else Draw.mutedColor
        Draw.ring.strokeWidth = size * 0.09f; Draw.ring.color = col
        c.drawCircle(x, y, size * 0.42f, Draw.ring)
        Draw.line.strokeWidth = size * 0.09f; Draw.line.color = col
        c.drawLine(x, y, x, y - size * 0.26f, Draw.line)
        c.drawLine(x, y, x + size * 0.18f, y + size * 0.1f, Draw.line)
    }

    // ---- weather helpers ------------------------------------------------------------
    private fun isNight(now: LocalDateTime, w: Weather?): Boolean {
        if (w == null) return now.hour < 6 || now.hour >= 21
        val hm = now.hour * 60 + now.minute
        fun m(s: String) = s.substring(0, 2).toInt() * 60 + s.substring(3, 5).toInt()
        return hm < m(w.sunrise) || hm > m(w.sunset)
    }

    private fun describe(code: Int): String = when (code) {
        0 -> "Clear"; 1 -> "Mostly clear"; 2 -> "Partly cloudy"; 3 -> "Overcast"
        45, 48 -> "Fog"; 51, 53, 55 -> "Drizzle"; 56, 57 -> "Freezing drizzle"
        61 -> "Light rain"; 63 -> "Rain"; 65 -> "Heavy rain"; 66, 67 -> "Freezing rain"
        71 -> "Light snow"; 73 -> "Snow"; 75 -> "Heavy snow"; 77 -> "Snow grains"
        80 -> "Light showers"; 81 -> "Showers"; 82 -> "Heavy showers"; 85, 86 -> "Snow showers"
        95 -> "Thunderstorm"; 96, 99 -> "Thunder + hail"; else -> "?"
    }

    /** Simple vector glyphs: sun/moon, cloud, rain, snow, fog, thunder. */
    private fun drawWeatherIcon(c: Canvas, x: Float, y: Float, s: Float, code: Int, night: Boolean) {
        val p = Draw.fill; p.alpha = 255
        when (code) {
            0, 1 -> if (night) moon(c, x, y, s) else sun(c, x, y, s)
            2 -> { if (night) moon(c, x - s * 0.3f, y - s * 0.3f, s * 0.6f) else sun(c, x - s * 0.3f, y - s * 0.3f, s * 0.6f); cloud(c, x + s * 0.1f, y + s * 0.2f, s * 0.8f) }
            3, 45, 48 -> { cloud(c, x, y, s); if (code != 3) { Draw.line.color = Draw.mutedColor; Draw.line.strokeWidth = s * 0.08f; for (i in 0..2) c.drawLine(x - s * 0.5f, y + s * 0.5f + i * s * 0.18f, x + s * 0.5f, y + s * 0.5f + i * s * 0.18f, Draw.line) } }
            in 51..67, in 80..82 -> { cloud(c, x, y - s * 0.15f, s); Draw.line.color = Draw.cpuColor; Draw.line.strokeWidth = s * 0.09f; for (i in -1..1) c.drawLine(x + i * s * 0.28f, y + s * 0.45f, x + i * s * 0.28f - s * 0.1f, y + s * 0.8f, Draw.line) }
            in 71..77, 85, 86 -> { cloud(c, x, y - s * 0.15f, s); p.color = Draw.textColor; for (i in -1..1) c.drawCircle(x + i * s * 0.28f, y + s * 0.62f, s * 0.07f, p) }
            else -> { cloud(c, x, y - s * 0.15f, s); Draw.line.color = color; Draw.line.strokeWidth = s * 0.1f; path.reset(); path.moveTo(x + s * 0.1f, y + s * 0.3f); path.lineTo(x - s * 0.1f, y + s * 0.6f); path.lineTo(x + s * 0.08f, y + s * 0.6f); path.lineTo(x - s * 0.1f, y + s * 0.95f); c.drawPath(path, Draw.line) }
        }
    }

    private fun sun(c: Canvas, x: Float, y: Float, s: Float) {
        Draw.fill.color = color; c.drawCircle(x, y, s * 0.42f, Draw.fill)
        Draw.line.color = color; Draw.line.strokeWidth = s * 0.08f
        for (i in 0 until 8) {
            val a = Math.toRadians(i * 45.0)
            c.drawLine(x + (s * 0.58f * cos(a)).toFloat(), y + (s * 0.58f * sin(a)).toFloat(), x + (s * 0.8f * cos(a)).toFloat(), y + (s * 0.8f * sin(a)).toFloat(), Draw.line)
        }
    }

    private fun moon(c: Canvas, x: Float, y: Float, s: Float) {
        Draw.fill.color = Draw.textColor; c.drawCircle(x, y, s * 0.42f, Draw.fill)
        Draw.fill.color = Color.BLACK; c.drawCircle(x + s * 0.2f, y - s * 0.15f, s * 0.36f, Draw.fill)
    }

    private fun cloud(c: Canvas, x: Float, y: Float, s: Float) {
        Draw.fill.color = Draw.mutedColor
        c.drawCircle(x - s * 0.25f, y + s * 0.05f, s * 0.28f, Draw.fill)
        c.drawCircle(x + s * 0.05f, y - s * 0.12f, s * 0.36f, Draw.fill)
        c.drawCircle(x + s * 0.35f, y + s * 0.08f, s * 0.26f, Draw.fill)
        c.drawRect(x - s * 0.25f, y + s * 0.05f, x + s * 0.35f, y + s * 0.33f, Draw.fill)
    }
}
