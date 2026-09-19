package com.echoportal

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import com.echoportal.Draw.fmt
import com.echoportal.Draw.label
import com.echoportal.Draw.monoLabel
import org.json.JSONObject
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * Claude Code monitor. Page 0: a breathing Claude sunburst whose rhythm follows the state
 * (offline / idle / thinking / working / waiting / done), what Claude is doing right now, the
 * request it is working on, tool and time counters. Page 1: the recent event log.
 */
class ClaudeFace(ctx: Context) : Face(ctx) {
    override val id = "claude"
    override val name = "Claude"
    override val color = Color.parseColor("#D97757")
    override val channels = setOf("claude")
    override val pageCount = 2

    private var up = false
    private var status = "offline"
    private var tool = ""; private var detail = ""; private var prompt = ""; private var message = ""
    private var project = ""; private var model = ""
    private var turnElapsed = 0.0; private var toolsTurn = 0; private var toolsSession = 0; private var subagents = 0
    private var events = listOf<String>()
    private var lastUpdate = 0L
    private var shownAt = 0L
    private val glow = Paint(Paint.ANTI_ALIAS_FLAG)
    private val ray = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND }
    private val waitColor = Color.parseColor("#FFB74D")
    private val okColor = Color.parseColor("#81C784")

    override fun onShow() { shownAt = System.currentTimeMillis() }

    override fun onEvent(channel: String, data: JSONObject) {
        up = data.optBoolean("up", false)
        status = data.optString("status", "offline")
        tool = data.optString("tool", ""); detail = data.optString("detail", "")
        prompt = data.optString("prompt", ""); message = data.optString("message", "")
        project = data.optString("project", ""); model = data.optString("model", "")
        turnElapsed = data.optDouble("turn_elapsed", 0.0)
        toolsTurn = data.optInt("tools_turn", 0); toolsSession = data.optInt("tools_session", 0); subagents = data.optInt("subagents", 0)
        data.optJSONArray("events")?.let { a -> events = (0 until a.length()).map { a.optString(it) } }
        lastUpdate = System.currentTimeMillis()
        invalidate()
    }

    override fun preview(): String = when (status) {
        "offline" -> "Claude Code not running"
        "working" -> "running $tool"
        "thinking" -> "thinking…"
        "waiting" -> "needs you"
        "done" -> "finished · $toolsTurn tools"
        else -> "idle"
    }

    override fun onTap(x: Float, y: Float, cx: Float, cy: Float, r: Float, page: Int): Boolean {
        requestPage(if (page == 0) 1 else 0); return true
    }

    /** Seconds per breath for each state; 0 = still. */
    private fun period(): Float = when (status) { "working" -> 1.1f; "thinking" -> 2.2f; "waiting" -> 0.55f; "done" -> 4f; "idle" -> 5f; else -> 0f }

    private fun stateColor(): Int = when (status) { "waiting" -> waitColor; "done" -> okColor; "offline" -> Draw.mutedColor; else -> color }

    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, page: Int, ambient: Boolean) {
        if (page == 1) { drawLog(c, cx, cy, r, f); return }
        val now = System.currentTimeMillis()
        val p = period()
        val phase = if (p > 0f) ((now % (p * 1000).toLong()) / (p * 1000f)) else 0f
        val breath = if (p > 0f) (0.5f + 0.5f * sin(2 * PI * phase).toFloat()) else 0f     // 0..1
        val col = stateColor()

        // header
        val title = when (status) {
            "offline" -> "Claude Code isn't running"; "idle" -> "idle"; "thinking" -> "thinking…"
            "working" -> "running $tool"; "waiting" -> "needs you"; "done" -> "finished"; else -> status
        }
        Draw.header(c, cx, cy, f, "Claude", title, col)

        // breathing sunburst
        val icy = cy - 30f * f
        val base = 44f * f
        val scale = 1f + 0.07f * breath
        glow.color = col
        glow.alpha = (18 + 40 * breath).toInt(); c.drawCircle(cx, icy, base * (1.9f + 0.35f * breath), glow)
        glow.alpha = (30 + 60 * breath).toInt(); c.drawCircle(cx, icy, base * (1.35f + 0.2f * breath), glow)
        sunburst(c, cx, icy, base * scale, col, 255)

        // what it's doing
        val act = when (status) {
            "working" -> if (detail.isNotEmpty()) detail else tool
            "waiting" -> message.ifEmpty { "waiting for your answer" }
            "thinking" -> "deciding what to do next"
            "done" -> fmt("%d tools · %s", toolsTurn, dur(turnElapsed))
            "idle" -> "waiting for a request"
            else -> "hooks report here when it starts"
        }
        label(c, act.take(44), cx, cy + 46f * f, 14f * f, Draw.textColor, bold = status == "waiting")
        if (prompt.isNotEmpty() && status != "offline") {
            val lines = wrap(prompt, 40)
            label(c, "“" + lines[0] + (if (lines.size == 1) "”" else ""), cx, cy + 68f * f, 11.5f * f, Draw.mutedColor)
            if (lines.size > 1) label(c, lines[1].take(40) + (if (lines.size > 2) "…”" else "”"), cx, cy + 84f * f, 11.5f * f, Draw.mutedColor)
        }
        // counters
        if (status != "offline") {
            val elapsed = if (status == "working" || status == "thinking") dur(turnElapsed) else null
            val parts = mutableListOf<String>()
            if (elapsed != null) parts += elapsed
            parts += fmt("%d tools", if (status == "idle") toolsSession else toolsTurn)
            if (subagents > 0) parts += fmt("%d agents", subagents)
            if (project.isNotEmpty()) parts += project
            label(c, parts.joinToString("  ·  "), cx, cy + 112f * f, 11f * f, Draw.mutedColor)
            if (model.isNotEmpty()) label(c, model, cx, cy + 128f * f, 9f * f, Draw.gridColor)
        }
        if (p > 0f) invalidate()
    }

    private fun drawLog(c: Canvas, cx: Float, cy: Float, r: Float, f: Float) {
        Draw.header(c, cx, cy, f, "Claude", "recent activity", stateColor())
        val rows = events.takeLast(9)
        if (rows.isEmpty()) label(c, "nothing yet", cx, cy, 13f * f, Draw.mutedColor)
        var y = cy - 100f * f
        for (row in rows) {
            val you = row.contains("  you: ")
            monoLabel(c, row.take(48), cx - 150f * f, y, 10f * f, if (you) color else Draw.textColor)
            y += 22f * f
        }
    }

    /** Claude-style sunburst: 12 rounded rays, alternating long and short. */
    private fun sunburst(c: Canvas, x: Float, y: Float, rad: Float, col: Int, alpha: Int) {
        ray.color = col; ray.alpha = alpha
        ray.strokeWidth = rad * 0.19f
        for (i in 0 until 12) {
            val a = (i * 30f - 90f) * (PI / 180f).toFloat()
            val len = if (i % 2 == 0) rad else rad * 0.72f
            val r0 = rad * 0.28f
            c.drawLine(x + r0 * cos(a), y + r0 * sin(a), x + len * cos(a), y + len * sin(a), ray)
        }
    }

    private fun dur(s: Double): String = if (s < 60) fmt("%.0fs", s) else fmt("%dm %02ds", (s / 60).toInt(), (s % 60).toInt())

    private fun wrap(s: String, n: Int): List<String> {
        val out = mutableListOf<String>(); var cur = ""
        for (w in s.split(" ")) { if (cur.isNotEmpty() && cur.length + 1 + w.length > n) { out += cur; cur = w } else cur = if (cur.isEmpty()) w else "$cur $w" }
        if (cur.isNotEmpty()) out += cur
        return out
    }

    override fun drawIcon(c: Canvas, x: Float, y: Float, size: Float, selected: Boolean) {
        sunburst(c, x, y, size * 0.36f, if (selected) color else Draw.mutedColor, 255)
    }
}
