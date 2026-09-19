package com.echoportal

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.BitmapShader
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Shader
import android.util.Base64
import com.echoportal.Draw.fmt
import com.echoportal.Draw.label
import org.json.JSONObject
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.min

/** "Sky": a generated picture of today's weather filling the circle, with the conditions overlaid. Tap = new picture. */
class WeatherArtFace(ctx: Context) : Face(ctx) {
    override val id = "sky"
    override val name = "Sky"
    override val color = Color.parseColor("#4FC3F7")
    override val channels = setOf("weatherart")

    private var image: Bitmap? = null
    private var prevImage: Bitmap? = null          // cross-fade source
    private var fadeStart = 0L
    private val fadeMs = 6000L
    private var temp: Double? = null
    private var hi: Double? = null
    private var lo: Double? = null
    private var condition = ""
    private var error: String? = null
    private var generatedAt = 0L
    private var requestedAt = 0L
    private val imgPaint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val gradPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val timeFmt = DateTimeFormatter.ofPattern("h:mm a", Locale.US)

    override fun onEvent(channel: String, data: JSONObject) {
        temp = if (data.isNull("temp")) null else data.optDouble("temp")
        hi = if (data.isNull("hi")) null else data.optDouble("hi")
        lo = if (data.isNull("lo")) null else data.optDouble("lo")
        condition = data.optString("condition", "")
        error = if (data.isNull("error")) null else data.optString("error")
        generatedAt = data.optLong("generated", 0L)
        val b64 = if (data.isNull("image")) null else data.optString("image")
        if (!b64.isNullOrEmpty()) {
            try {
                val bytes = Base64.decode(b64.substringAfter(","), Base64.DEFAULT)
                val next = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                if (next != null && image != null && next !== image) { prevImage = image; fadeStart = System.currentTimeMillis() }
                if (next != null) image = next
                requestedAt = 0L
            } catch (_: Exception) { }
        }
        invalidate()
    }

    override fun bgLevel(page: Int): Int = 0

    override fun preview(): String = if (image == null) "no picture yet" else fmt("%s  %s", temp?.let { fmt("%.0f°", it) } ?: "", condition)

    /** Tap: another picture of the same weather from the library (instant, no GPU). */
    override fun onTap(x: Float, y: Float, cx: Float, cy: Float, r: Float, page: Int): Boolean {
        send(JSONObject().put("ch", "weatherart").put("data", JSONObject().put("op", "next")))
        return true
    }

    override fun onTick() { if (requestedAt > 0) invalidate() }

    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, page: Int, ambient: Boolean) {
        val bmp = image
        if (bmp != null) {
            val t = if (fadeStart > 0L) ((System.currentTimeMillis() - fadeStart).toFloat() / fadeMs).coerceIn(0f, 1f) else 1f
            val prev = prevImage
            if (prev != null && t < 1f) { drawPicture(c, prev, cx, cy, r, 255); drawPicture(c, bmp, cx, cy, r, (255 * t).toInt()); invalidate() }
            else { drawPicture(c, bmp, cx, cy, r, 255); if (prev != null) { prevImage = null; fadeStart = 0L } }
            // darken the bottom for the overlay text
            gradPaint.shader = LinearGradient(0f, cy + r * 0.1f, 0f, cy + r, Color.TRANSPARENT, Color.argb(210, 0, 0, 0), Shader.TileMode.CLAMP)
            c.drawCircle(cx, cy, r, gradPaint)
            gradPaint.shader = LinearGradient(0f, cy - r, 0f, cy - r * 0.55f, Color.argb(150, 0, 0, 0), Color.TRANSPARENT, Shader.TileMode.CLAMP)
            c.drawCircle(cx, cy, r, gradPaint)
        } else {
            Draw.header(c, cx, cy, f, "Sky", error ?: "painting today's weather…", color)
        }
        if (bmp != null) label(c, LocalDateTime.now().format(timeFmt), cx, cy - r * 0.72f, 16f * f, Color.WHITE)
        if (temp != null) label(c, fmt("%.0f°", temp), cx, cy + r * 0.62f, 60f * f, Color.WHITE, bold = true)
        label(c, condition.replaceFirstChar { it.uppercase() }, cx, cy + r * 0.72f + 6f * f, 16f * f, Color.WHITE)
        if (hi != null && lo != null) label(c, fmt("H %.0f°   L %.0f°", hi, lo), cx, cy + r * 0.82f + 4f * f, 12f * f, Color.argb(220, 255, 255, 255))
        if (requestedAt > 0) label(c, "painting a new one…", cx, cy - r * 0.58f, 11f * f, color)
    }

    private fun drawPicture(c: Canvas, bmp: Bitmap, cx: Float, cy: Float, r: Float, alpha: Int) {
        val scale = (2 * r) / min(bmp.width, bmp.height)
        val m = Matrix().apply { setScale(scale, scale); postTranslate(cx - bmp.width * scale / 2f, cy - bmp.height * scale / 2f) }
        imgPaint.shader = BitmapShader(bmp, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP).apply { setLocalMatrix(m) }
        imgPaint.alpha = alpha
        c.drawCircle(cx, cy, r, imgPaint)
        imgPaint.shader = null; imgPaint.alpha = 255
    }

    override fun drawIcon(c: Canvas, x: Float, y: Float, size: Float, selected: Boolean) {
        val col = if (selected) color else Draw.mutedColor
        Draw.fill.color = col; Draw.fill.alpha = 255
        c.drawCircle(x - size * 0.18f, y - size * 0.14f, size * 0.16f, Draw.fill)     // sun
        Draw.fill.color = if (selected) Color.WHITE else Draw.textColor
        c.drawCircle(x + size * 0.05f, y + size * 0.1f, size * 0.2f, Draw.fill)        // cloud
        c.drawCircle(x + size * 0.28f, y + size * 0.14f, size * 0.16f, Draw.fill)
        c.drawCircle(x - size * 0.16f, y + size * 0.16f, size * 0.15f, Draw.fill)
        c.drawRect(x - size * 0.16f, y + size * 0.16f, x + size * 0.28f, y + size * 0.3f, Draw.fill)
    }
}
