package com.echoportal

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import com.echoportal.Draw.fmt
import com.echoportal.Draw.label
import org.json.JSONObject
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * Agenda: the next week of calendar events, pushed by the PC agent from read-only ICS feeds.
 *
 * Page 0: the next few events, with today/tomorrow called out.
 * Page 1: a day-by-day look at the whole configured window. Tap to ask the PC to refresh now.
 */
class CalendarFace(ctx: Context) : Face(ctx) {
    override val id = "calendar"
    override val name = "Agenda"
    override val color = Color.parseColor("#81C784")
    override val channels = setOf("calendar")
    override val pageCount = 2

    private data class Event(
        val start: Long, val end: Long, val allDay: Boolean,
        val title: String, val location: String,
    )

    private var events: List<Event> = emptyList()
    private var configured = false
    private var error: String? = null
    private var updated = 0L

    private val timeFmt = DateTimeFormatter.ofPattern("h:mm a", Locale.US)
    private val dayFmt = DateTimeFormatter.ofPattern("EEE", Locale.US)
    private val shortDayFmt = DateTimeFormatter.ofPattern("EEE d", Locale.US)

    override fun onEvent(channel: String, data: JSONObject) {
        if (data.has("configured")) configured = data.optBoolean("configured")
        if (data.has("error")) error = if (data.isNull("error")) null else data.optString("error")
        if (data.has("updated")) updated = data.optLong("updated")
        val arr = data.optJSONArray("events") ?: run { invalidate(); return }
        val list = ArrayList<Event>()
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            list.add(Event(
                o.optLong("start"), o.optLong("end"), o.optBoolean("all_day"),
                o.optString("title", "(untitled)"), o.optString("location"),
            ))
        }
        events = list.sortedBy { it.start }
        invalidate()
    }

    override fun onTick() { invalidate() }

    private fun upcoming(limit: Int): List<Event> {
        val now = System.currentTimeMillis()
        return events.filter { it.end >= now }.take(limit)
    }

    private fun at(ms: Long) = Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault())

    private fun timeLabel(e: Event): String {
        if (e.allDay) return "all day"
        val t = at(e.start)
        val today = LocalDate.now()
        val hm = t.format(timeFmt)
        return when (t.toLocalDate()) {
            today -> hm
            today.plusDays(1) -> "tmrw $hm"
            else -> t.format(dayFmt) + " " + hm
        }
    }

    override fun preview(): String {
        val next = upcoming(1).firstOrNull() ?: return if (configured) "nothing coming up" else "no calendar configured"
        return fmt("%s · %s", timeLabel(next), next.title.take(20))
    }

    override fun bgLevel(page: Int): Int = 2

    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, page: Int, ambient: Boolean) {
        val list = upcoming(if (page == 1) 8 else 4)
        if (!configured) {
            drawEmpty(c, cx, cy, f, "no calendar configured", "add calendar_urls to config.json", "read-only ICS links from Google or Outlook")
            return
        }
        if (list.isEmpty()) {
            val sub = error?.let { it.take(38) } ?: "next 7 days"
            drawEmpty(c, cx, cy, f, "nothing coming up", sub, "tap to refresh")
            return
        }
        if (page == 1) { drawWeek(c, cx, cy, r, f, list); return }

        Draw.header(c, cx, cy, f, "Agenda", if (error != null) "offline · showing last sync" else "next up", color)
        var y = cy - 104f * f
        val lx = cx - r * 0.62f
        val rx = cx + r * 0.62f
        for (e in list) {
            label(c, timeLabel(e), lx, y, 12f * f, if (e.allDay) Draw.ramColor else color, Paint.Align.LEFT)
            label(c, e.title.take(26), rx, y, 13f * f, Draw.textColor, Paint.Align.RIGHT)
            if (e.location.isNotEmpty()) label(c, "· " + e.location.take(24), rx, y + 14f * f, 9f * f, Draw.mutedColor, Paint.Align.RIGHT)
            y += 42f * f
        }
        label(c, "tap to refresh  ·  swipe for the week", cx, cy + 154f * f, 10f * f, Draw.gridColor)
    }

    private fun drawEmpty(c: Canvas, cx: Float, cy: Float, f: Float, title: String, sub: String, hint: String) {
        Draw.ring.strokeWidth = 4f * f; Draw.ring.color = color
        c.drawRoundRect(cx - 44f * f, cy - 90f * f, cx + 44f * f, cy - 14f * f, 8f * f, 8f * f, Draw.ring)
        Draw.line.strokeWidth = 4f * f; Draw.line.color = color
        c.drawLine(cx - 54f * f, cy - 66f * f, cx + 54f * f, cy - 66f * f, Draw.line)
        c.drawLine(cx - 24f * f, cy - 102f * f, cx - 24f * f, cy - 84f * f, Draw.line)
        c.drawLine(cx + 24f * f, cy - 102f * f, cx + 24f * f, cy - 84f * f, Draw.line)
        label(c, title, cx, cy + 22f * f, 22f * f, Draw.textColor, bold = true)
        label(c, sub, cx, cy + 48f * f, 12f * f, Draw.mutedColor)
        label(c, hint, cx, cy + 68f * f, 10f * f, Draw.gridColor)
    }

    private fun drawWeek(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, list: List<Event>) {
        Draw.header(c, cx, cy, f, "This week", "${list.size} upcoming", color)
        var y = cy - 104f * f
        val lx = cx - r * 0.62f
        val rx = cx + r * 0.62f
        var lastDay: LocalDate? = null
        for (e in list) {
            val day = at(e.start).toLocalDate()
            val dayText = if (day == LocalDate.now()) "Today" else day.format(shortDayFmt)
            label(c, if (day == lastDay) "" else dayText, lx, y, 11f * f, Draw.mutedColor, Paint.Align.LEFT)
            val time = if (e.allDay) "all day" else at(e.start).format(timeFmt)
            label(c, time, lx, y + 14f * f, 10f * f, if (e.allDay) Draw.ramColor else color, Paint.Align.LEFT)
            label(c, e.title.take(22), rx, y + 6f * f, 12f * f, Draw.textColor, Paint.Align.RIGHT)
            lastDay = day
            y += 36f * f
        }
        label(c, "tap to refresh", cx, cy + 154f * f, 10f * f, Draw.gridColor)
    }

    override fun onTap(x: Float, y: Float, cx: Float, cy: Float, r: Float, page: Int): Boolean {
        send(JSONObject().put("ch", "calendar").put("data", JSONObject().put("op", "refresh")))
        return true
    }

    override fun drawIcon(c: Canvas, x: Float, y: Float, size: Float, selected: Boolean) {
        val col = if (selected) color else Draw.mutedColor
        Draw.ring.strokeWidth = size * 0.08f; Draw.ring.color = col
        c.drawRoundRect(x - size * 0.34f, y - size * 0.30f, x + size * 0.34f, y + size * 0.38f, size * 0.08f, size * 0.08f, Draw.ring)
        Draw.line.strokeWidth = size * 0.07f; Draw.line.color = col
        c.drawLine(x - size * 0.34f, y - size * 0.10f, x + size * 0.34f, y - size * 0.10f, Draw.line)
        c.drawLine(x - size * 0.18f, y - size * 0.42f, x - size * 0.18f, y - size * 0.24f, Draw.line)
        c.drawLine(x + size * 0.18f, y - size * 0.42f, x + size * 0.18f, y - size * 0.24f, Draw.line)
        Draw.fill.color = col; Draw.fill.alpha = 255
        for (i in 0..2) c.drawCircle(x - size * 0.20f + i * size * 0.20f, y + size * 0.12f, size * 0.035f, Draw.fill)
    }
}
