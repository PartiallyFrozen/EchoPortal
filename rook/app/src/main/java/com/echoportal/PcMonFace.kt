package com.echoportal

import android.content.Context
import android.graphics.Canvas
import com.echoportal.Draw.fmt
import org.json.JSONObject

/** The original SpotMon dashboard, hosted as a face: seven pages driven by the "stats" channel. */
class PcMonFace(ctx: Context) : Face(ctx) {
    override val id = "pcmon"
    override val name = "PC Mon"
    override val color = Draw.cpuColor
    override val channels = setOf("stats")

    private val pages: List<Page> = listOf(OverviewPage(), CpuPage(), MemoryPage(), GpuPage(), DisksPage(), NetPage())
    override val pageCount get() = pages.size

    private val history = History(60)
    private var stats: Stats? = null
    private var lastUpdate = 0L

    override fun onEvent(channel: String, data: JSONObject) {
        stats = try { Stats.parse(data.toString()) } catch (e: Exception) { return }
        history.record(stats!!)
        lastUpdate = System.currentTimeMillis()
        invalidate()
    }

    override fun preview(): String {
        val s = stats ?: return "waiting for PC"
        val g = s.gpu
        return if (g != null) fmt("CPU %.0f%%  ·  GPU %.0f%% %d°  ·  RAM %.0f%%", s.cpu.total, g.util3d, g.tempC ?: 0, s.mem.pct)
        else fmt("CPU %.0f%%  ·  RAM %.0f%%", s.cpu.total, s.mem.pct)
    }

    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, page: Int, ambient: Boolean) {
        pages[page.coerceIn(0, pages.size - 1)].draw(c, cx, cy, r, f, stats, history)
        if (stats != null && System.currentTimeMillis() - lastUpdate > 5000) {
            Draw.label(c, "stale — no data for ${(System.currentTimeMillis() - lastUpdate) / 1000}s", cx, cy + 172f * f, 11f * f, Draw.hotColor)
        }
    }

    override fun drawIcon(c: Canvas, x: Float, y: Float, size: Float, selected: Boolean) {
        val col = if (selected) color else Draw.mutedColor
        Draw.ring.strokeWidth = size * 0.09f; Draw.ring.color = col
        c.drawRoundRect(x - size * 0.42f, y - size * 0.32f, x + size * 0.42f, y + size * 0.22f, size * 0.06f, size * 0.06f, Draw.ring)
        Draw.line.strokeWidth = size * 0.09f; Draw.line.color = col
        c.drawLine(x - size * 0.2f, y + size * 0.4f, x + size * 0.2f, y + size * 0.4f, Draw.line)
        // tiny graph inside
        Draw.line.strokeWidth = size * 0.06f
        c.drawLine(x - size * 0.3f, y + size * 0.1f, x - size * 0.12f, y - size * 0.1f, Draw.line)
        c.drawLine(x - size * 0.12f, y - size * 0.1f, x + size * 0.05f, y + size * 0.05f, Draw.line)
        c.drawLine(x + size * 0.05f, y + size * 0.05f, x + size * 0.3f, y - size * 0.18f, Draw.line)
    }
}
