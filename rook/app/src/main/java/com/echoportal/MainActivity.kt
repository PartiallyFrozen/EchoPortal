package com.echoportal

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.graphics.Canvas
import android.graphics.Color
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Bundle
import android.provider.Settings
import android.view.KeyEvent
import android.view.View
import android.view.WindowManager
import android.widget.EditText
import android.widget.Toast

class MainActivity : Activity(), ShellView.Host {

    private lateinit var shell: ShellView
    private lateinit var video: VideoPlayer
    private lateinit var root: android.widget.FrameLayout
    private lateinit var hub: Hub
    private lateinit var settings: ShellSettings
    private lateinit var audio: AudioManager
    private var calibrating = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        hideSystemUi()
        settings = ShellSettings(getSharedPreferences("rook", Context.MODE_PRIVATE))
        if (settings.server.isEmpty()) settings.server = getString(R.string.default_server)
        audio = getSystemService(Context.AUDIO_SERVICE) as AudioManager

        val faces = listOf(
            ClockFace(this, lat = 0.0, lon = 0.0, tz = "UTC"),
            PcMonFace(this),
            VoiceFace(this),
            ComfyFace(this),
            TickerFace(this),
            WeatherArtFace(this),
            ClaudeFace(this),
        )
        shell = ShellView(this, faces, settings).also {
            it.host = this
            it.calDx = settings.calDx; it.calDy = settings.calDy; it.calScale = settings.calScale
        }
        video = VideoPlayer(this)
        root = android.widget.FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
            val lp = android.widget.FrameLayout.LayoutParams(android.widget.FrameLayout.LayoutParams.MATCH_PARENT, android.widget.FrameLayout.LayoutParams.MATCH_PARENT)
            addView(video.view, lp)
            addView(shell, android.widget.FrameLayout.LayoutParams(lp))
        }
        setContentView(root)
        faces.forEach { f ->
            f.requestVideo = { url ->
                if (url == null) { video.stop(); shell.videoActive = false }
                else { video.view.post { video.applyCalibration(shell.calDx, shell.calDy, shell.calScale) }; video.play(url); shell.videoActive = true }
            }
            f.toggleVideo = { video.toggle() }
            f.muteVideo = { m -> video.muted = m }
        }
        setBrightness(settings.brightness)

        hub = Hub(
            onMessage = { ch, data, _ -> shell.onHubMessage(ch, data) },
            onState = { shell.status = it },
        )
        faces.forEach { f -> f.send = { obj -> hub.send(obj) } }
        (faces.first { it.id == "voice" } as VoiceFace).also { v -> v.sendBytes = { b -> hub.sendBytes(b) }; v.sendJson = { o -> hub.send(o) } }
        if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != android.content.pm.PackageManager.PERMISSION_GRANTED)
            requestPermissions(arrayOf(android.Manifest.permission.RECORD_AUDIO), 1)
        (faces.first { it.id == "clock" } as ClockFace).onStyleChanged = { p -> settings.clockStyle = p }
        if (settings.lastFace == "clock" && intent?.getStringExtra("face") == null) shell.switchTo("clock", settings.clockStyle)
        handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    /** `am start -n com.echoportal/.MainActivity --es face pcmon --ei page 3` opens a face/page (testing + automation). */
    private fun handleIntent(intent: Intent?) {
        intent?.getStringExtra("micsrc")?.let { src -> (shell.faces.first { it.id == "voice" } as VoiceFace).micSource = src }
        when (intent?.getStringExtra("sleep")) { "now" -> shell.sleepNow(); "wake" -> shell.wakeTemporarily() }
        intent?.getStringExtra("voicedemo")?.let { (shell.faces.first { it.id == "voice" } as VoiceFace).demo(it) }
        intent?.getStringExtra("cal")?.let { spec ->
            // not persisted: "0,0,1" for recordings, "default" to go back to the saved calibration
            if (spec == "default") { shell.calDx = settings.calDx; shell.calDy = settings.calDy; shell.calScale = settings.calScale }
            else spec.split(",").map { it.trim().toFloatOrNull() ?: 0f }.let { v -> if (v.size == 3) { shell.calDx = v[0]; shell.calDy = v[1]; shell.calScale = v[2] } }
            video.view.post { video.applyCalibration(shell.calDx, shell.calDy, shell.calScale) }
            shell.invalidate()
        }
        intent?.getStringExtra("bg")?.let { spec ->   // testing: --es bg black|sky|image|color:Midnight
            val (m, arg) = spec.split(":", limit = 2).let { it[0] to it.getOrNull(1) }
            val col = Background.PRESETS.firstOrNull { it.first.equals(arg ?: "", true) }?.second ?: settings.bgColor
            shell.background.set(m, col)
        }
        val face = intent?.getStringExtra("face") ?: return
        shell.wake()
        shell.switchTo(face, intent.getIntExtra("page", 0))
    }

    override fun onResume() { super.onResume(); if (!shell.sleeping) hub.connect(settings.server) }
    override fun onPause() { super.onPause(); hub.close() }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemUi()
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        if (calibrating) {
            when (keyCode) {
                KeyEvent.KEYCODE_VOLUME_UP -> { shell.calScale = (shell.calScale + 0.01f).coerceIn(0.7f, 1.3f); calOverlay?.invalidate(); return true }
                KeyEvent.KEYCODE_VOLUME_DOWN -> { shell.calScale = (shell.calScale - 0.01f).coerceIn(0.7f, 1.3f); calOverlay?.invalidate(); return true }
            }
        }
        if (shell.currentFace.onKey(keyCode)) return true
        when (keyCode) {
            KeyEvent.KEYCODE_VOLUME_UP -> { setBrightness((settings.brightness + 0.1f).coerceAtMost(1f)); settings.brightness = (settings.brightness + 0.1f).coerceAtMost(1f); return true }
            KeyEvent.KEYCODE_VOLUME_DOWN -> { settings.brightness = (settings.brightness - 0.1f).coerceAtLeast(0.03f); setBrightness(settings.brightness); return true }
        }
        return super.onKeyDown(keyCode, event)
    }

    // ---- ShellView.Host ------------------------------------------------------------------------
    override fun onMenu() {
        val items = arrayOf("Sleep now (until touched)", "Sleep schedule…", "Background…", "Open Settings", "Open Clock app", "PC agent address", "Calibrate screen", "Reset calibration", "Dim now")
        AlertDialog.Builder(this).setTitle("EchoPortal").setItems(items) { _, which ->
            when (which) {
                0 -> shell.sleepNow()
                1 -> sleepScheduleDialog()
                2 -> backgroundDialog()
                3 -> launch(Intent(Settings.ACTION_SETTINGS))
                4 -> launch(packageManager.getLaunchIntentForPackage("com.android.deskclock"))
                5 -> promptForServer()
                6 -> startCalibration()
                7 -> { shell.calDx = -2f; shell.calDy = 26f; shell.calScale = 1.07f; saveCalibration(); shell.invalidate() }
                8 -> shell.enterAmbient()
            }
        }.show()
    }

    /** Global background: black, a solid colour, today's Sky painting, or a picture sent from the tray app. */
    private fun backgroundDialog() {
        val bg = shell.background
        val items = arrayOf("Black", "Solid colour…", "Sky — today's weather painting", "My picture (send one from the tray app)")
        AlertDialog.Builder(this).setTitle("Background · now: ${bg.describe()}").setItems(items) { _, which ->
            when (which) {
                0 -> bg.set(Background.BLACK)
                1 -> AlertDialog.Builder(this).setTitle("Colour").setItems(Background.PRESETS.map { it.first }.toTypedArray()) { _, i ->
                    bg.set(Background.COLOR, Background.PRESETS[i].second)
                }.show()
                2 -> { bg.set(Background.SKY); if (!bg.hasSky) Toast.makeText(this, "Waiting for the PC agent's sky painting", Toast.LENGTH_SHORT).show() }
                3 -> { bg.set(Background.IMAGE); if (!bg.hasUser) Toast.makeText(this, "No picture yet — tray app → Background picture…", Toast.LENGTH_LONG).show() }
            }
        }.show()
    }

    override fun setBrightness(v: Float) {
        val lp = window.attributes
        lp.screenBrightness = v.coerceIn(0f, 1f)
        window.attributes = lp
    }

    override fun setVolume(v: Float) {
        val max = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        audio.setStreamVolume(AudioManager.STREAM_MUSIC, (v * max).toInt(), 0)
    }

    override fun getVolume(): Float {
        val max = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC).coerceAtLeast(1)
        return audio.getStreamVolume(AudioManager.STREAM_MUSIC).toFloat() / max
    }

    override fun onFaceChanged(id: String) {}

    override fun onSleep(sleeping: Boolean) {
        if (sleeping) { video.stop(); shell.videoActive = false; hub.close() }
        else hub.connect(settings.server)
    }

    /** Dialog with sleep / wake hour pickers and an on-off switch. */
    private fun sleepScheduleDialog() {
        val dp = resources.displayMetrics.density
        val row = android.widget.LinearLayout(this).apply { orientation = android.widget.LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER; setPadding((8 * dp).toInt(), (4 * dp).toInt(), (8 * dp).toInt(), 0) }
        fun picker(value: Int) = android.widget.NumberPicker(this).apply {
            minValue = 0; maxValue = 23; this.value = value; wrapSelectorWheel = true
            setFormatter { h -> String.format(java.util.Locale.US, "%d %s", if (h % 12 == 0) 12 else h % 12, if (h < 12) "am" else "pm") }
        }
        val start = picker(settings.sleepStart); val end = picker(settings.sleepEnd)
        fun labelled(text: String, v: android.view.View) = android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.VERTICAL; gravity = android.view.Gravity.CENTER
            addView(android.widget.TextView(this@MainActivity).apply { this.text = text; gravity = android.view.Gravity.CENTER })
            addView(v)
        }
        row.addView(labelled("Off at", start)); row.addView(labelled("On at", end))
        val sw = android.widget.Switch(this).apply { text = "  Sleep schedule enabled"; isChecked = settings.sleepEnabled }
        val box = android.widget.LinearLayout(this).apply { orientation = android.widget.LinearLayout.VERTICAL; addView(sw); addView(row); setPadding((16 * dp).toInt(), (8 * dp).toInt(), (16 * dp).toInt(), 0) }
        AlertDialog.Builder(this).setTitle("Sleep schedule").setView(box)
            .setPositiveButton("Save") { _, _ ->
                settings.sleepStart = start.value; settings.sleepEnd = end.value; settings.sleepEnabled = sw.isChecked
                shell.stayAwakeUntilMorning = false
                Toast.makeText(this, if (sw.isChecked) "Off ${start.value}:00, on ${end.value}:00" else "Sleep schedule off", Toast.LENGTH_SHORT).show()
                shell.invalidate()
            }
            .setNegativeButton("Cancel", null).show()
    }

    override fun chime() {
        try { ToneGenerator(AudioManager.STREAM_MUSIC, 70).startTone(ToneGenerator.TONE_PROP_ACK, 180) } catch (_: Exception) {}
    }

    // ---- calibration: draws a target on top of the shell -------------------------------------
    private fun startCalibration() {
        calibrating = true
        val overlay = object : View(this) {
            private var lx = 0f; private var ly = 0f
            override fun onDraw(c: Canvas) {
                val cx = width / 2f; val cy = height / 2f; val r = minOf(width, height) / 2f; val f = r / 240f
                c.drawColor(Color.BLACK)
                c.save(); c.translate(shell.calDx, shell.calDy); c.scale(shell.calScale, shell.calScale, cx, cy)
                Draw.ring.strokeWidth = 3f * f; Draw.ring.color = Draw.hotColor
                c.drawCircle(cx, cy, r - 2f * f, Draw.ring)
                Draw.ring.strokeWidth = 1.5f * f; Draw.ring.color = Draw.mutedColor
                for (k in listOf(0.75f, 0.5f, 0.25f)) c.drawCircle(cx, cy, r * k, Draw.ring)
                Draw.line.strokeWidth = 1.5f * f; Draw.line.color = Draw.mutedColor
                c.drawLine(cx - r, cy, cx + r, cy, Draw.line); c.drawLine(cx, cy - r, cx, cy + r, Draw.line)
                Draw.label(c, "CALIBRATE", cx, cy - 70f * f, 20f * f, Draw.textColor, bold = true)
                Draw.label(c, "red circle = edge of the glass", cx, cy - 46f * f, 12f * f, Draw.mutedColor)
                Draw.label(c, "drag to move  ·  volume ± to scale", cx, cy - 20f * f, 13f * f)
                Draw.label(c, "long-press to save", cx, cy + 2f * f, 13f * f)
                Draw.label(c, Draw.fmt("dx %+.0f  dy %+.0f  scale %.2f", shell.calDx, shell.calDy, shell.calScale), cx, cy + 40f * f, 13f * f, Draw.cpuColor)
                c.restore()
            }
            private var downT = 0L; private var moved = false
            override fun onTouchEvent(e: android.view.MotionEvent): Boolean {
                when (e.actionMasked) {
                    android.view.MotionEvent.ACTION_DOWN -> { lx = e.x; ly = e.y; downT = System.currentTimeMillis(); moved = false }
                    android.view.MotionEvent.ACTION_MOVE -> { if (kotlin.math.hypot(e.x - lx, e.y - ly) > 3f) moved = true; shell.calDx += e.x - lx; shell.calDy += e.y - ly; lx = e.x; ly = e.y; invalidate() }
                    android.view.MotionEvent.ACTION_UP -> if (!moved && System.currentTimeMillis() - downT > 550) { finishCalibration(); }
                }
                return true
            }
        }
        setContentView(overlay)
        calOverlay = overlay
    }

    private var calOverlay: View? = null
    private fun finishCalibration() {
        calibrating = false
        saveCalibration()
        setContentView(root)
        shell.invalidate()
        Toast.makeText(this, "Calibration saved", Toast.LENGTH_SHORT).show()
    }

    private fun saveCalibration() {
        settings.calDx = shell.calDx; settings.calDy = shell.calDy; settings.calScale = shell.calScale
    }

    // ---- misc ---------------------------------------------------------------------------------
    private fun hideSystemUi() {
        @Suppress("DEPRECATION")
        // LAYOUT_* flags keep the window full-size even while the system bars peek in, so touch
        // targets and drawing never disagree.
        window.decorView.systemUiVisibility = (View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                or View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_LAYOUT_STABLE or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION)
    }

    private fun launch(intent: Intent?) {
        if (intent == null) { Toast.makeText(this, "App not installed", Toast.LENGTH_SHORT).show(); return }
        try { startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
        catch (e: Exception) { Toast.makeText(this, "Can't open: ${e.message}", Toast.LENGTH_SHORT).show() }
    }

    private fun promptForServer() {
        val input = EditText(this).apply { setText(settings.server); setSingleLine() }
        AlertDialog.Builder(this).setTitle("PC agent address").setView(input)
            .setPositiveButton("Connect") { _, _ ->
                val url = input.text.toString().trim()
                if (url.isNotEmpty()) { settings.server = url; hub.connect(url) }
            }
            .setNegativeButton("Cancel", null).show()
    }
}
