package com.echoportal

import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import com.echoportal.Draw.fmt
import com.echoportal.Draw.label
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.cos
import kotlin.math.sin

/** Everything a clock style may show. */
data class ClockData(
    val now: LocalDateTime,
    val tempC: Double?, val condition: String?, val weatherCode: Int?, val night: Boolean,
    val hi: Double?, val lo: Double?,
    val cpuPct: Double?, val gpuUtil: Double?, val gpuTemp: Int?,
    val btcPrice: Double?, val btcChange: Double?, val btcCurrency: String,
    val jobsToday: Int?,
)

/** A watch-face style. All draw into a circle of radius r at (cx, cy); f = r / 240. */
interface ClockStyle {
    val name: String
    fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, d: ClockData)
}

/** Shared drawing helpers for the styles. */
object Dial {
    private val rect = RectF()
    private val dateFmt = DateTimeFormatter.ofPattern("EEE", Locale.US)
    private val monFmt = DateTimeFormatter.ofPattern("MMM", Locale.US)

    fun angle(deg: Double) = Math.toRadians(deg - 90.0)
    fun px(cx: Float, rr: Float, deg: Double) = cx + (rr * cos(angle(deg))).toFloat()
    fun py(cy: Float, rr: Float, deg: Double) = cy + (rr * sin(angle(deg))).toFloat()

    /** Minute ticks (60) with longer hour ticks. */
    fun ticks(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, rOuter: Float, minorLen: Float, majorLen: Float,
              minorColor: Int, majorColor: Int, minorW: Float = 1.5f, majorW: Float = 3f, skipHours: Boolean = false) {
        for (i in 0 until 60) {
            val major = i % 5 == 0
            if (major && skipHours) continue
            val deg = i * 6.0
            Draw.line.color = if (major) majorColor else minorColor
            Draw.line.strokeWidth = (if (major) majorW else minorW) * f
            val len = if (major) majorLen else minorLen
            c.drawLine(px(cx, rOuter, deg), py(cy, rOuter, deg), px(cx, rOuter - len, deg), py(cy, rOuter - len, deg), Draw.line)
        }
    }

    fun numerals(c: Canvas, cx: Float, cy: Float, rr: Float, f: Float, size: Float, color: Int, only: Set<Int>? = null, bold: Boolean = false) {
        for (h in 1..12) {
            if (only != null && h !in only) continue
            val deg = h * 30.0
            label(c, h.toString(), px(cx, rr, deg), py(cy, rr, deg) + size * 0.36f, size, color, bold = bold)
        }
    }

    fun hand(c: Canvas, cx: Float, cy: Float, deg: Double, length: Float, width: Float, color: Int, tail: Float = 0f, cap: Paint.Cap = Paint.Cap.ROUND) {
        Draw.line.color = color; Draw.line.strokeWidth = width; Draw.line.strokeCap = cap
        c.drawLine(px(cx, -tail, deg), py(cy, -tail, deg), px(cx, length, deg), py(cy, length, deg), Draw.line)
        Draw.line.strokeCap = Paint.Cap.ROUND
    }

    /** Hour/minute/second hands. Seconds step once a second (quartz feel). */
    fun hands(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, now: LocalDateTime,
              hourColor: Int, minuteColor: Int, secondColor: Int?, hourW: Float = 12f, minuteW: Float = 8f,
              hourLen: Float = 0.50f, minuteLen: Float = 0.74f, hubColor: Int = hourColor) {
        val h = (now.hour % 12) + now.minute / 60.0
        val m = now.minute + now.second / 60.0
        hand(c, cx, cy, h * 30.0, r * hourLen, hourW * f, hourColor, tail = 14f * f)
        hand(c, cx, cy, m * 6.0, r * minuteLen, minuteW * f, minuteColor, tail = 14f * f)
        if (secondColor != null) {
            hand(c, cx, cy, now.second * 6.0, r * 0.86f, 2f * f, secondColor, tail = 28f * f)
        }
        Draw.fill.color = hubColor; Draw.fill.alpha = 255
        c.drawCircle(cx, cy, 7f * f, Draw.fill)
        Draw.fill.color = Color.BLACK
        c.drawCircle(cx, cy, 2.5f * f, Draw.fill)
    }

    /** Round complication: outline (optionally partially filled as a gauge), a title line and a value line. */
    fun comp(c: Canvas, x: Float, y: Float, rad: Float, f: Float, title: String?, value: String, color: Int,
             gaugePct: Double? = null, textColor: Int = Draw.textColor, big: Float = 20f, small: Float = 10f) {
        Draw.ring.strokeWidth = 3f * f; Draw.ring.color = Draw.dimColor
        c.drawCircle(x, y, rad, Draw.ring)
        if (gaugePct != null) {
            Draw.ring.color = color
            rect.set(x - rad, y - rad, x + rad, y + rad)
            c.drawArc(rect, -90f, (360.0 * gaugePct / 100.0).toFloat().coerceIn(0f, 360f), false, Draw.ring)
        } else {
            Draw.ring.color = color; Draw.ring.alpha = 140
            c.drawCircle(x, y, rad, Draw.ring); Draw.ring.alpha = 255
        }
        if (title != null) {
            label(c, title, x, y - rad * 0.22f, small * f, Draw.mutedColor)
            label(c, value, x, y + rad * 0.40f, big * f, textColor, bold = true)
        } else {
            label(c, value, x, y + big * 0.36f * f, big * f, textColor, bold = true)
        }
    }

    fun dayName(d: LocalDateTime): String = d.format(dateFmt).uppercase(Locale.US)
    fun monName(d: LocalDateTime): String = d.format(monFmt).uppercase(Locale.US)

    fun temp(d: ClockData) = d.tempC?.let { fmt("%.0f°", it) } ?: "--"
    fun btcShort(d: ClockData): String = d.btcPrice?.let { if (it >= 1000) fmt("%.0fK", it / 1000) else fmt("%.0f", it) } ?: "--"

    /** Small weather glyph (reuses the Clock face's icon drawing through a callback-free simple set). */
    fun weatherGlyph(c: Canvas, x: Float, y: Float, s: Float, code: Int?, night: Boolean, color: Int) {
        Draw.fill.alpha = 255
        when (code) {
            null -> {}
            0, 1 -> {
                if (night) { Draw.fill.color = color; c.drawCircle(x, y, s * 0.42f, Draw.fill); Draw.fill.color = Color.BLACK; c.drawCircle(x + s * 0.22f, y - s * 0.16f, s * 0.36f, Draw.fill) }
                else {
                    Draw.fill.color = color; c.drawCircle(x, y, s * 0.36f, Draw.fill)
                    Draw.line.color = color; Draw.line.strokeWidth = s * 0.08f
                    for (i in 0 until 8) { val a = Math.toRadians(i * 45.0); c.drawLine(x + (s * 0.5f * cos(a)).toFloat(), y + (s * 0.5f * sin(a)).toFloat(), x + (s * 0.72f * cos(a)).toFloat(), y + (s * 0.72f * sin(a)).toFloat(), Draw.line) }
                }
            }
            else -> {  // cloud (with rain lines for wet codes)
                Draw.fill.color = color
                c.drawCircle(x - s * 0.25f, y + s * 0.05f, s * 0.28f, Draw.fill)
                c.drawCircle(x + s * 0.05f, y - s * 0.12f, s * 0.36f, Draw.fill)
                c.drawCircle(x + s * 0.35f, y + s * 0.08f, s * 0.26f, Draw.fill)
                c.drawRect(x - s * 0.25f, y + s * 0.05f, x + s * 0.35f, y + s * 0.33f, Draw.fill)
                if (code in 51..67 || code in 80..82 || code >= 95) {
                    Draw.line.color = Draw.cpuColor; Draw.line.strokeWidth = s * 0.08f
                    for (i in -1..1) c.drawLine(x + i * s * 0.25f, y + s * 0.45f, x + i * s * 0.25f - s * 0.08f, y + s * 0.72f, Draw.line)
                }
            }
        }
    }
}

// ------------------------------------------------------------------------------------------------
/** Classic analog: all twelve numerals, minute ticks, three hands, weather / date / GPU dials. */
class ClassicStyle : ClockStyle {
    override val name = "Classic"
    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, d: ClockData) {
        Dial.ticks(c, cx, cy, r, f, r * 0.965f, 8f * f, 16f * f, Draw.mutedColor, Draw.textColor)
        Dial.numerals(c, cx, cy, r * 0.80f, f, 22f * f, Draw.textColor)
        val rr = r * 0.36f; val rad = 50f * f
        // 9 o'clock: weather, 3 o'clock: date, 6 o'clock: GPU gauge, 12 o'clock: CPU gauge
        Dial.weatherGlyph(c, cx - rr, cy - 18f * f, 18f * f, d.weatherCode, d.night, Draw.mutedColor)
        Dial.comp(c, cx - rr, cy, rad, f, null, Dial.temp(d), Draw.mutedColor, big = 24f)
        Dial.comp(c, cx + rr, cy, rad, f, Dial.monName(d.now), d.now.dayOfMonth.toString(), Draw.mutedColor, big = 26f)
        Dial.comp(c, cx, cy + rr, rad, f, "GPU " + (d.gpuUtil?.let { fmt("%.0f%%", it) } ?: ""), d.gpuTemp?.let { "$it°" } ?: "--", Draw.gpuColor, gaugePct = d.gpuUtil, big = 24f, small = 11f)
        Dial.comp(c, cx, cy - rr, rad, f, "CPU", d.cpuPct?.let { fmt("%.0f%%", it) } ?: "--", Draw.cpuColor, gaugePct = d.cpuPct, big = 24f, small = 11f)
        Dial.hands(c, cx, cy, r, f, d.now, Draw.textColor, Draw.textColor, Draw.mutedColor)
    }
}

/** Bold minimal: huge 12 and 6, thick hands, day and date dials (image 2). */
class BoldStyle : ClockStyle {
    override val name = "Bold"
    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, d: ClockData) {
        Dial.ticks(c, cx, cy, r, f, r * 0.97f, 10f * f, 24f * f, Draw.mutedColor, Draw.textColor, minorW = 2f, majorW = 5f)
        label(c, "12", cx, cy - r * 0.50f, 84f * f, Draw.textColor, bold = true)
        label(c, "6", cx, cy + r * 0.86f, 84f * f, Draw.textColor, bold = true)
        val rad = 40f * f
        Dial.comp(c, cx - r * 0.42f, cy, rad, f, null, Dial.dayName(d.now), Draw.textColor, big = 18f)
        Dial.comp(c, cx + r * 0.42f, cy, rad, f, Dial.monName(d.now), d.now.dayOfMonth.toString(), Draw.textColor, big = 22f)
        Dial.comp(c, cx - r * 0.30f, cy + r * 0.38f, 32f * f, f, "CPU", d.cpuPct?.let { fmt("%.0f%%", it) } ?: "--", Draw.cpuColor, gaugePct = d.cpuPct, big = 16f, small = 9f)
        Dial.comp(c, cx + r * 0.30f, cy + r * 0.38f, 32f * f, f, "GPU", d.gpuTemp?.let { "$it°" } ?: "--", Draw.gpuColor, gaugePct = d.gpuUtil, big = 16f, small = 9f)
        label(c, Dial.temp(d) + "  ·  " + (d.condition ?: ""), cx, cy + r * 0.60f, 11f * f, Draw.mutedColor)
        Dial.hands(c, cx, cy, r, f, d.now, Draw.textColor, Draw.textColor, null, hourW = 16f, minuteW = 11f, hourLen = 0.42f, minuteLen = 0.66f)
    }
}

/** Pixel-Watch-like: numerals, accent tick ring, translucent band with weather / BTC / GPU, red seconds (image 3). */
class PixelStyle : ClockStyle {
    override val name = "Pixel"
    private val accent = Color.parseColor("#FFC107")
    private val band = Color.parseColor("#332A00")
    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, d: ClockData) {
        Dial.ticks(c, cx, cy, r, f, r * 0.965f, 6f * f, 12f * f, accent, accent, minorW = 1.5f, majorW = 2.5f, skipHours = true)
        Dial.numerals(c, cx, cy, r * 0.83f, f, 24f * f, Draw.textColor)
        // translucent band
        Draw.ring.strokeWidth = r * 0.34f; Draw.ring.color = band
        c.drawCircle(cx, cy, r * 0.50f, Draw.ring)
        val rad = 34f * f
        // dials in the band: weather (10 o'clock), BTC (9 o'clock lower), GPU (6 o'clock), date (12 o'clock)
        Dial.weatherGlyph(c, Dial.px(cx, r * 0.5f, 300.0), Dial.py(cy, r * 0.5f, 300.0) - 12f * f, 14f * f, d.weatherCode, d.night, accent)
        label(c, Dial.temp(d), Dial.px(cx, r * 0.5f, 300.0), Dial.py(cy, r * 0.5f, 300.0) + 18f * f, 14f * f, accent, bold = true)
        Dial.comp(c, Dial.px(cx, r * 0.5f, 240.0), Dial.py(cy, r * 0.5f, 240.0), rad, f, "BTC", Dial.btcShort(d), accent, big = 16f)
        Dial.comp(c, cx, cy + r * 0.5f, rad, f, "GPU", d.gpuTemp?.let { "$it°" } ?: "--", accent, gaugePct = d.gpuUtil, big = 16f)
        Dial.comp(c, Dial.px(cx, r * 0.5f, 120.0), Dial.py(cy, r * 0.5f, 120.0), rad, f, "CPU", d.cpuPct?.let { fmt("%.0f%%", it) } ?: "--", accent, gaugePct = d.cpuPct, big = 16f)
        Dial.comp(c, cx, cy - r * 0.5f, rad, f, Dial.monName(d.now), d.now.dayOfMonth.toString(), accent, big = 20f)
        label(c, d.now.format(DateTimeFormatter.ofPattern("h:mm", Locale.US)), Dial.px(cx, r * 0.5f, 60.0), Dial.py(cy, r * 0.5f, 60.0) + 5f * f, 15f * f, accent, bold = true)
        Dial.hands(c, cx, cy, r, f, d.now, Draw.textColor, Draw.textColor, Color.parseColor("#E53935"), hourW = 10f, minuteW = 7f)
    }
}

/** Neon digital: large time, date, glowing progress arcs (day and hour), two dials (image 4). */
class NeonStyle : ClockStyle {
    override val name = "Neon"
    private val cyan = Color.parseColor("#26C6DA")
    private val teal = Color.parseColor("#00897B")
    private val rect = RectF()
    private val timeFmt = DateTimeFormatter.ofPattern("HH:mm", Locale.US)
    private val dateFmt = DateTimeFormatter.ofPattern("EEE, MMM d", Locale.US)
    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, d: ClockData) {
        // glow: wide faint ring under the arcs
        Draw.ring.strokeWidth = 26f * f; Draw.ring.color = cyan; Draw.ring.alpha = 40
        c.drawCircle(cx, cy, r * 0.84f, Draw.ring); Draw.ring.alpha = 255
        // outer arc = progress through the day, inner arc = progress through the hour
        val dayPct = (d.now.hour * 60 + d.now.minute) / 1440f
        val hourPct = (d.now.minute * 60 + d.now.second) / 3600f
        rect.set(cx - r * 0.88f, cy - r * 0.88f, cx + r * 0.88f, cy + r * 0.88f)
        Draw.ring.strokeWidth = 7f * f; Draw.ring.color = Draw.dimColor; c.drawArc(rect, -90f, 360f, false, Draw.ring)
        Draw.ring.color = cyan; c.drawArc(rect, -90f, 360f * dayPct, false, Draw.ring)
        rect.set(cx - r * 0.78f, cy - r * 0.78f, cx + r * 0.78f, cy + r * 0.78f)
        Draw.ring.strokeWidth = 5f * f; Draw.ring.color = Draw.dimColor; c.drawArc(rect, -90f, 360f, false, Draw.ring)
        Draw.ring.color = teal; c.drawArc(rect, -90f, 360f * hourPct, false, Draw.ring)

        label(c, d.now.format(timeFmt), cx, cy - 10f * f, 74f * f, Draw.textColor, bold = true)
        label(c, d.now.format(dateFmt), cx, cy + 18f * f, 16f * f, Draw.mutedColor)
        val rad = 30f * f
        Dial.weatherGlyph(c, cx - 48f * f, cy + 62f * f, 12f * f, d.weatherCode, d.night, cyan)
        label(c, Dial.temp(d), cx - 48f * f, cy + 90f * f, 14f * f, Draw.textColor, bold = true)
        Draw.ring.strokeWidth = 2f * f; Draw.ring.color = cyan; c.drawCircle(cx - 48f * f, cy + 72f * f, rad, Draw.ring)
        Draw.fill.color = Color.parseColor("#EC407A"); Draw.fill.alpha = 255
        c.drawCircle(cx + 48f * f, cy + 72f * f, rad, Draw.fill)
        label(c, Dial.btcShort(d), cx + 48f * f, cy + 78f * f, 15f * f, Color.WHITE, bold = true)
        label(c, d.btcChange?.let { fmt("%+.1f%%", it) } ?: "BTC", cx + 48f * f, cy + 72f * f + rad + 13f * f, 10f * f, Draw.mutedColor)
        Dial.comp(c, cx - 128f * f, cy + 72f * f, 26f * f, f, "CPU", d.cpuPct?.let { fmt("%.0f%%", it) } ?: "--", cyan, gaugePct = d.cpuPct, big = 13f, small = 8f)
        Dial.comp(c, cx + 128f * f, cy + 72f * f, 26f * f, f, "GPU", d.gpuTemp?.let { "$it°" } ?: "--", Draw.gpuColor, gaugePct = d.gpuUtil, big = 13f, small = 8f)
        label(c, "day", cx, cy - r * 0.84f + 5f * f, 9f * f, cyan)
    }
}
