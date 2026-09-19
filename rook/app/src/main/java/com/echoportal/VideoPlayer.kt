package com.echoportal

import android.content.Context
import android.graphics.Matrix
import android.graphics.SurfaceTexture
import android.media.MediaPlayer
import android.util.Log
import android.view.Surface
import android.view.TextureView
import android.view.View

/**
 * A TextureView that sits UNDER the shell; the shell punches a transparent circle through itself
 * so the video shows inside the round screen. Plays a URL in a loop.
 */
class VideoPlayer(ctx: Context) : TextureView.SurfaceTextureListener {
    val view = TextureView(ctx).apply {
        surfaceTextureListener = this@VideoPlayer
        isOpaque = true
        visibility = View.GONE
    }
    private var surface: Surface? = null
    private var mp: MediaPlayer? = null
    private var url: String? = null
    var playing = false
        private set
    /** Muted by default; the face offers an unmute button. */
    var muted = true
        set(v) { field = v; mp?.setVolume(if (v) 0f else 1f, if (v) 0f else 1f) }

    fun play(url: String) {
        this.url = url
        view.visibility = View.VISIBLE
        if (surface != null) start(url)
    }

    fun stop() {
        url = null
        release()
        view.visibility = View.GONE
    }

    fun toggle() {
        mp?.let { if (it.isPlaying) { it.pause(); playing = false } else { it.start(); playing = true } }
    }

    /** Match the shell's screen calibration (scale about centre, then shift). */
    fun applyCalibration(dx: Float, dy: Float, scale: Float) {
        val cx = view.width / 2f; val cy = view.height / 2f
        view.setTransform(Matrix().apply { setScale(scale, scale, cx, cy); postTranslate(dx, dy) })
    }

    private fun start(u: String) {
        release()
        try {
            mp = MediaPlayer().apply {
                setSurface(surface)
                setDataSource(u)
                isLooping = true
                setVolume(if (muted) 0f else 1f, if (muted) 0f else 1f)
                setOnPreparedListener { it.start(); playing = true }
                setOnErrorListener { _, what, extra -> Log.w(TAG, "video error $what/$extra"); true }
                prepareAsync()
            }
        } catch (e: Exception) { Log.w(TAG, "video start failed: ${e.message}") }
    }

    private fun release() {
        try { mp?.stop() } catch (_: Exception) {}
        mp?.release(); mp = null; playing = false
    }

    override fun onSurfaceTextureAvailable(st: SurfaceTexture, w: Int, h: Int) {
        surface = Surface(st)
        url?.let { start(it) }
    }
    override fun onSurfaceTextureSizeChanged(st: SurfaceTexture, w: Int, h: Int) {}
    override fun onSurfaceTextureDestroyed(st: SurfaceTexture): Boolean { release(); surface?.release(); surface = null; return true }
    override fun onSurfaceTextureUpdated(st: SurfaceTexture) {}

    companion object { private const val TAG = "EchoPortal.Video" }
}
