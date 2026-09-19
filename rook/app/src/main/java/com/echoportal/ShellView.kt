package com.echoportal

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.os.Handler
import android.os.Looper
import android.view.MotionEvent
import android.view.View
import android.view.animation.DecelerateInterpolator
import com.echoportal.Draw.fmt
import com.echoportal.Draw.label
import org.json.JSONObject
import java.time.LocalTime
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * The shell: hosts faces, the ring launcher, the quick sheet, banners and ambient mode,
 * and owns the whole gesture vocabulary.
 *
 *   swipe left/right ............ pages within a face
 *   pull down from top edge ..... ring launcher   (in the launcher: drag to rotate, tap to open)
 *   pull up from bottom edge .... quick sheet     (brightness, volume, ambient, night mode)
 *   tap ......................... face action     long-press: shell menu
 */
class ShellView(context: Context, val faces: List<Face>, private val settings: ShellSettings) : View(context) {

    interface Host {
        fun onMenu()
        fun setBrightness(v: Float)          // 0..1
        fun setVolume(v: Float)              // 0..1
        fun getVolume(): Float
        fun onFaceChanged(id: String)
        fun chime()
        fun onSleep(sleeping: Boolean)       // close/reopen the PC connection, stop video
    }
    var host: Host? = null
    val background = Background(context, settings).also { it.invalidate = { invalidate() } }

    enum class Mode { FACE, LAUNCHER, SHEET }
    var mode = Mode.FACE
        private set

    // ---- state -----------------------------------------------------------------------------
    var faceIdx = 0
        private set
    private var page = 0
    private var pageOffset = 0f
    private var pageAnim: ValueAnimator? = null

    private var launcherT = 0f      // 0 = hidden, 1 = fully shown
    private var sheetT = 0f
    private var overlayAnim: ValueAnimator? = null
    private var ringRot = 0f        // radians; icon i sits at angle -PI/2 + i*step + ringRot
    private var ringAnim: ValueAnimator? = null
    private var dwellStart = 0L     // >0 while the auto-open countdown runs
    private val dwellMs = 1500L
    private val dwellTick = object : Runnable {
        override fun run() {
            if (dwellStart == 0L || mode != Mode.LAUNCHER) return
            val elapsed = System.currentTimeMillis() - dwellStart
            if (elapsed >= dwellMs) {
                dwellStart = 0L
                val sel = selectedRingIndex()
                switchTo(sel); animateOverlay(open = false, launcher = true)
            } else { invalidate(); main.postDelayed(this, 40) }
        }
    }
    private fun startDwell() { dwellStart = System.currentTimeMillis(); main.removeCallbacks(dwellTick); main.postDelayed(dwellTick, 40) }
    private fun cancelDwell() { dwellStart = 0L; main.removeCallbacks(dwellTick) }

    var ambient = false
        private set
    var sleeping = false
        private set
    private var wakeUntil = 0L               // touch during the sleep window keeps it awake until this time
    var stayAwakeUntilMorning = false
    private var forcedSleep = false          // "sleep now": stays asleep until touched, regardless of the schedule
    private var preAmbientFace = 0
    private var lastTouch = System.currentTimeMillis()

    private data class Banner(val title: String, val text: String, val faceId: String?, val until: Long)
    private var banner: Banner? = null

    var status = ""
        set(v) { field = v; invalidate() }

    var calDx = 0f; var calDy = 0f; var calScale = 1f
    /** True while a video surface is showing beneath; the shell clears a circle so it shows through. */
    var videoActive = false
        set(v) { field = v; invalidate() }
    private val clearPaint = Paint().apply { xfermode = android.graphics.PorterDuffXfermode(android.graphics.PorterDuff.Mode.CLEAR) }

    private val main = Handler(Looper.getMainLooper())
    private val ticker = object : Runnable {
        override fun run() { tick(); main.postDelayed(this, 1000) }
    }

    // ---- gesture tracking --------------------------------------------------------------------
    private var downX = 0f; private var downY = 0f; private var downT = 0L; private var downRawY = 0f
    private var lastX = 0f; private var lastY = 0f
    private var gesture = Gesture.NONE
    private var longPressFired = false
    private var ringAngle0 = 0f; private var ringRot0 = 0f
    private var sheetDrag: String? = null
    private val longPress = Runnable { longPressFired = true; if (mode != Mode.SHEET) host?.onMenu() }
    private enum class Gesture { NONE, PAGE, PULL_DOWN, PULL_UP, RING, SHEET_SLIDER }

    init {
        setLayerType(LAYER_TYPE_HARDWARE, null)   // needed for the CLEAR xfermode to make a real hole
        faces.forEach { it.invalidate = { postInvalidate() } }
        faces.forEach { face -> face.requestPage = { p ->
            if (face === currentFace && mode == Mode.FACE && !sleeping && !ambient && gesture == Gesture.NONE && pageAnim?.isRunning != true) snapPage(p)
        } }
        faceIdx = faces.indexOfFirst { it.id == settings.lastFace }.coerceAtLeast(0)
        faces[faceIdx].onShow(); faces[faceIdx].onPage(0)
        main.post(ticker)
    }

    fun onHubMessage(ch: String, data: JSONObject) {
        if (ch == "notify") {
            showBanner(data.optString("title"), data.optString("text"), data.optString("face", null), data.optLong("ttl", 8000))
            if (data.optBoolean("chime", true)) host?.chime()
            return
        }
        if (ch == "weatherart" || ch == "bg") background.onEvent(ch, data)
        faces.forEach { if (ch in it.channels) it.onEvent(ch, data) }
    }

    fun showBanner(title: String, text: String, faceId: String?, ttl: Long) {
        banner = Banner(title, text, faceId, System.currentTimeMillis() + ttl)
        if (ambient) wake()
        invalidate()
    }

    val currentFace get() = faces[faceIdx]

    fun switchTo(id: String, toPage: Int = 0) {
        val i = faces.indexOfFirst { it.id == id }
        if (i >= 0) {
            switchTo(i)
            page = toPage.coerceIn(0, faces[i].pageCount - 1); pageOffset = 0f
            faces[i].onPage(page)
            if (mode != Mode.FACE) { launcherT = 0f; sheetT = 0f; mode = Mode.FACE }
            invalidate()
        }
    }

    private fun switchTo(i: Int) {
        if (i == faceIdx) return
        faces[faceIdx].onHide()
        faceIdx = i; page = 0; pageOffset = 0f
        faces[faceIdx].onShow(); faces[faceIdx].onPage(0)
        settings.lastFace = faces[i].id
        host?.onFaceChanged(faces[i].id)
        invalidate()
    }

    // ---- ambient ------------------------------------------------------------------------------
    fun inSleepWindow(): Boolean {
        if (!settings.sleepEnabled) return false
        val h = LocalTime.now().hour
        val a = settings.sleepStart; val b = settings.sleepEnd
        return if (a == b) false else if (a < b) h in a until b else (h >= a || h < b)
    }

    fun sleepUntilText(): String {
        val e = settings.sleepEnd
        return fmt("%d:00 %s", if (e % 12 == 0) 12 else e % 12, if (e < 12) "am" else "pm")
    }

    private fun tick() {
        val now = System.currentTimeMillis()
        val wantSleep = forcedSleep || (inSleepWindow() && now > wakeUntil && !stayAwakeUntilMorning)
        if (wantSleep && !sleeping) enterSleep()
        if (!wantSleep && sleeping) exitSleep()
        if (!inSleepWindow()) stayAwakeUntilMorning = false
        if (sleeping) { banner = null; invalidate(); return }
        faces[faceIdx].onTick()
        if (ambient) {
            faces.firstOrNull { it.id == "clock" }?.onTick()
        } else if (mode == Mode.FACE && settings.ambientEnabled) {
            val idle = now - lastTouch
            val night = settings.nightMode && isNight()
            if (idle > settings.idleMinutes * 60_000L || (night && idle > 60_000L)) enterAmbient()
        }
        banner?.let { if (now > it.until) banner = null }
        invalidate()
    }

    private fun isNight(): Boolean {
        val h = LocalTime.now().hour
        return if (settings.nightStart > settings.nightEnd) h >= settings.nightStart || h < settings.nightEnd
        else h in settings.nightStart until settings.nightEnd
    }

    /** Sleep immediately and stay asleep until the screen is touched. */
    fun sleepNow() { forcedSleep = true; enterSleep() }

    fun enterSleep() {
        if (sleeping) return
        sleeping = true
        if (ambient) { ambient = false }
        faces[faceIdx].onHide()
        host?.onSleep(true)
        host?.setBrightness(0f)
        invalidate()
    }

    fun exitSleep() {
        if (!sleeping) return
        sleeping = false
        lastTouch = System.currentTimeMillis()
        host?.setBrightness(settings.brightness)
        host?.onSleep(false)
        faces[faceIdx].onShow(); faces[faceIdx].onPage(page)
        invalidate()
    }

    /** Touch while sleeping: wake for 10 minutes. */
    fun wakeTemporarily() {
        forcedSleep = false
        wakeUntil = System.currentTimeMillis() + 10 * 60_000L
        exitSleep()
    }

    fun enterAmbient() {
        if (ambient) return
        ambient = true
        preAmbientFace = faceIdx
        host?.setBrightness(settings.ambientBrightness)
        invalidate()
    }

    fun wake() {
        if (!ambient) return
        ambient = false
        lastTouch = System.currentTimeMillis()
        host?.setBrightness(settings.brightness)
        invalidate()
    }

    // ---- touch --------------------------------------------------------------------------------
    override fun onTouchEvent(e: MotionEvent): Boolean {
        // map screen coords back into calibrated content space
        val cx = width / 2f; val cy = height / 2f
        val x = (e.x - calDx - cx) / calScale + cx
        val y = (e.y - calDy - cy) / calScale + cy
        val r = min(width, height) / 2f

        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                lastTouch = System.currentTimeMillis()
                if (sleeping) { wakeTemporarily(); gesture = Gesture.NONE; return true }   // first touch only wakes
                if (ambient) { wake(); gesture = Gesture.NONE; return true }   // first touch only wakes
                downX = x; downY = y; downT = lastTouch; lastX = x; lastY = y; downRawY = e.y
                gesture = Gesture.NONE; longPressFired = false
                cancelDwell()
                pageAnim?.cancel(); ringAnim?.cancel()
                sheetDrag = null
                if (mode == Mode.SHEET) {
                    sheetDrag = sheetHit(x, y, cx, cy, r)
                    if (sheetDrag != null) { gesture = Gesture.SHEET_SLIDER; sheetSet(sheetDrag!!, x, cx, r) }
                } else {
                    main.postDelayed(longPress, 550)
                }
                if (mode == Mode.LAUNCHER) { ringAngle0 = atan2(y - cy, x - cx); ringRot0 = ringRot }
            }
            MotionEvent.ACTION_MOVE -> {
                if (ambient || sleeping) return true
                val dx = x - downX; val dy = y - downY
                if (gesture == Gesture.NONE && hypot(dx, dy) > 14f) {
                    main.removeCallbacks(longPress)
                    gesture = when (mode) {
                        // vertical swipes are the shell's: down from the upper half = launcher,
                        // up from the lower half = quick sheet (no edge needed - the system owns the edges)
                        Mode.FACE -> when {
                            dy > abs(dx) && downY < cy -> Gesture.PULL_DOWN
                            -dy > abs(dx) && downY > cy -> Gesture.PULL_UP
                            abs(dx) > abs(dy) -> Gesture.PAGE
                            else -> Gesture.NONE
                        }
                        Mode.LAUNCHER -> when {
                            downY < cy - r * 0.45f && dy > abs(dx) * 1.5f -> Gesture.PULL_DOWN   // pull down again = close
                            else -> Gesture.RING
                        }
                        Mode.SHEET -> if (dy > abs(dx)) Gesture.PULL_DOWN else Gesture.NONE
                    }
                }
                when (gesture) {
                    Gesture.PAGE -> pageOffset = dx.coerceIn(-width.toFloat(), width.toFloat())
                    Gesture.PULL_DOWN -> when (mode) {
                        Mode.FACE -> launcherT = (dy / (r * 0.9f)).coerceIn(0f, 1f)
                        Mode.LAUNCHER -> launcherT = (1f - dy / (r * 0.9f)).coerceIn(0f, 1f)
                        Mode.SHEET -> sheetT = (1f - dy / (r * 0.9f)).coerceIn(0f, 1f)
                    }
                    Gesture.PULL_UP -> sheetT = (-dy / (r * 0.9f)).coerceIn(0f, 1f)
                    Gesture.RING -> {
                        val a = atan2(y - cy, x - cx)
                        var d = a - ringAngle0
                        while (d > Math.PI) d -= (2 * Math.PI).toFloat()
                        while (d < -Math.PI) d += (2 * Math.PI).toFloat()
                        ringRot = ringRot0 + d
                    }
                    Gesture.SHEET_SLIDER -> sheetSet(sheetDrag!!, x, cx, r)
                    Gesture.NONE -> {}
                }
                lastX = x; lastY = y
                invalidate()
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                main.removeCallbacks(longPress)
                if (ambient || sleeping || longPressFired) { gesture = Gesture.NONE; return true }
                val dt = (System.currentTimeMillis() - downT).coerceAtLeast(1)
                val vx = (x - downX) / dt * 1000f; val vy = (y - downY) / dt * 1000f
                val isTap = gesture == Gesture.NONE && hypot(x - downX, y - downY) < 14f && dt < 400
                when (gesture) {
                    Gesture.PAGE -> {
                        val dir = when { vx < -500 || pageOffset < -width / 3f -> 1; vx > 500 || pageOffset > width / 3f -> -1; else -> 0 }
                        android.util.Log.d("EchoPortal.Pager", "UP: action=${e.actionMasked} vx=$vx offset=$pageOffset dir=$dir page=$page")
                        snapPage(page + dir)
                    }
                    Gesture.PULL_DOWN -> when (mode) {
                        Mode.FACE -> animateOverlay(open = launcherT > 0.35f || vy > 600, launcher = true)
                        Mode.LAUNCHER -> animateOverlay(open = !(launcherT < 0.65f || vy > 600), launcher = true)
                        Mode.SHEET -> animateOverlay(open = !(sheetT < 0.65f || vy > 600), launcher = false)
                    }
                    Gesture.PULL_UP -> animateOverlay(open = sheetT > 0.35f || vy < -600, launcher = false)
                    Gesture.RING -> snapRing(if (abs(vx) + abs(vy) > 900) ringVelocityStep(x - cx, y - cy, vx, vy) else 0)
                    Gesture.SHEET_SLIDER -> {}
                    Gesture.NONE -> if (isTap) onTap(x, y, cx, cy, r)
                }
                gesture = Gesture.NONE
            }
        }
        return true
    }

    private fun ringVelocityStep(px: Float, py: Float, vx: Float, vy: Float): Int {
        // cross > 0 = clockwise on screen = ring rotation increasing = selected index DEcreasing
        val cross = px * vy - py * vx
        return if (cross > 0) -1 else 1
    }

    private fun onTap(x: Float, y: Float, cx: Float, cy: Float, r: Float) {
        when (mode) {
            Mode.FACE -> {
                banner?.let { b ->
                    if (y < cy - r * 0.45f) { b.faceId?.let { switchTo(it) }; banner = null; invalidate(); return }
                }
                currentFace.onTap(x, y, cx, cy, r, page)
            }
            Mode.LAUNCHER -> {
                val hit = ringHit(x, y, cx, cy, r)
                if (hit >= 0) { switchTo(hit); animateOverlay(open = false, launcher = true) }
                else if (hypot(x - cx, y - cy) < r * 0.35f) { switchTo(selectedRingIndex()); animateOverlay(open = false, launcher = true) }
            }
            Mode.SHEET -> {
                val btn = sheetButtonHit(x, y, cx, cy, r)
                when (btn) {
                    "ambient" -> { animateOverlay(open = false, launcher = false); main.postDelayed({ enterAmbient() }, 350) }
                    "sleepsched" -> {
                        if (inSleepWindow() && !stayAwakeUntilMorning) { stayAwakeUntilMorning = true }   // late night: stay up
                        else { settings.sleepEnabled = !settings.sleepEnabled }
                        invalidate()
                    }
                    "autoambient" -> { settings.ambientEnabled = !settings.ambientEnabled; invalidate() }
                    "menu" -> { animateOverlay(open = false, launcher = false); host?.onMenu() }
                    null -> if (y < cy - r * 0.1f) animateOverlay(open = false, launcher = false)
                }
            }
        }
    }

    // ---- pages --------------------------------------------------------------------------------
    private fun snapPage(target: Int) {
        val t = target.coerceIn(0, currentFace.pageCount - 1)
        android.util.Log.d("EchoPortal.Pager", "snapPage: page=$page target=$target -> t=$t offset=$pageOffset")
        val w = width.toFloat()
        val end = when { t > page -> -w; t < page -> w; else -> 0f }
        pageAnim?.cancel()
        pageAnim = ValueAnimator.ofFloat(pageOffset, end).apply {
            duration = (180 + 150 * abs(end - pageOffset) / w).toLong()
            interpolator = DecelerateInterpolator()
            addUpdateListener { pageOffset = it.animatedValue as Float; invalidate() }
            addListener(object : android.animation.AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: android.animation.Animator) { val changed = page != t; page = t; pageOffset = 0f; if (changed) currentFace.onPage(t); invalidate() }
            })
            start()
        }
    }

    // ---- overlays -----------------------------------------------------------------------------
    private fun animateOverlay(open: Boolean, launcher: Boolean) {
        val from = if (launcher) launcherT else sheetT
        val to = if (open) 1f else 0f
        if (launcher) cancelDwell()
        if (open) mode = if (launcher) Mode.LAUNCHER else Mode.SHEET
        if (launcher && open) { ringRot = -indexAngle(faceIdx); }
        overlayAnim?.cancel()
        overlayAnim = ValueAnimator.ofFloat(from, to).apply {
            duration = 220
            interpolator = DecelerateInterpolator()
            addUpdateListener { v -> if (launcher) launcherT = v.animatedValue as Float else sheetT = v.animatedValue as Float; invalidate() }
            addListener(object : android.animation.AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: android.animation.Animator) { if (!open) mode = Mode.FACE; invalidate() }
            })
            start()
        }
    }

    // ---- ring launcher geometry ----------------------------------------------------------------
    private val step get() = (2 * Math.PI / faces.size).toFloat()
    private fun indexAngle(i: Int) = i * step
    private fun iconAngle(i: Int) = (-Math.PI / 2).toFloat() + indexAngle(i) + ringRot
    private fun ringRadius(r: Float) = r * 0.66f

    private fun selectedRingIndex(): Int {
        var best = 0; var bestD = Float.MAX_VALUE
        for (i in faces.indices) {
            var d = abs(normAngle(iconAngle(i) + (Math.PI / 2).toFloat()))
            if (d < bestD) { bestD = d; best = i }
        }
        return best
    }

    private fun normAngle(a: Float): Float {
        var x = a
        while (x > Math.PI) x -= (2 * Math.PI).toFloat()
        while (x < -Math.PI) x += (2 * Math.PI).toFloat()
        return x
    }

    private fun ringHit(x: Float, y: Float, cx: Float, cy: Float, r: Float): Int {
        val rr = ringRadius(r)
        for (i in faces.indices) {
            val ix = cx + rr * cos(iconAngle(i)); val iy = cy + rr * sin(iconAngle(i))
            if (hypot(x - ix, y - iy) < r * 0.16f) return i
        }
        return -1
    }

    private fun snapRing(extraSteps: Int) {
        val sel = selectedRingIndex()
        val target = -indexAngle((sel + extraSteps + faces.size * 4) % faces.size)
        // choose the equivalent target angle closest to the current rotation
        var t = target
        while (t - ringRot > Math.PI) t -= (2 * Math.PI).toFloat()
        while (t - ringRot < -Math.PI) t += (2 * Math.PI).toFloat()
        ringAnim?.cancel()
        ringAnim = ValueAnimator.ofFloat(ringRot, t).apply {
            duration = 220; interpolator = DecelerateInterpolator()
            addUpdateListener { ringRot = it.animatedValue as Float; invalidate() }
            addListener(object : AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: Animator) { if (selectedRingIndex() != faceIdx) startDwell() }
            })
            start()
        }
    }

    // ---- sheet geometry ------------------------------------------------------------------------
    private fun sliderRect(name: String, cx: Float, cy: Float, r: Float): FloatArray {
        val l = cx - r * 0.55f; val rr = cx + r * 0.55f
        val y = if (name == "brightness") cy + r * 0.02f else cy + r * 0.24f
        return floatArrayOf(l, y - r * 0.07f, rr, y + r * 0.07f)
    }

    private fun sheetHit(x: Float, y: Float, cx: Float, cy: Float, r: Float): String? {
        for (n in listOf("brightness", "volume")) {
            val q = sliderRect(n, cx, cy, r)
            if (x >= q[0] - r * 0.05f && x <= q[2] + r * 0.05f && y >= q[1] && y <= q[3]) return n
        }
        return null
    }

    private fun sheetSet(name: String, x: Float, cx: Float, r: Float) {
        val q = sliderRect(name, cx, 0f, r)
        val v = ((x - q[0]) / (q[2] - q[0])).coerceIn(0f, 1f)
        if (name == "brightness") { settings.brightness = v.coerceAtLeast(0.03f); host?.setBrightness(settings.brightness) }
        else host?.setVolume(v)
        invalidate()
    }

    private fun sheetButtons(cx: Float, cy: Float, r: Float): List<Triple<String, Float, Float>> {
        val y = cy + r * 0.52f
        return listOf(Triple("ambient", cx - r * 0.42f, y), Triple("autoambient", cx - r * 0.14f, y), Triple("sleepsched", cx + r * 0.14f, y), Triple("menu", cx + r * 0.42f, y))
    }

    private fun sheetButtonHit(x: Float, y: Float, cx: Float, cy: Float, r: Float): String? =
        sheetButtons(cx, cy, r).firstOrNull { hypot(x - it.second, y - it.third) < r * 0.13f }?.first

    // ---- drawing -------------------------------------------------------------------------------
    override fun onDraw(canvas: Canvas) {
        canvas.drawColor(Color.BLACK)
        val w = width.toFloat(); val h = height.toFloat()
        val cx = w / 2f; val cy = h / 2f
        val r = min(w, h) / 2f
        val f = r / 240f

        canvas.save()
        canvas.translate(calDx, calDy)
        canvas.scale(calScale, calScale, cx, cy)

        if (sleeping) { canvas.restore(); return }   // black; backlight at minimum
        if (ambient) {
            (faces.firstOrNull { it.id == "clock" } ?: currentFace).draw(canvas, cx, cy, r, f, 0, ambient = true)
            canvas.restore()
            return
        }
        background.draw(canvas, cx, cy, r, currentFace.bgLevel(page))

        // awake inside the sleep window: say so, quietly
        if (inSleepWindow() && mode == Mode.FACE) {
            label(canvas, if (stayAwakeUntilMorning) "awake until ${sleepUntilText()}" else "sleeps in ${((wakeUntil - System.currentTimeMillis()) / 60_000L + 1).coerceAtLeast(1)} min  ·  ${sleepUntilText()}",
                cx, cy - 170f * f, 10f * f, Draw.gridColor)
        }

        // video showing beneath: clear a circle so the TextureView is visible (only when nothing overlays it)
        if (videoActive && mode == Mode.FACE && launcherT == 0f && sheetT == 0f && pageOffset == 0f && banner == null) {
            canvas.drawCircle(cx, cy, r * 0.98f, clearPaint)
        }

        // face + neighbour page while sliding
        drawFacePage(canvas, page, pageOffset, cx, cy, r, f)
        if (pageOffset < 0 && page + 1 < currentFace.pageCount) drawFacePage(canvas, page + 1, pageOffset + w, cx, cy, r, f)
        if (pageOffset > 0 && page > 0) drawFacePage(canvas, page - 1, pageOffset - w, cx, cy, r, f)
        if (currentFace.pageCount > 1) drawDots(canvas, cx, cy, f)
        if (status.isNotEmpty() && currentFace.channels.isNotEmpty()) label(canvas, status.take(46), cx, cy + 160f * f, 11f * f, Draw.hotColor)

        if (launcherT > 0f) drawLauncher(canvas, cx, cy, r, f, launcherT)
        if (sheetT > 0f) drawSheet(canvas, cx, cy, r, f, sheetT)
        banner?.let { drawBanner(canvas, it, cx, cy, r, f) }
        canvas.restore()
    }

    private fun drawFacePage(c: Canvas, p: Int, dx: Float, cx: Float, cy: Float, r: Float, f: Float) {
        c.save(); c.translate(dx, 0f)
        currentFace.draw(c, cx, cy, r, f, p, ambient = false)
        c.restore()
    }

    private fun drawDots(c: Canvas, cx: Float, cy: Float, f: Float) {
        val n = currentFace.pageCount
        val gap = 12f * f
        val x0 = cx - gap * (n - 1) / 2f
        val cur = page - (pageOffset / width)
        for (i in 0 until n) {
            val d = 1f - (abs(cur - i)).coerceIn(0f, 1f)
            Draw.fill.color = Draw.textColor; Draw.fill.alpha = (60 + 195 * d).roundToInt()
            c.drawCircle(x0 + gap * i, cy + 190f * f, (2.2f + 1.3f * d) * f, Draw.fill)
        }
    }

    private fun drawLauncher(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, t: Float) {
        Draw.fill.color = Color.BLACK; Draw.fill.alpha = (230 * t).roundToInt()
        c.drawCircle(cx, cy, r * 1.2f, Draw.fill)
        val s = 0.85f + 0.15f * t
        c.save(); c.scale(s, s, cx, cy)
        val sel = selectedRingIndex()
        val rr = ringRadius(r)
        // ring track
        Draw.ring.strokeWidth = 1f * f; Draw.ring.color = Draw.gridColor
        c.drawCircle(cx, cy, rr, Draw.ring)
        for (i in faces.indices) {
            val a = iconAngle(i)
            val ix = cx + rr * cos(a); val iy = cy + rr * sin(a)
            val selected = i == sel
            val size = if (selected) 62f * f else 44f * f
            Draw.fill.color = if (selected) Draw.dimColor else Color.BLACK; Draw.fill.alpha = 255
            c.drawCircle(ix, iy, size * 0.75f, Draw.fill)
            if (selected) {
                Draw.ring.strokeWidth = 2f * f; Draw.ring.color = faces[i].color; c.drawCircle(ix, iy, size * 0.75f, Draw.ring)
                if (dwellStart > 0L) {
                    val p = ((System.currentTimeMillis() - dwellStart).toFloat() / dwellMs).coerceIn(0f, 1f)
                    Draw.ring.strokeWidth = 4f * f
                    val rr = size * 0.75f + 6f * f
                    c.drawArc(ix - rr, iy - rr, ix + rr, iy + rr, -90f, 360f * p, false, Draw.ring)
                }
            }
            faces[i].drawIcon(c, ix, iy, size, selected)
        }
        val face = faces[sel]
        label(c, face.name, cx, cy - 6f * f, 28f * f, Draw.textColor, bold = true)
        label(c, face.preview().take(40), cx, cy + 20f * f, 13f * f, Draw.mutedColor)
        label(c, LocalTime.now().let { fmt("%d:%02d", if (it.hour % 12 == 0) 12 else it.hour % 12, it.minute) }, cx, cy - 40f * f, 14f * f, Draw.mutedColor)
        label(c, if (dwellStart > 0L) "opening…  ·  touch to cancel" else "drag to rotate  ·  opens on its own", cx, cy + 46f * f, 10f * f, Draw.gridColor)
        c.restore()
        Draw.fill.alpha = 255
    }

    private fun drawSheet(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, t: Float) {
        Draw.fill.color = Color.BLACK; Draw.fill.alpha = (150 * t).roundToInt()
        c.drawCircle(cx, cy, r * 1.2f, Draw.fill)
        c.save(); c.translate(0f, (1f - t) * r)
        // sheet body: lower part of the circle
        Draw.fill.color = Color.parseColor("#141A20"); Draw.fill.alpha = 255
        c.drawCircle(cx, cy, r, Draw.fill)
        Draw.fill.color = Color.BLACK; c.drawRect(cx - r, cy - r, cx + r, cy - r * 0.2f, Draw.fill)
        Draw.fill.color = Color.parseColor("#141A20"); c.drawRoundRect(cx - r, cy - r * 0.2f, cx + r, cy + r * 0.2f, 30f * f, 30f * f, Draw.fill)
        // grabber
        Draw.fill.color = Draw.mutedColor; c.drawRoundRect(cx - 20f * f, cy - r * 0.16f, cx + 20f * f, cy - r * 0.16f + 4f * f, 2f * f, 2f * f, Draw.fill)

        for (n in listOf("brightness", "volume")) {
            val q = sliderRect(n, cx, cy, r)
            val v = if (n == "brightness") settings.brightness else (host?.getVolume() ?: 0f)
            label(c, if (n == "brightness") "☀ Brightness" else "♪ Volume", q[0], q[1] - 6f * f, 11f * f, Draw.mutedColor, Paint.Align.LEFT)
            label(c, "${(v * 100).roundToInt()}%", q[2], q[1] - 6f * f, 11f * f, Draw.mutedColor, Paint.Align.RIGHT)
            Draw.bar(c, q[0], q[1] + (q[3] - q[1]) / 2 - 4f * f, q[2], 8f * f, v * 100.0, if (n == "brightness") Draw.ramColor else Draw.cpuColor)
        }
        for ((name, bx, by) in sheetButtons(cx, cy, r)) {
            val on = when (name) { "sleepsched" -> settings.sleepEnabled && !stayAwakeUntilMorning; "autoambient" -> settings.ambientEnabled; else -> false }
            Draw.fill.color = if (on) Draw.cpuColor else Draw.dimColor; Draw.fill.alpha = 255
            c.drawCircle(bx, by, r * 0.1f, Draw.fill)
            val glyph = when (name) { "ambient" -> "☾"; "autoambient" -> "⏱"; "sleepsched" -> if (inSleepWindow()) "☀" else "⏻"; else -> "≡" }
            label(c, glyph, bx, by + 7f * f, 20f * f, if (on) Color.BLACK else Draw.textColor)
            val cap = when (name) {
                "ambient" -> "dim now"; "autoambient" -> "auto ${settings.idleMinutes}m"
                "sleepsched" -> if (inSleepWindow() && !stayAwakeUntilMorning) "stay awake" else if (settings.sleepEnabled) "off ${hh(settings.sleepStart)}–${hh(settings.sleepEnd)}" else "sched off"
                else -> "menu" }
            label(c, cap, bx, by + r * 0.1f + 14f * f, 9f * f, Draw.mutedColor)
        }
        c.restore()
    }

    private fun hh(h: Int) = fmt("%d%s", if (h % 12 == 0) 12 else h % 12, if (h < 12) "a" else "p")

    private fun drawBanner(c: Canvas, b: Banner, cx: Float, cy: Float, r: Float, f: Float) {
        val top = cy - r * 0.92f; val bot = cy - r * 0.5f
        Draw.fill.color = Color.parseColor("#1B2A38"); Draw.fill.alpha = 240
        c.drawRoundRect(cx - r * 0.7f, top, cx + r * 0.7f, bot, 18f * f, 18f * f, Draw.fill)
        Draw.ring.strokeWidth = 1.5f * f; Draw.ring.color = Draw.cpuColor
        c.drawRoundRect(cx - r * 0.7f, top, cx + r * 0.7f, bot, 18f * f, 18f * f, Draw.ring)
        label(c, b.title.take(28), cx, top + 24f * f, 15f * f, Draw.textColor, bold = true)
        label(c, b.text.take(44), cx, top + 44f * f, 12f * f, Draw.mutedColor)
        if (b.faceId != null) label(c, "tap to open", cx, top + 60f * f, 9f * f, Draw.gridColor)
    }
}
