package com.echoportal

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import ai.picovoice.porcupine.Porcupine
import ai.picovoice.porcupine.PorcupineManager

/**
 * "Jarvis" wake word, detected on the Spot itself with Picovoice Porcupine (built-in keyword).
 *
 * Porcupine owns the microphone while it listens, so a voice session suspends it for the length of
 * the recording and resumes it afterwards. The engine is built on a background thread because
 * Porcupine validates the AccessKey over the network the first time it starts; a failure just
 * leaves the feature off and reports the reason.
 */
class WakeWord(
    private val ctx: Context,
    private val onWake: () -> Unit,
    private val onError: (String) -> Unit = {},
) {
    private val main = Handler(Looper.getMainLooper())
    private var manager: PorcupineManager? = null
    private var key = ""
    private var failedKey = ""       // a key Porcupine rejected: do not revalidate it on every resume
    private var enabled = false
    private var listening = false
    private var visible = false      // activity resumed
    private var micBusy = false      // VoiceFace is recording
    private var building = false
    private var transientReported = false
    private var gen = 0

    val isListening: Boolean get() = listening
    /** True when a key is accepted and the feature is on (may still be building). */
    val isEnabled: Boolean get() = enabled
    private val canListen get() = enabled && visible && !micBusy

    /** Apply settings (called on resume and whenever the menu changes them). */
    fun apply(accessKey: String, on: Boolean, force: Boolean = false) {
        visible = true
        val k = accessKey.trim()
        if (k != key) { key = k; failedKey = ""; transientReported = false; release() }
        if (force) { failedKey = ""; transientReported = false }
        enabled = on && k.isNotEmpty() && k != failedKey
        ensure()
    }

    /** The activity is no longer visible. */
    fun pause() { visible = false; stop() }

    /** A voice session needs the microphone. */
    fun suspendForVoice() { micBusy = true; stop() }

    /** The voice session finished; listen for "Jarvis" again. */
    fun resumeAfterVoice() { micBusy = false; ensure() }

    fun release() {
        stop()
        val old = manager
        manager = null
        gen++
        if (old != null) Thread {
            try { old.delete() } catch (e: Exception) { Log.w(TAG, "delete: ${e.message}") }
        }.start()
    }

    private fun ensure() {
        if (!enabled) { stop(); return }
        if (manager == null) { build(); return }
        if (canListen) start() else stop()
    }

    private fun build() {
        if (building) return
        building = true
        val myGen = ++gen
        val accessKey = key
        Thread {
            try {
                val m = PorcupineManager.Builder()
                    .setAccessKey(accessKey)
                    .setKeyword(Porcupine.BuiltInKeyword.JARVIS)
                    .setSensitivity(0.6f)
                    .build(ctx.applicationContext) { _ ->
                        main.post {
                            // Porcupine may fire just before pause/release; do not wake or start
                            // a recording from a stale engine callback.
                            if (myGen == gen && listening && canListen) onWake()
                        }
                    }
                main.post {
                    building = false
                    if (myGen != gen) {
                        Thread { try { m.delete() } catch (_: Exception) {} }.start()
                        ensure()   // settings changed while building; try again with the new key
                        return@post
                    }
                    manager = m
                    transientReported = false
                    Log.i(TAG, "Jarvis ready")
                    ensure()
                }
            } catch (e: Exception) {
                val msg = e.message ?: "wake word failed"
                Log.w(TAG, "unavailable: $msg")
                main.post {
                    building = false
                    if (myGen == gen) {
                        if (keyRejected(msg)) {
                            // A real key rejection is permanent; stop asking for this key.
                            failedKey = accessKey
                            enabled = false
                            onError(msg)
                        } else if (!transientReported) {
                            // Network/DNS hiccup: keep the feature enabled so the next resume
                            // rebuilds it, but only tell the user once.
                            transientReported = true
                            onError("$msg - will retry")
                        }
                    }
                }
            }
        }.start()
    }

    /** Only cache failures that cannot be fixed by retrying with a network connection. */
    private fun keyRejected(msg: String): Boolean {
        val m = msg.lowercase(java.util.Locale.ROOT)
        return m.contains("invalid accesskey") || m.contains("invalid access key") ||
            m.contains("accesskey is invalid") || m.contains("access key is invalid") ||
            m.contains("unauthorized") || m.contains("not authorized") ||
            m.contains("forbidden") || m.contains("authentication failed") ||
            m.contains(" 401") || m.startsWith("401") ||
            m.contains(" 403") || m.startsWith("403")
    }

    private fun start() {
        val m = manager ?: return
        if (listening) return
        try {
            m.start()
            listening = true
            Log.i(TAG, "listening for Jarvis")
        } catch (e: Exception) {
            Log.w(TAG, "start failed: ${e.message}")
            onError(e.message ?: "wake word failed")
        }
    }

    private fun stop() {
        val m = manager ?: return
        if (!listening) return
        try { m.stop() } catch (e: Exception) { Log.w(TAG, "stop: ${e.message}") }
        listening = false
    }

    companion object { private const val TAG = "EchoPortal.Wake" }
}
