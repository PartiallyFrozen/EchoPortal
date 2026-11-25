package com.echoportal

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RadialGradient
import android.graphics.Shader
import android.graphics.Typeface
import com.echoportal.Draw.label
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * Video clock. A short, muted clip served by the PC's media server loops under the shell's
 * punched-out circle, with the time and date on a soft scrim on top. If the PC is off (or no
 * media server is known) it degrades to a plain dark clock rather than a black screen.
 *
 * The clip is `media/fireplace.mp4` next to the agent by default; long-press -> Video clip...
 * names another file in the same folder, no rebuild needed.
 */
class VideoClockFace(ctx: Context) : Face(ctx) {
    override val id = "videoclock"
    override val name = "Video Clock"
    override val color = Color.parseColor("#FF8A65")

    /** e.g. `http://192.168.0.147:8766`; null while the PC is unreachable. */
    var mediaBase: String? = null
        set(v) { if (field == v) return; field = v; sync() }

    /** Live decoder state, so a failed/retrying clip never leaves an opaque black circle. */
    var player: VideoPlayer? = null
        set(value) {
            field = value
            value?.playbackSpeed = CLOCK_SPEED
            value?.zoom = CLOCK_ZOOM
        }

    /** File inside the agent's media/ folder; the long-press menu changes it on the Spot. */
    var clip: String = FILE
        set(v) {
            val t = v.trim().ifEmpty { FILE }
            if (field == t) return
            field = t
            sync()
        }

    /** Shared hub token, appended to the media URL because MediaPlayer cannot set headers. */
    var token: String = ""
        set(v) {
            val t = v.trim()
            if (field == t) return
            field = t
            sync()
        }

    private var visible = false
    private var showing = false

    private val timeFmt = DateTimeFormatter.ofPattern("h:mm", Locale.US)
    private val ampmFmt = DateTimeFormatter.ofPattern("a", Locale.US)
    private val dateFmt = DateTimeFormatter.ofPattern("EEEE, MMMM d", Locale.US)
    private val scrim = Paint(Paint.ANTI_ALIAS_FLAG)
    private val backdrop = Paint(Paint.ANTI_ALIAS_FLAG)

    private fun url(): String? {
        val base = mediaBase ?: return null
        val file = java.net.URLEncoder.encode(clip, "UTF-8").replace("+", "%20")
        val u = "$base/media/$file"
        return if (token.isEmpty()) u else "$u?token=" + java.net.URLEncoder.encode(token, "UTF-8")
    }

    override fun onShow() { visible = true; sync() }
    override fun onHide() { visible = false; sync() }
    override fun onPage(page: Int) { sync() }

    /**
     * Ask the shell for the video only while this face is actually on screen. A re-request
     * restarts the clip, which is cheap and makes wake-from-sleep reliable.
     */
    private fun sync() {
        val u = url()
        if (visible && u != null) {
            muteVideo(true)
            requestVideo(u)
            showing = true
        } else if (showing) {
            requestVideo(null)
            showing = false
        }
    }

    override fun preview(): String =
        if (mediaBase == null) "video clock  ·  PC off" else "video clock  ·  looping clip"

    override fun bgLevel(page: Int): Int = 0

    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, page: Int, ambient: Boolean) {
        val now = LocalDateTime.now()

        if (ambient) {
            label(c, now.format(timeFmt), cx, cy + 8f * f, 120f * f, Draw.mutedColor, bold = true)
            label(c, now.format(dateFmt), cx, cy + 50f * f, 24f * f, Draw.gridColor)
            return
        }

        // No decoder/frame yet: paint our own backdrop instead of leaving a black hole.
        val live = showing && player?.playing == true
        if (!live) {
            backdrop.shader = RadialGradient(
                cx, cy, r,
                intArrayOf(Color.parseColor("#2A1E46"), Color.parseColor("#140E26"), Color.parseColor("#050409")),
                floatArrayOf(0f, 0.62f, 1f), Shader.TileMode.CLAMP,
            )
            c.drawCircle(cx, cy, r, backdrop)
            backdrop.shader = null
        }

        // scrim: keep the clock readable over a bright fire, still show the video through it
        scrim.shader = LinearGradient(
            cx, cy - r, cx, cy + r,
            intArrayOf(Color.TRANSPARENT, 0x55000000, 0xCC000000.toInt()),
            floatArrayOf(0f, 0.42f, 1f), Shader.TileMode.CLAMP,
        )
        c.drawCircle(cx, cy, r, scrim)
        scrim.shader = null

        Draw.text.setShadowLayer(8f * f, 0f, 2f * f, Color.BLACK)
        // Centre time + AM/PM as one measured group so "12:59 PM" never overlaps.
        val t = now.format(timeFmt)
        val ap = now.format(ampmFmt)
        val timeSize = 120f * f
        val apSize = 24f * f
        Draw.text.typeface = Typeface.DEFAULT_BOLD; Draw.text.textSize = timeSize
        val tw = Draw.text.measureText(t)
        Draw.text.typeface = Typeface.DEFAULT; Draw.text.textSize = apSize
        val aw = Draw.text.measureText(ap)
        val gap = 8f * f
        val x0 = cx - (tw + gap + aw) / 2f
        label(c, t, x0, cy + 8f * f, timeSize, Color.WHITE, align = Paint.Align.LEFT, bold = true)
        label(c, ap, x0 + tw + gap, cy + 2f * f, apSize, Color.WHITE, align = Paint.Align.LEFT)
        label(c, now.format(dateFmt), cx, cy + 50f * f, 24f * f, Color.parseColor("#E0E0E0"))
        Draw.text.clearShadowLayer()

        if (!live) label(c, "video needs the PC", cx, cy + 168f * f, 10f * f, Draw.mutedColor)
    }

    override fun drawIcon(c: Canvas, x: Float, y: Float, size: Float, selected: Boolean) {
        val col = if (selected) color else Draw.mutedColor
        Draw.ring.strokeWidth = size * 0.09f; Draw.ring.color = col
        c.drawCircle(x, y, size * 0.42f, Draw.ring)
        Draw.fill.color = col
        val p = Path().apply {
            moveTo(x - size * 0.12f, y - size * 0.2f)
            lineTo(x + size * 0.22f, y)
            lineTo(x - size * 0.12f, y + size * 0.2f)
            close()
        }
        c.drawPath(p, Draw.fill)
    }

    companion object {
        /** File name inside the agent's `media/` folder. */
        const val FILE = "fireplace.mp4"

        /** Half speed keeps the fire calm and the loop unhurried. */
        const val CLOCK_SPEED = 0.50f

        /** Pull the clip back slightly so the fire fills less of the circle. */
        const val CLOCK_ZOOM = 0.92f
    }
}
