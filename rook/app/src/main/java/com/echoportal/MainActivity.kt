package com.echoportal

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.graphics.Canvas
import android.graphics.Color
import android.media.AudioManager
import android.media.ToneGenerator
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
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
    private lateinit var voiceFace: VoiceFace
    private lateinit var assistantFace: AssistantFace
    private lateinit var videoClock: VideoClockFace
    private lateinit var wakeWord: WakeWord
    private var lastWake = 0L
    private var calibrating = false
    private var resumed = false

    // mDNS discovery: the PC advertises _echoportal._tcp so the first run needs no typed IP.
    private var nsdManager: NsdManager? = null
    private var discoveryListener: NsdManager.DiscoveryListener? = null
    private var multicastLock: WifiManager.MulticastLock? = null
    private var discovering = false
    private val ui = Handler(Looper.getMainLooper())
    // mDNS can fail silently on some Wi-Fi stacks; do not leave the UI stuck "looking".
    private val discoveryTimeout = Runnable {
        if (discovering) {
            stopDiscovery()
            Toast.makeText(this, "No PC agent found - set the address manually", Toast.LENGTH_LONG).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        hideSystemUi()
        settings = ShellSettings(getSharedPreferences("rook", Context.MODE_PRIVATE))
        if (settings.server.isEmpty()) settings.server = getString(R.string.default_server)
        audio = getSystemService(Context.AUDIO_SERVICE) as AudioManager

        voiceFace = VoiceFace(this)
        assistantFace = AssistantFace(this)
        assistantFace.token = settings.apiToken
        assistantFace.deviceId = settings.deviceId
        assistantFace.deviceName = settings.deviceName
        val videoClock = VideoClockFace(this).also { this.videoClock = it }
        videoClock.clip = settings.videoClip
        videoClock.token = settings.apiToken
        val faces = listOf(
            // Amherst, NY 14228 (ZIP centroid). Change these three values to move the weather.
            ClockFace(this, lat = 43.0408, lon = -78.7812, tz = "America/New_York"),
            videoClock,
            PcMonFace(this),
            voiceFace,
            assistantFace,
            ComfyFace(this),
            TimerFace(this),
            CalendarFace(this),
            WeatherArtFace(this),
        )
        shell = ShellView(this, faces, settings).also {
            it.host = this
            it.calDx = settings.calDx; it.calDy = settings.calDy; it.calScale = settings.calScale
        }
        video = VideoPlayer(this).also { videoClock.player = it }
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
            f.muteVideo = { m -> video.muted = m }
            f.toggleVideo = { video.toggle() }
        }
        setBrightness(settings.brightness)

        hub = Hub(
            onMessage = { ch, data, _ -> shell.onHubMessage(ch, data) },
            // An empty status means the socket is open: that is the "PC is on" signal.
            onState = { s ->
                shell.status = s; shell.pcOnline = s.isEmpty()
                videoClock.mediaBase = if (s.isEmpty()) settings.mediaBase else null
                assistantFace.mediaBase = if (s.isEmpty()) settings.mediaBase else null
            },
        )
        faces.forEach { f -> f.send = { obj -> hub.send(obj) } }
        voiceFace.sendBytes = { b -> hub.sendBytes(b) }
        voiceFace.sendJson = { o -> hub.send(o) }
        wakeWord = WakeWord(this, onWake = { onWakeWord() }, onError = { msg -> onWakeWordError(msg) })
        voiceFace.onMicBusy = { busy ->
            if (busy) wakeWord.suspendForVoice() else wakeWord.resumeAfterVoice()
        }
        // Barge-in: stop queued/spoken reply audio as soon as the user starts talking.
        voiceFace.onVoiceDetected = { assistantFace.bargeIn() }
        if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != android.content.pm.PackageManager.PERMISSION_GRANTED)
            requestPermissions(arrayOf(android.Manifest.permission.RECORD_AUDIO), 2)
        (faces.first { it.id == "clock" } as ClockFace).onStyleChanged = { p -> settings.clockStyle = p }
        if (settings.lastFace == "clock" && intent?.getStringExtra("face") == null) shell.switchTo("clock", settings.clockStyle)
        handleIntent(intent)
        if (settings.server.isEmpty()) discoverAgent()
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    /** `am start -n com.echoportal/.MainActivity --es face pcmon --ei page 3` opens a face/page (testing + automation). */
    private fun handleIntent(intent: Intent?) {
        intent?.getStringExtra("micsrc")?.let { src -> voiceFace.micSource = src }
        when (intent?.getStringExtra("sleep")) { "now" -> shell.sleepNow(); "wake" -> shell.wakeTemporarily() }
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

    override fun onResume() {
        super.onResume()
        resumed = true
        if (shell.sleeping) { wakeWord.pause(); return }
        applyWakeWord()
        connectHub()   // also restarts mDNS if the first attempt timed out with no address
    }

    override fun onPause() {
        super.onPause()
        resumed = false
        // Release the mic/video before closing the hub: a recording Voice face would otherwise
        // keep AudioRecord alive and send its stop op to a later connection.
        shell.pauseCurrentFace()
        wakeWord.pause()
        stopDiscovery()
        hub.close()
        shell.pcOnline = false
        videoClock.mediaBase = null
    }

    /** One place that applies the saved address and token, so no caller forgets the token. */
    private fun connectHub() {
        if (settings.server.isBlank()) {
            // mDNS is the first-run path. If it timed out or the PC booted later, any later
            // "connect" (touch wake, token save) starts a fresh search instead of going silent.
            if (!shell.sleeping && !discovering) discoverAgent()
            return
        }
        hub.connect(settings.server, settings.apiToken, settings.deviceId)
    }

    override fun onDestroy() {
        wakeWord.release()
        stopDiscovery()
        multicastLock?.release()
        multicastLock = null
        super.onDestroy()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemUi()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        // Code 2 is used both at startup and by the wake-word toggle. Either way, a grant means
        // the audio features can start without waiting for an app restart.
        if (requestCode != 2) return
        val granted = grantResults.firstOrNull() == android.content.pm.PackageManager.PERMISSION_GRANTED
        applyWakeWord(force = granted)
        if (granted && wakeWord.isEnabled)
            Toast.makeText(this, "Listening for \"Jarvis\"", Toast.LENGTH_SHORT).show()
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
        val items = arrayOf("Sleep now (until touched)", "Sleep schedule…", "Background…", "Open Settings", "Open Clock app", "PC agent address", "Find PC agent (mDNS)", "Calibrate screen", "Reset calibration", "Dim now",
            "Always on while PC is on: ${if (settings.alwaysOn) "on" else "off"}",
            "PC hub token…",
            "Video clip…",
            "Wake word (Jarvis): ${if (settings.wakeWord) "on" else "off"}",
            "Wake word key…",
            "Device name…")
        AlertDialog.Builder(this).setTitle("EchoPortal").setItems(items) { _, which ->
            when (which) {
                0 -> shell.sleepNow()
                1 -> sleepScheduleDialog()
                2 -> backgroundDialog()
                3 -> launch(Intent(Settings.ACTION_SETTINGS))
                4 -> launch(packageManager.getLaunchIntentForPackage("com.android.deskclock"))
                5 -> promptForServer()
                6 -> discoverAgent()
                7 -> startCalibration()
                8 -> { shell.calDx = -2f; shell.calDy = 26f; shell.calScale = 1.07f; saveCalibration(); shell.invalidate() }
                9 -> shell.enterAmbient()
                10 -> {
                    settings.alwaysOn = !settings.alwaysOn
                    shell.invalidate()
                    Toast.makeText(this,
                        if (settings.alwaysOn) "Always on while the PC is connected" else "Sleep schedule and auto-dim restored",
                        Toast.LENGTH_SHORT).show()
                }
                11 -> apiTokenDialog()
                12 -> videoClipDialog()
                13 -> {
                    if (settings.wakeWord) {
                        settings.wakeWord = false
                        applyWakeWord()
                        Toast.makeText(this, "Wake word off", Toast.LENGTH_SHORT).show()
                    } else if (settings.picovoiceKey.isBlank()) {
                        wakeWordKeyDialog()
                    } else {
                        settings.wakeWord = true
                        enableWakeWord()
                    }
                }
                14 -> wakeWordKeyDialog()
                15 -> deviceNameDialog()
            }
        }.show()
    }

    /** Start/stop Porcupine from the saved settings and reflect it on the Voice face. */
    private fun applyWakeWord(force: Boolean = false) {
        val granted = checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) ==
            android.content.pm.PackageManager.PERMISSION_GRANTED
        val on = settings.wakeWord && settings.picovoiceKey.isNotBlank() && granted
        wakeWord.apply(settings.picovoiceKey, on, force)
        voiceFace.wakeWordOn = wakeWord.isEnabled
        voiceFace.invalidate()
    }

    /** Turn the wake word on only after the mic permission is actually granted. */
    private fun enableWakeWord() {
        if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) !=
            android.content.pm.PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(android.Manifest.permission.RECORD_AUDIO), 2)
            Toast.makeText(this, "Grant microphone access to start \"Jarvis\"", Toast.LENGTH_LONG).show()
            return
        }
        applyWakeWord(force = true)
        Toast.makeText(this,
            if (wakeWord.isEnabled) "Listening for \"Jarvis\"" else "Wake word unavailable - check the key",
            Toast.LENGTH_LONG).show()
    }

    /** "Jarvis" was heard: wake the screen, open Voice and start recording without a tap. */
    private fun onWakeWord() {
        if (!resumed) return
        val now = System.currentTimeMillis()
        if (now - lastWake < 1500) return          // Porcupine can fire twice on one phrase
        lastWake = now
        if (shell.sleeping) shell.wakeTemporarily() else shell.wake()
        assistantFace.bargeIn()               // a wake word spoken over a reply also interrupts it
        voiceFace.setAssistantMode(true)      // "Jarvis" means an assistant turn, not dictation
        shell.switchTo("voice", 0)
        chime()
        // Porcupine has already released the mic; the delay covers slower devices and the
        // AudioRecord setup that follows.
        ui.postDelayed({ voiceFace.startFromWakeWord() }, 200)
    }

    private fun onWakeWordError(msg: String) {
        voiceFace.wakeWordOn = false
        voiceFace.invalidate()
        Toast.makeText(this, "Wake word unavailable: ${msg.take(120)}", Toast.LENGTH_LONG).show()
    }

    /** Picovoice AccessKey (free) — stored on the device, used only to run the built-in keyword. */
    private fun wakeWordKeyDialog() {
        val input = EditText(this).apply {
            setText(settings.picovoiceKey)
            setSingleLine()
            hint = "Picovoice AccessKey"
        }
        AlertDialog.Builder(this).setTitle("Picovoice AccessKey")
            .setMessage("Create a free key at console.picovoice.ai. It is stored on this device and runs \"Jarvis\" offline.")
            .setView(input)
            .setPositiveButton("Save") { _, _ ->
                settings.picovoiceKey = input.text.toString().trim()
                settings.wakeWord = settings.picovoiceKey.isNotEmpty()
                if (settings.wakeWord) enableWakeWord()
                else { applyWakeWord(); Toast.makeText(this, "Wake word off", Toast.LENGTH_SHORT).show() }
            }
            .setNeutralButton("Clear") { _, _ ->
                settings.picovoiceKey = ""
                settings.wakeWord = false
                applyWakeWord()
                Toast.makeText(this, "Wake word off", Toast.LENGTH_SHORT).show()
            }
            .setNegativeButton("Cancel", null).show()
    }

    /** Shared token for the PC hub/API; must match `api_token` in the PC's config.json. */
    private fun apiTokenDialog() {
        val input = EditText(this).apply {
            setText(settings.apiToken)
            setSingleLine()
            hint = "blank = trust the LAN"
        }
        AlertDialog.Builder(this).setTitle("PC hub token")
            .setMessage("Set the same value as api_token in the PC's config.json. Leave blank if the PC has none.")
            .setView(input)
            .setPositiveButton("Save") { _, _ ->
                settings.apiToken = input.text.toString().trim()
                videoClock.token = settings.apiToken
                assistantFace.token = settings.apiToken
                connectHub()
                Toast.makeText(this, if (settings.apiToken.isEmpty()) "Token cleared" else "Token saved", Toast.LENGTH_SHORT).show()
            }
            .setNeutralButton("Clear") { _, _ ->
                settings.apiToken = ""
                videoClock.token = ""
                assistantFace.token = ""
                connectHub()
            }
            .setNegativeButton("Cancel", null).show()
    }

    /** File inside the PC's media/ folder; the agent serves it at /media/<name>. */
    private fun videoClipDialog() {
        val input = EditText(this).apply {
            setText(settings.videoClip)
            setSingleLine()
            hint = "fireplace.mp4"
        }
        AlertDialog.Builder(this).setTitle("Video clip")
            .setMessage("A file in the PC agent's media folder, e.g. fireplace.mp4. Copy a new clip there and type its name.")
            .setView(input)
            .setPositiveButton("Save") { _, _ ->
                settings.videoClip = input.text.toString().trim().ifEmpty { "fireplace.mp4" }
                videoClock.clip = settings.videoClip
                Toast.makeText(this, "Clip: ${settings.videoClip}", Toast.LENGTH_SHORT).show()
            }
            .setNegativeButton("Cancel", null).show()
    }

    /** Readable label for this Spot, shown on the Jarvis face so multiple devices are distinct. */
    private fun deviceNameDialog() {
        val input = EditText(this).apply {
            setText(settings.deviceName)
            setSingleLine()
            hint = "e.g. Echo Spot"
        }
        AlertDialog.Builder(this).setTitle("Device name").setView(input)
            .setPositiveButton("Save") { _, _ ->
                settings.deviceName = input.text.toString().trim().ifEmpty { android.os.Build.MODEL }
                assistantFace.deviceName = settings.deviceName
                shell.invalidate()
                Toast.makeText(this, "Name: ${settings.deviceName}", Toast.LENGTH_SHORT).show()
            }
            .setNegativeButton("Cancel", null).show()
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
        if (sleeping) {
            video.stop(); shell.videoActive = false; shell.pcOnline = false; hub.close()
            videoClock.mediaBase = null
            wakeWord.pause()
        } else {
            applyWakeWord()
            connectHub()
        }
    }

    /** Shows the weekday/weekend off schedule and the weekday morning override. */
    private fun sleepScheduleDialog() {
        val dp = resources.displayMetrics.density
        val summary = android.widget.TextView(this).apply {
            text = shell.sleepScheduleText()
            textSize = 13f
            gravity = android.view.Gravity.CENTER
            setPadding(0, (10 * dp).toInt(), 0, (6 * dp).toInt())
        }
        val sw = android.widget.Switch(this).apply { text = "  Sleep schedule enabled"; isChecked = settings.sleepEnabled }
        val box = android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.VERTICAL
            addView(summary); addView(sw)
            setPadding((16 * dp).toInt(), (8 * dp).toInt(), (16 * dp).toInt(), 0)
        }
        AlertDialog.Builder(this).setTitle("Sleep schedule").setView(box)
            .setPositiveButton("Save") { _, _ ->
                settings.sleepEnabled = sw.isChecked
                shell.stayAwakeUntilMorning = false
                Toast.makeText(this, if (sw.isChecked) shell.sleepScheduleText() else "Sleep schedule off", Toast.LENGTH_LONG).show()
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
                if (url.isNotEmpty()) { settings.server = url; connectHub() }
            }
            .setNeutralButton("Discover") { _, _ -> discoverAgent() }
            .setNegativeButton("Cancel", null).show()
    }

    // ---- mDNS discovery -----------------------------------------------------------------------
    /** Ask the LAN for the PC's advertised _echoportal._tcp service and connect to the first answer. */
    private fun discoverAgent() {
        if (discovering) {
            Toast.makeText(this, "Already looking for the PC agent…", Toast.LENGTH_SHORT).show()
            return
        }
        val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
        if (multicastLock == null && wifi != null) {
            multicastLock = wifi.createMulticastLock("echoportal-mdns").apply {
                setReferenceCounted(false)
                acquire()
            }
        }
        val nsd = getSystemService(Context.NSD_SERVICE) as? NsdManager
        if (nsd == null) {
            Toast.makeText(this, "mDNS is not available on this device", Toast.LENGTH_SHORT).show()
            return
        }
        nsdManager = nsd
        discovering = true
        Toast.makeText(this, "Looking for the PC agent…", Toast.LENGTH_SHORT).show()
        ui.postDelayed(discoveryTimeout, 12_000L)
        val listener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(serviceType: String) {}
            override fun onServiceLost(serviceInfo: NsdServiceInfo) {}
            override fun onDiscoveryStopped(serviceType: String) {}

            override fun onServiceFound(serviceInfo: NsdServiceInfo) {
                if (!serviceInfo.serviceType.contains("_echoportal._tcp", ignoreCase = true)) return
                try {
                    nsd.resolveService(serviceInfo, object : NsdManager.ResolveListener {
                        override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) {}
                        override fun onServiceResolved(serviceInfo: NsdServiceInfo) {
                            val addr = serviceInfo.host ?: return
                            if (addr is java.net.Inet6Address) return   // the agent advertises IPv4
                            val host = addr.hostAddress ?: return
                            val url = "ws://$host:${serviceInfo.port}"
                            ui.post {
                                if (!discovering) return@post
                                stopDiscovery()
                                settings.server = url
                                connectHub()
                                Toast.makeText(this@MainActivity, "Found $url", Toast.LENGTH_SHORT).show()
                            }
                        }
                    })
                } catch (_: Exception) {
                    // resolve can fail while the service is still being announced; the next
                    // onServiceFound retries.
                }
            }

            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                ui.post {
                    stopDiscovery()
                    Toast.makeText(this@MainActivity, "Discovery failed ($errorCode)", Toast.LENGTH_SHORT).show()
                }
            }

            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {
                ui.removeCallbacks(discoveryTimeout)
                discovering = false
            }
        }
        discoveryListener = listener
        try {
            nsd.discoverServices("_echoportal._tcp.", NsdManager.PROTOCOL_DNS_SD, listener)
        } catch (e: Exception) {
            ui.removeCallbacks(discoveryTimeout)
            discovering = false
            discoveryListener = null
            Toast.makeText(this, "Discovery failed: ${e.message}", Toast.LENGTH_SHORT).show()
        }
    }

    private fun stopDiscovery() {
        ui.removeCallbacks(discoveryTimeout)
        discovering = false
        val listener = discoveryListener ?: return
        discoveryListener = null
        try { nsdManager?.stopServiceDiscovery(listener) } catch (_: Exception) {}
    }
}
