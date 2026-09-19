package com.echoportal

import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Typeface
import java.util.Locale

/** Shared paints, palette and drawing primitives used by every page. */
object Draw {
    val cpuColor = Color.parseColor("#4FC3F7")
    val ramColor = Color.parseColor("#FFB74D")
    val gpuColor = Color.parseColor("#CE93D8")
    val diskColor = Color.parseColor("#81C784")
    val netColor = Color.parseColor("#F06292")
    val dimColor = Color.parseColor("#1E262E")
    val gridColor = Color.parseColor("#263238")
    val textColor = Color.parseColor("#ECEFF1")
    val mutedColor = Color.parseColor("#78909C")
    val hotColor = Color.parseColor("#EF5350")

    val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND }
    val line = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 2f; strokeJoin = Paint.Join.ROUND }
    val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    val grid = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 1f; color = gridColor }
    val text = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = textColor; textAlign = Paint.Align.CENTER }
    val mono = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = textColor; typeface = Typeface.MONOSPACE }

    private val rect = RectF()
    private val path = Path()

    fun fmt(f: String, vararg a: Any?) = String.format(Locale.US, f, *a)

    fun label(c: Canvas, s: String, x: Float, y: Float, size: Float, color: Int = textColor,
              align: Paint.Align = Paint.Align.CENTER, bold: Boolean = false) {
        text.color = color; text.textSize = size; text.textAlign = align
        text.typeface = if (bold) Typeface.DEFAULT_BOLD else Typeface.DEFAULT
        c.drawText(s, x, y, text)
    }

    fun monoLabel(c: Canvas, s: String, x: Float, y: Float, size: Float, color: Int = textColor,
                  align: Paint.Align = Paint.Align.LEFT) {
        mono.color = color; mono.textSize = size; mono.textAlign = align
        c.drawText(s, x, y, mono)
    }

    /** Page header: title at the top of the circle, optional subtitle under it. */
    fun header(c: Canvas, cx: Float, cy: Float, f: Float, title: String, sub: String?, color: Int) {
        label(c, title, cx, cy - 158f * f, 22f * f, color, bold = true)
        if (sub != null) label(c, sub, cx, cy - 138f * f, 12f * f, mutedColor)
    }

    fun ringArc(c: Canvas, cx: Float, cy: Float, radius: Float, width: Float, pct: Double, color: Int, alarmAbove90: Boolean = true) {
        rect.set(cx - radius, cy - radius, cx + radius, cy + radius)
        ring.strokeWidth = width
        ring.color = dimColor
        c.drawArc(rect, -90f, 360f, false, ring)
        ring.color = if (alarmAbove90 && pct > 90) hotColor else color
        c.drawArc(rect, -90f, (360.0 * pct / 100.0).toFloat().coerceIn(0f, 360f), false, ring)
    }

    /**
     * Task-Manager-style history graph: grid, filled area, line. Values are scaled to [0, max].
     * If max <= 0 the series' own maximum is used (auto-scale, e.g. for network Mbps).
     */
    fun graph(c: Canvas, l: Float, t: Float, r: Float, b: Float, series: FloatArray, color: Int, max: Float = 100f) {
        // frame + grid
        line.color = gridColor; line.strokeWidth = 1.5f
        c.drawRect(l, t, r, b, line)
        val w = r - l; val h = b - t
        for (i in 1 until 6) c.drawLine(l + w * i / 6f, t, l + w * i / 6f, b, grid)
        for (i in 1 until 4) c.drawLine(l, t + h * i / 4f, r, t + h * i / 4f, grid)

        var top = max
        if (top <= 0f) {
            top = 0f
            for (v in series) if (!v.isNaN() && v > top) top = v
            top = if (top <= 0f) 1f else top * 1.15f
        }
        val n = series.size
        path.reset()
        var started = false
        var lastX = l
        for (i in 0 until n) {
            val v = series[i]
            if (v.isNaN()) continue
            val x = l + w * i / (n - 1).toFloat()
            val y = b - h * (v / top).coerceIn(0f, 1f)
            if (!started) { path.moveTo(x, b); path.lineTo(x, y); started = true } else path.lineTo(x, y)
            lastX = x
        }
        if (!started) return
        path.lineTo(lastX, b)
        path.close()
        fill.color = color; fill.alpha = 60
        c.drawPath(path, fill)

        path.reset(); started = false
        for (i in 0 until n) {
            val v = series[i]
            if (v.isNaN()) continue
            val x = l + w * i / (n - 1).toFloat()
            val y = b - h * (v / top).coerceIn(0f, 1f)
            if (!started) { path.moveTo(x, y); started = true } else path.lineTo(x, y)
        }
        line.color = color; line.strokeWidth = 2f
        c.drawPath(path, line)
    }

    /** Horizontal usage bar with a label on the left and a value on the right. */
    fun bar(c: Canvas, l: Float, y: Float, r: Float, h: Float, pct: Double, color: Int) {
        fill.color = dimColor; fill.alpha = 255
        c.drawRoundRect(l, y, r, y + h, h / 2, h / 2, fill)
        fill.color = if (pct > 90) hotColor else color
        val w = ((r - l) * pct / 100.0).toFloat().coerceIn(0f, r - l)
        if (w > 0) c.drawRoundRect(l, y, l + w, y + h, h / 2, h / 2, fill)
    }

    fun uptime(sec: Long): String {
        val d = sec / 86400; val h = (sec % 86400) / 3600; val m = (sec % 3600) / 60
        return if (d > 0) "${d}d ${h}h" else "${h}h ${m}m"
    }

    fun rate(mbps: Double): String = when {
        mbps >= 1000 -> fmt("%.2f Gbps", mbps / 1000)
        mbps >= 1 -> fmt("%.1f Mbps", mbps)
        else -> fmt("%.0f Kbps", mbps * 1000)
    }
}
