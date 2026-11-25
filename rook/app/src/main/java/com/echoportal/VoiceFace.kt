package com.echoportal

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.echoportal.Draw.fmt
import com.echoportal.Draw.label
import org.json.JSONObject
import kotlin.math.hypot
import kotlin.math.min
import kotlin.math.sqrt

/**
 * Voice / dictation face.
 *
 *   IDLE      big mic. tap = start listening. bottom pill toggles the target (focused window / Claude Code)
 *   LISTEN    ring pulses with the mic level; stops on ~1.2 s of silence, 30 s max, or tap
 *   THINK     PC is transcribing (Whisper on the GPU)
 *   REVIEW    transcript + [discard] [insert] [send]  (send = type + Enter, insert = type only)
 *   DONE      "sent to <window>" for a moment, then back to IDLE
 *
 * Audio: 16 kHz mono PCM16 streamed to the PC agent as binary WebSocket frames on the "voice" channel.
 */
class VoiceFace(ctx: Context) : Face(ctx) {
    override val id = "voice"
    override val name = "Voice"
    override val color = Color.parseColor("#F06292")
    override val channels = setOf("voice")

    /** Set by the shell: sends a raw binary frame to the hub. */
    var sendBytes: (ByteArray) -> Boolean = { false }

    /**
     * Set by the activity: true just before AudioRecord opens the mic and false once it is
     * released, so the always-listening wake word can hand the microphone over cleanly.
     */
    var onMicBusy: (Boolean) -> Unit = {}

    /** Fired once on the rising edge of detected speech; used to barge in on reply audio. */
    var onVoiceDetected: (() -> Unit)? = null

    /** Set by the activity when the on-device "Jarvis" wake word is listening. */
    var wakeWordOn = false

    private enum class State { IDLE, LISTEN, THINK, REVIEW, DONE, ERROR }
    private var state = State.IDLE
    // 0 = focused window, 1 = Claude Code, 2 = Jarvis assistant
    private var targetMode = 0
    private val assistantMode get() = targetMode == 2
    private var transcript = ""
    private var targetWindow = ""
    private var message = ""
    private var level = 0f            // 0..1 smoothed mic level
    private var listenStart = 0L
    @Volatile private var lastSpeech = 0L
    @Volatile private var spoke = false
    private var rec: AudioRecord? = null
    private var recThread: Thread? = null
    @Volatile private var recording = false
    private val main = Handler(Looper.getMainLooper())
    private val rect = android.graphics.RectF()

    private val sampleRate = 16000
    private val silenceMs = 1200L
    private val maxMs = 30_000L
    private val speechThreshold = 0.0025f   // the Spot mic array is ~30 dB quieter than a phone mic

    // ---- lifecycle ------------------------------------------------------------------------
    override fun onHide() {
        stopListening(send = false)
        if (state == State.LISTEN) state = State.IDLE
        setAssistantMode(false)   // a one-shot Jarvis turn must not turn the next dictation into one
    }

    override fun onTick() {
        val now = System.currentTimeMillis()
        if (state == State.DONE && now - doneAt > (if (message.isNotEmpty()) 4500 else 2500)) { state = State.IDLE; message = ""; invalidate() }
        if (state == State.ERROR && now - doneAt > 4000) { state = State.IDLE; invalidate() }
        // A dropped reply must not leave the spinner up forever.
        if (state == State.THINK && now - thinkAt > 60_000) fail("no reply from PC")
    }
    private var doneAt = 0L
    private var thinkAt = 0L

    // ---- hub events ----------------------------------------------------------------------
    override fun onEvent(channel: String, data: JSONObject) {
        when (data.optString("op")) {
            "transcript" -> {
                transcript = data.optString("text").trim()
                targetWindow = data.optString("target")
                message = ""
                if (transcript.isEmpty()) { fail("didn't catch that") } else { state = State.REVIEW }
            }
            "sent" -> { targetWindow = data.optString("window"); message = ""; doneAt = System.currentTimeMillis(); state = State.DONE }
            "command" -> {
                // The PC recognised a timer/alarm phrase and did it; show its confirmation.
                transcript = ""
                targetWindow = data.optString("target")
                message = data.optString("text", "done")
                doneAt = System.currentTimeMillis()
                state = State.DONE
            }
            "assistant" -> {
                // The PC answered through Jarvis; the shell switches to the Assistant face.
                transcript = ""
                message = data.optString("text", "asked Jarvis")
                doneAt = System.currentTimeMillis()
                state = State.DONE
            }
            "focus" -> targetWindow = data.optString("window")
            "error" -> fail(data.optString("text", "error"))
        }
        invalidate()
    }

    private fun fail(msg: String) { message = msg; doneAt = System.currentTimeMillis(); state = State.ERROR; invalidate() }

    private fun op(op: String, extra: (JSONObject) -> Unit = {}): Boolean {
        val o = JSONObject().put("ch", "voice").put("data", JSONObject().put("op", op).also(extra))
        val ok = sendJson(o)
        Log.i(TAG, "op $op sent=$ok")
        return ok
    }
    /** Set by the shell: sends a JSON message, returns false if the hub is down. */
    var sendJson: (JSONObject) -> Boolean = { false }

    // ---- recording -------------------------------------------------------------------------
    /** Capture source, switchable at runtime for diagnosis: `--es micsrc mic|voice|unprocessed|default|camcorder`. */
    var micSource = "voice"
    private fun audioSource(): Int = when (micSource) {
        "mic" -> MediaRecorder.AudioSource.MIC
        "unprocessed" -> MediaRecorder.AudioSource.UNPROCESSED
        "default" -> MediaRecorder.AudioSource.DEFAULT
        "camcorder" -> MediaRecorder.AudioSource.CAMCORDER
        else -> MediaRecorder.AudioSource.VOICE_RECOGNITION
    }.also { Log.i(TAG, "audio source=$micSource ($it)") }

    private fun hasMicPermission() =
        ctx.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    private fun startListening() {
        if (recording) return
        // Porcupine owns the mic while it listens; make it let go before AudioRecord opens it.
        onMicBusy(true)
        if (!hasMicPermission()) { onMicBusy(false); fail("mic permission missing"); return }
        val minBuf = AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val r = try {
            AudioRecord(audioSource(), sampleRate, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, maxOf(minBuf, sampleRate))   // 0.5 s buffer
        } catch (e: Exception) { onMicBusy(false); fail("mic: ${e.message}"); return }
        if (r.state != AudioRecord.STATE_INITIALIZED) { onMicBusy(false); fail("mic init failed"); r.release(); return }
        if (!op("start") { it.put("rate", sampleRate) }) {
            Log.w(TAG, "hub is down; not starting the mic")
            try { r.release() } catch (_: Exception) {}
            onMicBusy(false)
            fail("PC offline")
            return
        }
        rec = r
        recording = true
        spoke = false
        level = 0f
        message = ""
        listenStart = System.currentTimeMillis(); lastSpeech = listenStart
        state = State.LISTEN
        r.startRecording()
        recThread = Thread({
            val chunk = ByteArray(640)   // 20 ms
            var peak = 0f; var frames = 0
            while (recording) {
                val n = try {
                    r.read(chunk, 0, chunk.size)
                } catch (e: Exception) {
                    Log.w(TAG, "mic read failed: ${e.message}")
                    main.post { stopListening(send = false) }
                    break
                }
                if (n <= 0) continue
                // RMS for the meter + VAD
                var acc = 0.0
                for (i in 0 until n / 2) { val s = ((chunk[2 * i + 1].toInt() shl 8) or (chunk[2 * i].toInt() and 0xff)).toShort(); acc += s * s.toDouble() }
                val rms = (sqrt(acc / (n / 2)) / 32768.0).toFloat()
                val now = System.currentTimeMillis()
                if (rms > peak) peak = rms
                if (++frames % 50 == 0) { Log.i(TAG, "listening: ${frames / 50}s peak rms=%.4f spoke=$spoke".format(peak)); peak = 0f }
                // VAD decisions run on the recorder thread so silence detection cannot depend on
                // when the main thread gets around to a posted UI update.
                if (rms > speechThreshold) {
                    if (!spoke) {
                        spoke = true
                        main.post { onVoiceDetected?.invoke() }
                    }
                    lastSpeech = now
                }
                main.post {
                    level = level * 0.6f + rms * 0.4f * 60f
                    invalidate()
                }
                sendBytes(if (n == chunk.size) chunk else chunk.copyOf(n))
                val silentLongEnough = spoke && now - lastSpeech > silenceMs
                if (silentLongEnough || now - listenStart > maxMs) {
                    main.post { stopListening(send = true) }
                    break
                }
            }
        }, "voice-rec").also { it.start() }
        invalidate()
    }

    private fun stopListening(send: Boolean) {
        if (!recording) return
        recording = false
        try { rec?.stop() } catch (_: Exception) {}
        rec?.release(); rec = null
        onMicBusy(false)
        if (send) {
            if (!spoke) Log.i(TAG, "stop: VAD heard nothing, sending anyway")
            state = State.THINK
            thinkAt = System.currentTimeMillis()
            if (!op("stop") { it.put("assistant", assistantMode) }) fail("PC offline")
        } else op("cancel")
        invalidate()
    }

    /**
     * Wake word fired: start listening without a tap. A pending review is left alone so an
     * accidental detection cannot throw away the transcript on screen.
     */
    fun startFromWakeWord(): Boolean {
        if (recording || state == State.LISTEN || state == State.THINK || state == State.REVIEW) return false
        startListening()
        return state == State.LISTEN
    }

    /** Called when the on-device wake word fires: the next utterance becomes an assistant turn. */
    fun setAssistantMode(on: Boolean) {
        targetMode = if (on) 2 else 0
        invalidate()
    }

    // ---- touch --------------------------------------------------------------------------------
    override fun onTap(x: Float, y: Float, cx: Float, cy: Float, r: Float, page: Int): Boolean {
        val f = r / 240f
        when (state) {
            State.IDLE, State.DONE, State.ERROR -> {
                if (y > cy + 120f * f) { targetMode = (targetMode + 1) % 3; invalidate() }   // target pill
                else startListening()
            }
            State.LISTEN -> stopListening(send = true)
            State.THINK -> {}
            State.REVIEW -> {
                val by = cy + 118f * f
                if (hypot(x - (cx - 120f * f), y - by) < 34f * f) { state = State.IDLE; invalidate() }          // discard
                else if (hypot(x - cx, y - by) < 34f * f) sendText(enter = false)                                 // insert
                else if (hypot(x - (cx + 120f * f), y - by) < 34f * f) sendText(enter = true)                     // send
            }
        }
        return true
    }

    private fun sendText(enter: Boolean) {
        state = State.THINK; message = "typing…"
        thinkAt = System.currentTimeMillis()
        if (!op("send") { it.put("text", transcript).put("enter", enter).put("target", if (targetMode == 1) "claude" else "focused") })
            fail("PC offline")
        invalidate()
    }

    override fun onKey(keyCode: Int): Boolean = false

    // ---- drawing ---------------------------------------------------------------------------
    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, page: Int, ambient: Boolean) {
        when (state) {
            State.IDLE, State.DONE, State.ERROR -> {
                StubFace.mic(c, cx, cy - 40f * f, 110f * f, if (state == State.ERROR) Draw.hotColor else color)
                label(c, when (state) { State.DONE -> message.ifEmpty { "sent ✓" }; State.ERROR -> message; else -> if (wakeWordOn) "say \"Jarvis\" or tap" else "tap to talk" },
                    cx, cy + 56f * f, 18f * f, if (state == State.ERROR) Draw.hotColor else Draw.textColor, bold = state != State.IDLE)
                if (state == State.DONE && message.isEmpty() && targetWindow.isNotEmpty()) label(c, "→ " + targetWindow.take(36), cx, cy + 78f * f, 12f * f, Draw.mutedColor)
                drawTargetPill(c, cx, cy, r, f)
            }
            State.LISTEN -> {
                val elapsed = (System.currentTimeMillis() - listenStart) / 1000
                val ringR = r * 0.62f + r * 0.18f * min(level, 1f)
                Draw.ring.strokeWidth = 6f * f; Draw.ring.color = color; Draw.ring.alpha = 200
                c.drawCircle(cx, cy - 10f * f, ringR, Draw.ring)
                Draw.ring.alpha = 255
                Draw.ring.strokeWidth = 2f * f; Draw.ring.color = Draw.dimColor
                c.drawCircle(cx, cy - 10f * f, r * 0.62f, Draw.ring)
                StubFace.mic(c, cx, cy - 20f * f, 100f * f, color)
                label(c, if (spoke) "listening…" else "go ahead", cx, cy + 62f * f, 18f * f, Draw.textColor, bold = true)
                label(c, fmt("%d s  ·  tap to stop", elapsed), cx, cy + 84f * f, 12f * f, Draw.mutedColor)
            }
            State.THINK -> {
                Draw.ring.strokeWidth = 5f * f; Draw.ring.color = Draw.dimColor
                c.drawCircle(cx, cy - 30f * f, 50f * f, Draw.ring)
                val a = ((System.currentTimeMillis() / 4) % 360).toFloat()
                rect.set(cx - 50f * f, cy - 80f * f, cx + 50f * f, cy + 20f * f)
                Draw.ring.color = color
                c.drawArc(rect, a, 80f, false, Draw.ring)
                label(c, if (message == "typing…") message else "transcribing…", cx, cy + 60f * f, 18f * f, Draw.textColor, bold = true)
                invalidate()
            }
            State.REVIEW -> {
                label(c, "→ " + (if (targetMode == 1) "Claude Code" else targetWindow.ifEmpty { "focused window" }).take(38), cx, cy - 150f * f, 12f * f, Draw.mutedColor)
                drawWrapped(c, transcript, cx, cy - 118f * f, r, f)
                val by = cy + 118f * f
                button(c, cx - 120f * f, by, 32f * f, "✕", "discard", Draw.dimColor, Draw.textColor, f)
                button(c, cx, by, 32f * f, "⤓", "insert", Draw.dimColor, Draw.textColor, f)
                button(c, cx + 120f * f, by, 32f * f, "⏎", "send", color, Color.BLACK, f)
            }
        }
    }

    private fun drawTargetPill(c: Canvas, cx: Float, cy: Float, r: Float, f: Float) {
        val txt = when (targetMode) {
            1 -> "→ Claude Code"
            2 -> "→ Jarvis"
            else -> "→ focused window"
        }
        val w = 150f * f; val y = cy + 130f * f
        Draw.fill.color = if (targetMode == 0) Draw.dimColor else color; Draw.fill.alpha = 255
        c.drawRoundRect(cx - w / 2, y, cx + w / 2, y + 28f * f, 14f * f, 14f * f, Draw.fill)
        label(c, txt, cx, y + 19f * f, 12f * f, if (targetMode == 0) Draw.textColor else Color.BLACK, bold = true)
        label(c, "tap to switch target", cx, y + 44f * f, 9f * f, Draw.gridColor)
    }

    private fun button(c: Canvas, x: Float, y: Float, rad: Float, glyph: String, cap: String, bg: Int, fg: Int, f: Float) {
        Draw.fill.color = bg; Draw.fill.alpha = 255
        c.drawCircle(x, y, rad, Draw.fill)
        label(c, glyph, x, y + 9f * f, 24f * f, fg)
        label(c, cap, x, y + rad + 14f * f, 10f * f, Draw.mutedColor)
    }

    /** Word-wrap the transcript into the circle between yTop and the buttons; shrinks text if long. */
    private fun drawWrapped(c: Canvas, text: String, cx: Float, yTop: Float, r: Float, f: Float) {
        var size = if (text.length > 160) 14f * f else if (text.length > 80) 17f * f else 21f * f
        val yBottom = cy0(yTop, r, f)
        for (attempt in 0..3) {
            Draw.text.textSize = size; Draw.text.typeface = android.graphics.Typeface.DEFAULT
            val lines = wrap(text, size, cx, yTop, r, f)
            val lh = size * 1.25f
            if (yTop + lines.size * lh <= yBottom || attempt == 3) {
                var y = yTop + size
                for (ln in lines) { label(c, ln, cx, y, size, Draw.textColor); y += lh }
                return
            }
            size *= 0.85f
        }
    }
    private fun cy0(yTop: Float, r: Float, f: Float) = yTop + 190f * f   // space above the buttons

    private fun wrap(text: String, size: Float, cx: Float, yTop: Float, r: Float, f: Float): List<String> {
        val out = ArrayList<String>()
        val cy = yTop + 118f * f   // circle centre in this coordinate frame (yTop = cy - 118f)
        var y = yTop + size
        var line = StringBuilder()
        Draw.text.textSize = size
        for (word in text.split(Regex("\\s+"))) {
            val dy = y - cy
            val halfChord = sqrt((r * r - dy * dy).coerceAtLeast(0f)) - 18f * f
            val candidate = if (line.isEmpty()) word else "$line $word"
            if (Draw.text.measureText(candidate) <= 2 * halfChord || line.isEmpty()) { line = StringBuilder(candidate) }
            else { out.add(line.toString()); line = StringBuilder(word); y += size * 1.25f }
        }
        if (line.isNotEmpty()) out.add(line.toString())
        return out
    }

    override fun drawIcon(c: Canvas, x: Float, y: Float, size: Float, selected: Boolean) =
        StubFace.mic(c, x, y, size, if (selected) color else Draw.mutedColor)

    override fun preview() = when (targetMode) {
        1 -> "tap to talk → Claude Code"
        2 -> "tap to talk → Jarvis"
        else -> "tap to talk → focused window"
    }

    companion object { private const val TAG = "EchoPortal.Voice" }
}
