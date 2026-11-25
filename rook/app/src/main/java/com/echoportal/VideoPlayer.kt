package com.echoportal

import android.content.Context
import android.graphics.Matrix
import android.graphics.SurfaceTexture
import android.media.MediaPlayer
import android.media.PlaybackParams
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Surface
import android.view.TextureView
import android.view.View

/**
 * A TextureView that sits UNDER the shell; the shell punches a transparent circle through itself
 * so the video shows inside the round screen. Plays a URL in a loop.
 *
 * The media server can be unreachable for a moment (PC asleep, Wi-Fi roaming), so a failed
 * prepare retries with backoff instead of leaving a dead black circle until the face is reopened.
 */
class VideoPlayer(ctx: Context) : TextureView.SurfaceTextureListener {
    val view = TextureView(ctx).apply {
        surfaceTextureListener = this@VideoPlayer
        isOpaque = true
        visibility = View.GONE
    }
    private val main = Handler(Looper.getMainLooper())
    private var surface: Surface? = null
    private var mp: MediaPlayer? = null
    private var url: String? = null
    private var wantPlay = false
    private var wantPause = false    // a pause tap before prepareAsync finishes must stick
    private var retryMs = 2_000L
    var playing = false
        private set
    /** Muted by default; the face offers an unmute button. */
    var muted = true
        set(v) { field = v; mp?.setVolume(if (v) 0f else 1f, if (v) 0f else 1f) }

    /** Playback rate; Video Clock asks for a slower loop without affecting ComfyUI clips. */
    var playbackSpeed: Float = 1f
        set(v) {
            val rate = v.coerceIn(0.25f, 2f)
            if (field == rate) return
            field = rate
            mp?.let { applyPlaybackSpeed(it) }
        }

    private val retry = Runnable {
        if (!wantPlay) return@Runnable
        url?.let { start(it) }
    }

    fun play(url: String) {
        this.url = url
        wantPlay = true
        wantPause = false
        retryMs = 2_000L
        main.removeCallbacks(retry)
        view.visibility = View.VISIBLE
        if (surface != null) start(url)
    }

    fun stop() {
        wantPlay = false
        wantPause = false
        url = null
        main.removeCallbacks(retry)
        release()
        view.visibility = View.GONE
    }

    /** Pause/resume without releasing the decoder (ComfyUI video outputs). */
    fun toggle() {
        wantPause = !wantPause
        applyPause()
    }

    private fun applyPause() {
        val player = mp ?: return
        try {
            if (wantPause && player.isPlaying) { player.pause(); playing = false }
            else if (!wantPause && !player.isPlaying) { player.start(); playing = true }
        } catch (e: Exception) {
            Log.w(TAG, "pause/resume failed: ${e.message}")
        }
    }

    /** Match the shell's screen calibration (scale about centre, then shift). */
    fun applyCalibration(dx: Float, dy: Float, scale: Float) {
        val cx = view.width / 2f; val cy = view.height / 2f
        view.setTransform(Matrix().apply { setScale(scale, scale, cx, cy); postTranslate(dx, dy) })
    }

    private fun applyPlaybackSpeed(player: MediaPlayer) {
        try {
            player.setPlaybackParams(PlaybackParams().setSpeed(playbackSpeed))
        } catch (e: Exception) {
            Log.w(TAG, "playback speed ${playbackSpeed}x not applied: ${e.message}")
        }
    }

    private fun start(u: String) {
        release()
        try {
            mp = MediaPlayer().apply {
                setSurface(surface)
                setDataSource(u)
                isLooping = true
                setVolume(if (muted) 0f else 1f, if (muted) 0f else 1f)
                setOnPreparedListener {
                    if (wantPause) { it.pause(); playing = false } else { it.start(); playing = true }
                    applyPlaybackSpeed(it)
                    retryMs = 2_000L
                }
                setOnErrorListener { _, what, extra ->
                    Log.w(TAG, "video error $what/$extra")
                    playing = false
                    scheduleRetry()
                    true
                }
                prepareAsync()
            }
        } catch (e: Exception) {
            Log.w(TAG, "video start failed: ${e.message}")
            scheduleRetry()
        }
    }

    private fun scheduleRetry() {
        if (!wantPlay) return
        main.removeCallbacks(retry)
        main.postDelayed(retry, retryMs)
        retryMs = (retryMs * 2).coerceAtMost(60_000L)
    }

    private fun release() {
        try { mp?.stop() } catch (_: Exception) {}
        mp?.release(); mp = null; playing = false
    }

    override fun onSurfaceTextureAvailable(st: SurfaceTexture, w: Int, h: Int) {
        surface = Surface(st)
        if (wantPlay) url?.let { start(it) }
    }
    override fun onSurfaceTextureSizeChanged(st: SurfaceTexture, w: Int, h: Int) {}
    override fun onSurfaceTextureDestroyed(st: SurfaceTexture): Boolean {
        main.removeCallbacks(retry)
        release()
        surface?.release(); surface = null
        return true
    }
    override fun onSurfaceTextureUpdated(st: SurfaceTexture) {}

    companion object { private const val TAG = "EchoPortal.Video" }
}
