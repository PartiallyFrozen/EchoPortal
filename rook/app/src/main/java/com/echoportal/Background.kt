package com.echoportal

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.BitmapShader
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Shader
import android.util.Base64
import org.json.JSONObject
import java.io.File

/**
 * Global background behind every face: black, a solid colour, today's Sky painting, or a picture sent
 * from the PC (tray app -> agent "bg" channel, kept in filesDir/background.jpg).
 *
 * Pictures are defocused according to how busy the face is (Face.bgLevel):
 *   0 = the face owns the whole circle (Sky) - nothing drawn
 *   1 = light content (clock faces)         - soft blur, light scrim
 *   2 = dense content (monitors, lists)     - heavy blur, dark scrim so text stays readable
 */
class Background(private val ctx: Context, private val settings: ShellSettings) {
    companion object {
        const val BLACK = "black"; const val COLOR = "color"; const val SKY = "sky"; const val IMAGE = "image"
        val PRESETS = listOf(
            "Midnight" to 0xFF0B1E3A.toInt(), "Ocean" to 0xFF06283D.toInt(), "Slate" to 0xFF1C2B3A.toInt(), "Charcoal" to 0xFF1F2326.toInt(),
            "Forest" to 0xFF0F2A1F.toInt(), "Plum" to 0xFF2A1436.toInt(), "Wine" to 0xFF3A0F1E.toInt(), "Rust" to 0xFF3A1E0F.toInt(),
        )
        private const val SMALL = 96          // blur working size (px); upscaled with bilinear filtering
    }

    var invalidate: () -> Unit = {}
    private var sky: Bitmap? = null
    private var prevSky: Bitmap? = null            // cross-fade source when the sky picture changes
    private var fadeStart = 0L
    private val fadeMs = 6000L
    private var skyGen = 0                         // bumps per sky picture so blurred copies of old/new are cached apart
    private var user: Bitmap? = null
    private val cache = HashMap<String, Bitmap>()
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val file get() = File(ctx.filesDir, "background.jpg")

    init { if (file.exists()) user = BitmapFactory.decodeFile(file.path) }

    val mode: String get() = settings.bgMode
    val hasSky: Boolean get() = sky != null
    val hasUser: Boolean get() = user != null

    fun set(mode: String, color: Int = settings.bgColor) {
        settings.bgMode = mode; settings.bgColor = color; invalidate()
    }

    fun describe(): String = when (mode) {
        COLOR -> "colour · " + (PRESETS.firstOrNull { it.second == settings.bgColor }?.first ?: "custom")
        SKY -> if (hasSky) "sky painting" else "sky painting (waiting for the PC)"
        IMAGE -> if (hasUser) "your picture" else "your picture (none sent yet)"
        else -> "black"
    }

    fun onEvent(ch: String, data: JSONObject) {
        when (ch) {
            "weatherart" -> decode(data)?.let { next ->
                if (sky != null) { prevSky = sky; fadeStart = System.currentTimeMillis() } else drop("sky")
                drop("sky${skyGen - 1}-"); skyGen++; sky = next; invalidate()
            }
            "bg" -> {
                val push = data.optBoolean("push", false)
                if (data.isNull("image")) {
                    if (push) { user = null; file.delete(); drop("user-"); if (mode == IMAGE) settings.bgMode = BLACK; invalidate() }
                    return
                }
                val b64 = data.optString("image"); if (b64.isEmpty()) return
                try {
                    val bytes = Base64.decode(b64.substringAfter(","), Base64.DEFAULT)
                    val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return
                    user = bmp; file.writeBytes(bytes); drop("user-")
                    if (push) settings.bgMode = IMAGE
                    invalidate()
                } catch (_: Exception) { }
            }
        }
    }

    private fun decode(data: JSONObject): Bitmap? {
        if (data.isNull("image")) return null
        val b64 = data.optString("image"); if (b64.isEmpty()) return null
        return try {
            val bytes = Base64.decode(b64.substringAfter(","), Base64.DEFAULT)
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
        } catch (_: Exception) { null }
    }

    private fun drop(prefix: String) { cache.keys.filter { it.startsWith(prefix) }.forEach { cache.remove(it) } }

    fun draw(c: Canvas, cx: Float, cy: Float, r: Float, level: Int) {
        if (level <= 0) return
        when (mode) {
            COLOR -> { fill.color = settings.bgColor; fill.alpha = 255; c.drawCircle(cx, cy, r, fill) }
            SKY, IMAGE -> {
                val src = (if (mode == SKY) sky else user) ?: return
                val key = if (mode == SKY) "sky$skyGen-" else "user-"
                val prev = if (mode == SKY) prevSky else null
                val t = if (fadeStart > 0L) ((System.currentTimeMillis() - fadeStart).toFloat() / fadeMs).coerceIn(0f, 1f) else 1f
                if (prev != null && t < 1f) {
                    drawBlurred(c, blurred("sky${skyGen - 1}-", prev, level), cx, cy, r, 255)
                    drawBlurred(c, blurred(key, src, level), cx, cy, r, (255 * t).toInt())
                    invalidate()
                } else {
                    if (prev != null) { prevSky = null; fadeStart = 0L; drop("sky${skyGen - 1}-") }
                    drawBlurred(c, blurred(key, src, level), cx, cy, r, 255)
                }
                fill.color = Color.BLACK; fill.alpha = if (level >= 2) 150 else 100
                c.drawCircle(cx, cy, r, fill)
            }
        }
    }

    private fun drawBlurred(c: Canvas, bmp: Bitmap, cx: Float, cy: Float, r: Float, alpha: Int) {
        val scale = 2f * r / bmp.width
        paint.shader = BitmapShader(bmp, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP).apply {
            setLocalMatrix(Matrix().apply { setScale(scale, scale); postTranslate(cx - r, cy - r) })
        }
        paint.alpha = alpha
        c.drawCircle(cx, cy, r, paint)
        paint.shader = null; paint.alpha = 255
    }

    /** Centre-cropped, downscaled and box-blurred (3 passes ~ gaussian) copy of src; cached per source and level. */
    private fun blurred(key: String, src: Bitmap, level: Int): Bitmap {
        val ck = "$key$level"
        cache[ck]?.let { return it }
        val side = minOf(src.width, src.height)
        val sq = Bitmap.createBitmap(src, (src.width - side) / 2, (src.height - side) / 2, side, side)
        val small = Bitmap.createScaledBitmap(sq, SMALL, SMALL, true)
        val px = IntArray(SMALL * SMALL)
        small.getPixels(px, 0, SMALL, 0, 0, SMALL, SMALL)
        val radius = if (level >= 2) 9 else 3
        val tmp = IntArray(px.size)
        repeat(3) { boxBlur(px, tmp, SMALL, SMALL, radius); boxBlur(tmp, px, SMALL, SMALL, radius, vertical = true) }
        val out = Bitmap.createBitmap(SMALL, SMALL, Bitmap.Config.ARGB_8888)
        out.setPixels(px, 0, SMALL, 0, 0, SMALL, SMALL)
        cache[ck] = out
        return out
    }

    private fun boxBlur(src: IntArray, dst: IntArray, w: Int, h: Int, radius: Int, vertical: Boolean = false) {
        val n = 2 * radius + 1
        val lines = if (vertical) w else h
        val len = if (vertical) h else w
        for (l in 0 until lines) {
            var rs = 0; var gs = 0; var bs = 0
            fun at(i: Int) = if (vertical) src[i.coerceIn(0, len - 1) * w + l] else src[l * w + i.coerceIn(0, len - 1)]
            for (i in -radius..radius) { val p = at(i); rs += (p shr 16) and 255; gs += (p shr 8) and 255; bs += p and 255 }
            for (i in 0 until len) {
                val o = if (vertical) i * w + l else l * w + i
                dst[o] = (255 shl 24) or ((rs / n) shl 16) or ((gs / n) shl 8) or (bs / n)
                val pIn = at(i + radius + 1); val pOut = at(i - radius)
                rs += ((pIn shr 16) and 255) - ((pOut shr 16) and 255)
                gs += ((pIn shr 8) and 255) - ((pOut shr 8) and 255)
                bs += (pIn and 255) - (pOut and 255)
            }
        }
    }
}
