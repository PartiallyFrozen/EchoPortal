package com.echoportal

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import com.echoportal.Draw.fmt
import com.echoportal.Draw.label
import kotlin.math.ceil
import kotlin.math.roundToInt
import org.json.JSONObject

/**
 * Timers and alarms. The PC owns the clock, so a timer keeps running while the Spot sleeps or
 * reconnects; this face just draws the snapshot and counts down locally.
 *
 * Page 0: the nearest timer, big, with a progress ring. Tap to cancel (or dismiss when finished).
 * Page 1: every timer. Tap a row to cancel it.
 */
class TimerFace(ctx: Context) : Face(ctx) {
    override val id = "timer"
    override val name = "Timers"
    override val color = Color.parseColor("#FF8A65")
    override val channels = setOf("timer")
    override val pageCount = 2

    private data class Item(
        val id: String, val label: String, val kind: String, val endsAt: Long,
        val durationS: Int, val wake: Boolean, val state: String,
    )

    private var items: List<Item> = emptyList()
    private var skewMs = 0L

    override fun onEvent(channel: String, data: JSONObject) {
        if (!data.has("items")) return
        val arr = data.optJSONArray("items") ?: return
        val list = ArrayList<Item>()
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            list.add(Item(
                o.optString("id"), o.optString("label", "Timer"), o.optString("kind", "timer"),
                o.optLong("ends_at"), o.optInt("duration_s"), o.optBoolean("wake"),
                o.optString("state", "running"),
            ))
        }
        items = list
        val server = data.optLong("now", 0L)
        if (server > 0) skewMs = server - System.currentTimeMillis()
        invalidate()
    }

    override fun onTick() { invalidate() }

    private fun now() = System.currentTimeMillis() + skewMs
    private fun running() = items.filter { it.state == "running" }.sortedBy { it.endsAt }
    private fun done() = items.filter { it.state == "done" }
    private fun ordered() = running() + done()

    // Round up so a freshly set 1-minute timer reads 1:00, not 0:59 on the next frame.
    private fun remaining(t: Item) = ceil((t.endsAt - now()) / 1000.0).toInt().coerceAtLeast(0)

    private fun clock(sec: Int): String {
        val h = sec / 3600
        val m = (sec % 3600) / 60
        val s = sec % 60
        return if (h > 0) fmt("%d:%02d:%02d", h, m, s) else fmt("%d:%02d", m, s)
    }

    override fun preview(): String {
        val t = running().firstOrNull()
        if (t != null) return fmt("%s · %s", t.label.take(18), clock(remaining(t)))
        return if (done().isNotEmpty()) "time's up" else "no timers"
    }

    override fun bgLevel(page: Int): Int = 2

    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, page: Int, ambient: Boolean) {
        if (page == 1) { drawList(c, cx, cy, r, f); return }
        val run = running()
        val finished = done()
        if (run.isEmpty() && finished.isEmpty()) {
            drawEmpty(c, cx, cy, f)
            return
        }
        val t = run.firstOrNull()
        if (t != null) {
            val rem = remaining(t)
            val pct = if (t.durationS > 0) (1.0 - rem.toDouble() / t.durationS).coerceIn(0.0, 1.0) * 100 else 0.0
            Draw.ringArc(c, cx, cy - 26f * f, r * 0.60f, 7f * f, pct, if (t.wake) Draw.ramColor else color)
            label(c, clock(rem), cx, cy - 4f * f, 74f * f, Draw.textColor, bold = true)
            label(c, t.label.take(26), cx, cy + 52f * f, 18f * f, Draw.textColor)
            label(c, if (t.wake) "wake-up light" else "timer", cx, cy + 74f * f, 12f * f, Draw.mutedColor)
            label(c, "tap to cancel  ·  swipe for all", cx, cy + 154f * f, 10f * f, Draw.gridColor)
        } else {
            label(c, "time's up", cx, cy - 12f * f, 32f * f, color, bold = true)
            label(c, finished.first().label.take(28), cx, cy + 22f * f, 17f * f, Draw.textColor)
            label(c, "tap to dismiss", cx, cy + 154f * f, 10f * f, Draw.gridColor)
        }
    }

    private fun drawEmpty(c: Canvas, cx: Float, cy: Float, f: Float) {
        Draw.ring.strokeWidth = 5f * f; Draw.ring.color = color
        c.drawCircle(cx, cy - 46f * f, 46f * f, Draw.ring)
        Draw.line.strokeWidth = 5f * f; Draw.line.color = color
        c.drawLine(cx, cy - 46f * f, cx, cy - 76f * f, Draw.line)
        c.drawLine(cx, cy - 46f * f, cx + 24f * f, cy - 32f * f, Draw.line)
        label(c, "no timers", cx, cy + 34f * f, 22f * f, Draw.textColor, bold = true)
        label(c, "say \"set a timer for 10 minutes\"", cx, cy + 60f * f, 12f * f, Draw.mutedColor)
        label(c, "or POST /api/timer on the agent", cx, cy + 80f * f, 10f * f, Draw.gridColor)
    }

    private fun drawList(c: Canvas, cx: Float, cy: Float, r: Float, f: Float) {
        Draw.header(c, cx, cy, f, "Timers", if (items.isEmpty()) "nothing scheduled" else "${items.size} total", color)
        val list = ordered().take(6)
        if (list.isEmpty()) {
            label(c, "say \"set a timer for 10 minutes\"", cx, cy + 10f * f, 13f * f, Draw.mutedColor)
            return
        }
        var y = cy - 108f * f
        val lx = cx - r * 0.58f
        val rx = cx + r * 0.58f
        for (t in list) {
            val state = t.state
            val right = when (state) {
                "running" -> clock(remaining(t))
                else -> "done"
            }
            label(c, t.label.take(22), lx, y, 14f * f, if (state == "running") Draw.textColor else Draw.mutedColor, Paint.Align.LEFT)
            label(c, right, rx, y, 14f * f, if (state == "running") (if (t.wake) Draw.ramColor else color) else Draw.hotColor, Paint.Align.RIGHT)
            y += 36f * f
        }
        label(c, "tap a row to cancel", cx, cy + 154f * f, 10f * f, Draw.gridColor)
    }

    override fun onTap(x: Float, y: Float, cx: Float, cy: Float, r: Float, page: Int): Boolean {
        if (page == 0) {
            val t = running().firstOrNull()
            if (t != null) sendOp("cancel", t.id)
            else if (done().isNotEmpty()) sendOp("dismiss")
            return true
        }
        val f = r / 240f
        val start = cy - 108f * f
        val rowH = 36f * f
        // Round to the nearest row: truncating toward zero made a tap just above row 0 select it.
        val idx = ((y - start) / rowH).roundToInt()
        val list = ordered().take(6)
        if (idx in list.indices) {
            val t = list[idx]
            sendOp(if (t.state == "running") "cancel" else "dismiss", t.id)
        }
        return true
    }

    private fun sendOp(op: String, id: String? = null) {
        val data = JSONObject().put("op", op)
        if (id != null) data.put("id", id)
        send(JSONObject().put("ch", "timer").put("data", data))
    }

    override fun drawIcon(c: Canvas, x: Float, y: Float, size: Float, selected: Boolean) {
        val col = if (selected) color else Draw.mutedColor
        Draw.ring.strokeWidth = size * 0.09f; Draw.ring.color = col
        c.drawCircle(x, y, size * 0.40f, Draw.ring)
        Draw.line.strokeWidth = size * 0.08f; Draw.line.color = col
        c.drawLine(x, y, x, y - size * 0.26f, Draw.line)
        c.drawLine(x, y, x + size * 0.18f, y + size * 0.12f, Draw.line)
        c.drawCircle(x, y - size * 0.46f, size * 0.07f, Draw.fill.apply { color = col; alpha = 255 })
    }
}
