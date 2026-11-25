package com.echoportal

import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import okio.ByteString.Companion.toByteString
import java.util.concurrent.TimeUnit

/**
 * One WebSocket to the PC agent. Every message is a JSON object with a "ch" (channel) field;
 * "data" carries the payload. Reconnects with backoff. Callbacks land on the main thread.
 */
class Hub(
    private val onMessage: (channel: String, data: JSONObject, raw: JSONObject) -> Unit,
    private val onState: (String) -> Unit,
) {
    private val main = Handler(Looper.getMainLooper())
    private val http = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(15, TimeUnit.SECONDS)
        .build()

    private var url = ""
    private var token = ""
    private var deviceId = ""
    private var socket: WebSocket? = null
    private var closed = false
    private var backoffMs = 1000L
    // Every open() takes a new number; callbacks from an older socket are ignored so a late
    // failure cannot schedule a second reconnect loop or overwrite the current state.
    private var generation = 0
    var connected = false
        private set

    /** `deviceId` is appended as a `device` query parameter so the hub can attribute a socket. */
    fun connect(url: String, token: String = "", deviceId: String = "") {
        this.url = url.trim()
        this.token = token.trim()
        this.deviceId = deviceId.trim()
        closed = false
        backoffMs = 1000L
        main.removeCallbacksAndMessages(null)
        generation++
        socket?.cancel()
        socket = null
        if (this.url.isEmpty()) {
            // Nothing to retry: the address is set from the long-press menu.
            closed = true
            connected = false
            onState("PC address not set")
            return
        }
        open()
    }

    fun close() {
        closed = true
        main.removeCallbacksAndMessages(null)
        generation++
        socket?.cancel()
        socket = null
        connected = false
    }

    fun send(obj: JSONObject): Boolean = socket?.send(obj.toString()) ?: false
    fun sendBytes(bytes: ByteArray): Boolean = socket?.send(bytes.toByteString()) ?: false

    private fun open() {
        if (closed) return
        val gen = ++generation
        onState("connecting to $url")
        // Unknown query params are ignored by older hubs, so this stays backward compatible.
        // OkHttp's HttpUrl rejects ws:// URLs, so use Uri (which keeps the ws scheme) here.
        val wsBuilder = Uri.parse(url).buildUpon()
        if (deviceId.isNotEmpty()) wsBuilder.appendQueryParameter("device", deviceId)
        // The agent also accepts ?token= on the handshake. Uri encodes non-ASCII safely, unlike
        // OkHttp's header builder, which rejects such values before the request is sent.
        if (token.isNotEmpty()) wsBuilder.appendQueryParameter("token", token)
        val wsUrl = wsBuilder.build().toString()
        val request = try {
            Request.Builder().url(wsUrl).build()
        } catch (e: IllegalArgumentException) {
            connected = false
            onState("invalid PC address: $url")
            return
        }
        val ws = http.newWebSocket(request, object : WebSocketListener() {
            private fun stale() = closed || gen != generation

            override fun onOpen(webSocket: WebSocket, response: Response) {
                if (stale()) { webSocket.cancel(); return }
                backoffMs = 1000L
                connected = true
                main.post { if (!stale()) onState("") }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                if (stale()) return
                val obj = try { JSONObject(text) } catch (e: Exception) { Log.w(TAG, "bad frame"); return }
                val ch = obj.optString("ch", "stats")
                val data = obj.optJSONObject("data") ?: obj
                main.post { if (!stale()) onMessage(ch, data, obj) }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (stale()) return
                connected = false
                scheduleReconnect("offline: ${t.message ?: "connection failed"}")
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (stale()) return
                connected = false
                if (code == 1008) {
                    // Retrying with the same token cannot succeed; slow down and say why.
                    scheduleReconnect("hub rejected the token", 30_000L)
                } else {
                    scheduleReconnect("closed by server")
                }
            }
        })
        socket = ws
    }

    private fun scheduleReconnect(why: String, delayMs: Long = backoffMs) {
        if (closed) return
        val delay = delayMs
        main.post { onState("$why - retrying in ${delay / 1000}s") }
        main.postDelayed({ open() }, delay)
        // A token rejection keeps its 30 s cadence until connectHub() resets backoffMs.
        val cap = if (delayMs >= 30_000L) 30_000L else 15_000L
        backoffMs = (delay * 2).coerceAtMost(cap)
    }

    companion object { private const val TAG = "EchoPortal.Hub" }
}
