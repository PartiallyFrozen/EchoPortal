package com.echoportal

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.BitmapShader
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Shader
import android.util.Base64
import com.echoportal.Draw.fmt
import com.echoportal.Draw.label
import org.json.JSONObject
import kotlin.math.min

/**
 * ComfyUI monitor. Page 0: progress rings (node steps inside, whole-job outside), current node,
 * elapsed / ETA, queue. Page 1: the latest output image clipped to the circle. Tap flips pages.
 */
class ComfyFace(ctx: Context) : Face(ctx) {
    override val id = "comfy"
    override val name = "ComfyUI"
    override val color = Color.parseColor("#81C784")
    override val channels = setOf("comfy")
    override val pageCount = 2

    private var up = false
    private var running = false
    private var queue = 0
    private var node = ""
    private var step = 0; private var steps = 0
    private var nodesDone = 0; private var nodesTotal = 0
    private var elapsed = 0.0
    private var eta: Double? = null
    private var lastDuration: Double? = null
    private var jobsToday = 0
    private var error: String? = null
    private var image: Bitmap? = null
    private var imageName = ""
    private var videoUrl: String? = null
    private var videoName = ""
    private var curPage = 0
    private var videoShowing = false
    private var muted = true
    private var paused = false
    private var lastUpdate = 0L
    private var lastDoneAt = 0L
    private val rect = RectF()
    private val imgPaint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)

    override fun onEvent(channel: String, data: JSONObject) {
        val wasRunning = running
        up = data.optBoolean("up", false)
        running = data.optBoolean("running", false)
        queue = data.optInt("queue", 0)
        node = data.optString("node", "")
        step = data.optInt("step", 0); steps = data.optInt("steps", 0)
        nodesDone = data.optInt("nodes_done", 0); nodesTotal = data.optInt("nodes_total", 0)
        elapsed = data.optDouble("elapsed", 0.0)
        eta = if (data.isNull("eta")) null else data.optDouble("eta")
        lastDuration = if (data.isNull("last_duration")) null else data.optDouble("last_duration")
        jobsToday = data.optInt("jobs_today", 0)
        error = if (data.isNull("error")) null else data.optString("error")
        if (wasRunning && !running) lastDoneAt = System.currentTimeMillis()
        lastUpdate = System.currentTimeMillis()
        val v = if (data.isNull("last_video")) null else data.optString("last_video").ifEmpty { null }
        if (v != videoUrl) { videoUrl = v; videoName = data.optString("last_video_name", ""); if (curPage == 1) syncVideo() }
        val b64 = if (data.isNull("last_image")) null else data.optString("last_image")
        android.util.Log.i("EchoPortal.Comfy", "event: running=$running up=$up image=${b64?.length ?: 0} video=${videoUrl != null} name=${data.optString("last_image_name")}")
        if (!b64.isNullOrEmpty()) {
            try {
                val bytes = Base64.decode(b64.substringAfter(","), Base64.DEFAULT)
                image = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                imageName = data.optString("last_image_name", "")
                android.util.Log.i("EchoPortal.Comfy", "decoded image ${image?.width}x${image?.height}")
            } catch (e: Exception) { android.util.Log.w("EchoPortal.Comfy", "image decode failed: $e") }
        }
        invalidate()
    }

    override fun onPage(page: Int) { curPage = page; syncVideo() }
    override fun onHide() { curPage = -1; syncVideo() }

    /** Show the video surface only while the media page is visible and the latest output is a video. */
    private fun syncVideo() {
        val want = curPage == 1 && videoUrl != null
        if (want && !videoShowing) { muted = true; paused = false; muteVideo(true); requestVideo(videoUrl); videoShowing = true }
        else if (!want && videoShowing) { requestVideo(null); videoShowing = false }
    }

    override fun onTick() { if (running) { elapsed += 1.0; eta = eta?.let { (it - 1.0).coerceAtLeast(0.0) }; invalidate() } }

    override fun preview(): String = when {
        !up -> "ComfyUI not running"
        running -> fmt("%s  ·  %d/%d  ·  %s", node.take(18), step, steps, fmtSecs(elapsed))
        else -> fmt("idle  ·  %d today%s", jobsToday, lastDuration?.let { "  ·  last ${fmtSecs(it)}" } ?: "")
    }

    private fun fmtSecs(s: Double): String = if (s >= 60) fmt("%d:%02d", (s / 60).toInt(), (s % 60).toInt()) else fmt("%.0f s", s)

    override fun onTap(x: Float, y: Float, cx: Float, cy: Float, r: Float, page: Int): Boolean {
        if (page != 1 || !videoShowing) return false
        val f = r / 240f
        val by = cy + r * 0.80f
        when {
            kotlin.math.hypot(x - (cx + 112f * f), y - by) < 30f * f -> { muted = !muted; muteVideo(muted) }
            else -> { paused = !paused; toggleVideo() }
        }
        invalidate()
        return true
    }

    /** Small speaker glyph; a slash across it when muted. */
    private fun drawSpeaker(c: Canvas, x: Float, y: Float, s: Float, on: Boolean, f: Float) {
        Draw.fill.color = Draw.dimColor; Draw.fill.alpha = 230
        c.drawCircle(x, y, 20f * f, Draw.fill)
        Draw.fill.alpha = 255
        Draw.fill.color = if (on) Draw.textColor else Draw.mutedColor
        c.drawRect(x - s * 0.45f, y - s * 0.18f, x - s * 0.2f, y + s * 0.18f, Draw.fill)
        val p = android.graphics.Path().apply {
            moveTo(x - s * 0.2f, y - s * 0.18f); lineTo(x + s * 0.05f, y - s * 0.42f); lineTo(x + s * 0.05f, y + s * 0.42f); lineTo(x - s * 0.2f, y + s * 0.18f); close()
        }
        c.drawPath(p, Draw.fill)
        Draw.ring.strokeWidth = s * 0.08f; Draw.ring.color = Draw.fill.color
        if (on) {
            rect.set(x - s * 0.15f, y - s * 0.35f, x + s * 0.4f, y + s * 0.35f)
            c.drawArc(rect, -40f, 80f, false, Draw.ring)
            rect.set(x - s * 0.1f, y - s * 0.55f, x + s * 0.6f, y + s * 0.55f)
            c.drawArc(rect, -45f, 90f, false, Draw.ring)
        } else {
            Draw.line.strokeWidth = s * 0.1f; Draw.line.color = Draw.hotColor
            c.drawLine(x - s * 0.5f, y + s * 0.5f, x + s * 0.5f, y - s * 0.5f, Draw.line)
        }
    }

    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, page: Int, ambient: Boolean) {
        if (page == 1) { drawImagePage(c, cx, cy, r, f); return }
        Draw.header(c, cx, cy, f, "ComfyUI", if (!up) "not running" else if (running) "rendering" else "idle", color)

        // outer ring: whole job (nodes done); inner ring: current node's steps
        val jobPct = if (nodesTotal > 0) 100.0 * nodesDone / nodesTotal else 0.0
        val stepPct = if (steps > 0) 100.0 * step / steps else 0.0
        Draw.ringArc(c, cx, cy, r * 0.56f, 10f * f, if (running) jobPct else if (lastDoneAt > 0) 100.0 else 0.0, color, alarmAbove90 = false)
        Draw.ringArc(c, cx, cy, r * 0.45f, 8f * f, if (running) stepPct else 0.0, Draw.cpuColor, alarmAbove90 = false)

        if (!up) {
            label(c, "—", cx, cy + 12f * f, 40f * f, Draw.mutedColor)
            label(c, "start ComfyUI on the PC", cx, cy + 40f * f, 12f * f, Draw.mutedColor)
        } else if (running) {
            label(c, if (steps > 0) fmt("%d / %d", step, steps) else "…", cx, cy + 6f * f, 34f * f, Draw.textColor, bold = true)
            label(c, node.take(22), cx, cy + 30f * f, 13f * f, color)
            label(c, fmt("%s elapsed%s", fmtSecs(elapsed), eta?.let { "  ·  ~${fmtSecs(it)} left" } ?: ""), cx, cy + 50f * f, 12f * f, Draw.mutedColor)
        } else {
            label(c, "idle", cx, cy + 10f * f, 30f * f, Draw.textColor, bold = true)
            label(c, lastDuration?.let { "last job ${fmtSecs(it)}" } ?: "no jobs yet", cx, cy + 34f * f, 13f * f, Draw.mutedColor)
        }
        error?.let { label(c, it.take(40), cx, cy + 74f * f, 11f * f, Draw.hotColor) }
        label(c, fmt("queue %d  ·  %d job%s today  ·  nodes %d/%d", queue, jobsToday, if (jobsToday == 1) "" else "s", nodesDone, nodesTotal),
            cx, cy + 148f * f, 11f * f, Draw.mutedColor)
        // tiny preview of the latest image at the bottom, as a hint that page 2 exists
        image?.let { bmp ->
            val s = 40f * f
            drawRoundImage(c, bmp, cx, cy + 108f * f, s)
        }
    }

    private fun drawImagePage(c: Canvas, cx: Float, cy: Float, r: Float, f: Float) {
        val bmp = image
        if (videoShowing) {
            // the shell has cleared the circle; the video plays underneath. Just add the caption.
            Draw.fill.color = Color.BLACK; Draw.fill.alpha = 150
            rect.set(cx - r, cy + r * 0.62f, cx + r, cy + r)
            c.drawRect(rect, Draw.fill)
            Draw.fill.alpha = 255
            label(c, videoName.take(26), cx - 10f * f, cy + r * 0.80f, 12f * f, Draw.textColor)
            label(c, if (paused) "\u2016 paused  \u00b7  tap to play" else "\u25b6 video  \u00b7  tap to pause", cx - 10f * f, cy + r * 0.80f + 16f * f, 10f * f, Draw.mutedColor)
            drawSpeaker(c, cx + 112f * f, cy + r * 0.80f + 4f * f, 22f * f, !muted, f)
            label(c, if (muted) "muted" else "sound", cx + 112f * f, cy + r * 0.80f + 34f * f, 9f * f, Draw.mutedColor)
            return
        }
        if (bmp == null) {
            Draw.header(c, cx, cy, f, "ComfyUI", "latest image", color)
            label(c, "no image yet", cx, cy + 8f * f, 16f * f, Draw.mutedColor)
            return
        }
        drawRoundImage(c, bmp, cx, cy, r * 0.98f)
        // caption strip at the bottom
        Draw.fill.color = Color.BLACK; Draw.fill.alpha = 150
        rect.set(cx - r, cy + r * 0.62f, cx + r, cy + r)
        c.drawRect(rect, Draw.fill)
        Draw.fill.alpha = 255
        label(c, imageName.take(30), cx, cy + r * 0.80f, 12f * f, Draw.textColor)
        label(c, lastDuration?.let { "rendered in ${fmtSecs(it)}" } ?: "", cx, cy + r * 0.80f + 16f * f, 10f * f, Draw.mutedColor)
    }

    /** Draw a bitmap centre-cropped into a circle of the given radius. */
    private fun drawRoundImage(c: Canvas, bmp: Bitmap, cx: Float, cy: Float, radius: Float) {
        val scale = (2 * radius) / min(bmp.width, bmp.height)
        val m = Matrix().apply {
            setScale(scale, scale)
            postTranslate(cx - bmp.width * scale / 2f, cy - bmp.height * scale / 2f)
        }
        imgPaint.shader = BitmapShader(bmp, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP).apply { setLocalMatrix(m) }
        c.drawCircle(cx, cy, radius, imgPaint)
        imgPaint.shader = null
    }

    override fun drawIcon(c: Canvas, x: Float, y: Float, size: Float, selected: Boolean) =
        StubFace.nodes(c, x, y, size, if (selected) color else Draw.mutedColor)
}
