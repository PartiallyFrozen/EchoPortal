package com.echoportal

import android.os.Handler
import android.os.Looper
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
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
    private var socket: WebSocket? = null
    private var closed = false
    private var backoffMs = 1000L
    var connected = false
        private set

    fun connect(url: String) {
        this.url = url
        closed = false
        backoffMs = 1000L
        main.removeCallbacksAndMessages(null)
        socket?.cancel()
        open()
    }

    fun close() {
        closed = true
        main.removeCallbacksAndMessages(null)
        socket?.cancel()
        socket = null
        connected = false
    }

    fun send(obj: JSONObject): Boolean = socket?.send(obj.toString()) ?: false
    fun sendBytes(bytes: ByteArray): Boolean = socket?.send(okio.ByteString.of(*bytes)) ?: false

    private fun open() {
        if (closed) return
        onState("connecting to $url")
        socket = http.newWebSocket(Request.Builder().url(url).build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                backoffMs = 1000L
                connected = true
                main.post { onState("") }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val obj = try { JSONObject(text) } catch (e: Exception) { Log.w(TAG, "bad frame"); return }
                val ch = obj.optString("ch", "stats")
                val data = obj.optJSONObject("data") ?: obj
                main.post { onMessage(ch, data, obj) }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                connected = false
                scheduleReconnect("offline: ${t.message ?: "connection failed"}")
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                connected = false
                scheduleReconnect("closed by server")
            }
        })
    }

    private fun scheduleReconnect(why: String) {
        if (closed) return
        val delay = backoffMs
        main.post { onState("$why - retrying in ${delay / 1000}s") }
        main.postDelayed({ open() }, delay)
        backoffMs = (backoffMs * 2).coerceAtMost(15_000L)
    }

    companion object { private const val TAG = "EchoPortal.Hub" }
}
