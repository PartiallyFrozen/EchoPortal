package com.echoportal

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import com.echoportal.Draw.fmt
import com.echoportal.Draw.label
import org.json.JSONObject
import java.util.Locale
import kotlin.math.abs

/**
 * Crypto + stock prices. One page per symbol: big price, change (green/red), sparkline,
 * and a compact list of the other symbols underneath. Symbols are configured on the PC (tray > Tickers...).
 */
class TickerFace(ctx: Context) : Face(ctx) {
    override val id = "ticker"
    override val name = "Markets"
    override val color = Color.parseColor("#FFD54F")
    override val channels = setOf("ticker")

    private data class Item(val sym: String, val name: String, val kind: String, val price: Double?, val changePct: Double?,
                            val currency: String, val spark: FloatArray, val market: String, val live: Boolean)

    private var items: List<Item> = emptyList()
    private var updated = 0L
    private var error: String? = null
    private var cycleSec = 0
    private var curPage = 0
    private var lastCycle = 0L
    private var userTouchedAt = 0L
    override val pageCount get() = items.size.coerceAtLeast(1)

    private val up = Color.parseColor("#66BB6A")
    private val down = Color.parseColor("#EF5350")

    override fun onEvent(channel: String, data: JSONObject) {
        val arr = data.optJSONArray("items") ?: return
        items = List(arr.length()) { i ->
            val o = arr.getJSONObject(i)
            val sp = o.optJSONArray("spark")
            Item(
                o.optString("sym"), o.optString("name"), o.optString("kind"),
                if (o.isNull("price")) null else o.optDouble("price"),
                if (o.isNull("change_pct")) null else o.optDouble("change_pct"),
                o.optString("currency", "USD"),
                if (sp == null) FloatArray(0) else FloatArray(sp.length()) { sp.optDouble(it).toFloat() },
                o.optString("market", ""),
                o.optBoolean("live", false),
            )
        }
        updated = data.optLong("updated", 0L)
        error = if (data.isNull("error")) null else data.optString("error")
        cycleSec = data.optInt("cycle", 0)
        invalidate()
    }

    override fun onPage(page: Int) {
        // a page change we did not ask for = the user swiped: hold the cycle for a while
        if (page != expectedPage) userTouchedAt = System.currentTimeMillis()
        curPage = page; expectedPage = -1
        lastCycle = System.currentTimeMillis()
    }
    private var expectedPage = -1
    override fun onShow() { lastCycle = System.currentTimeMillis(); expectedPage = 0; userTouchedAt = 0L }

    override fun preview(): String {
        val it = items.firstOrNull() ?: return "no tickers yet"
        return fmt("%s %s  %s", it.sym, money(it.price, it.currency, short = true), pct(it.changePct))
    }

    private fun money(v: Double?, cur: String, short: Boolean = false): String {
        if (v == null) return "--"
        val sym = when (cur) { "USD", "CAD", "AUD" -> "$"; "EUR" -> "€"; "GBP" -> "£"; "JPY" -> "¥"; else -> "" }
        return when {
            v >= 10000 -> sym + String.format(Locale.US, "%,.0f", v)
            v >= 1000 -> sym + String.format(Locale.US, if (short) "%,.0f" else "%,.2f", v)
            v >= 1 -> sym + String.format(Locale.US, "%.2f", v)
            else -> sym + String.format(Locale.US, "%.4f", v)
        }
    }

    private fun pct(p: Double?): String = if (p == null) "" else String.format(Locale.US, "%s%.2f%%", if (p >= 0) "+" else "−", abs(p))

    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, page: Int, ambient: Boolean) {
        if (items.isEmpty()) {
            Draw.header(c, cx, cy, f, "Markets", "waiting for prices", color)
            label(c, error ?: "set symbols in the tray app: Tickers…", cx, cy + 8f * f, 12f * f, Draw.mutedColor)
            return
        }
        val it = items[page.coerceIn(0, items.size - 1)]
        val col = when { it.changePct == null -> Draw.mutedColor; it.changePct >= 0 -> up; else -> down }
        Draw.header(c, cx, cy, f, it.sym, it.name.take(28) + if (it.market.isNotEmpty() && it.market != "24h") "  ·  market ${it.market}" else "", color)

        label(c, money(it.price, it.currency), cx, cy - 66f * f, 46f * f, Draw.textColor, bold = true)
        label(c, pct(it.changePct) + if (it.kind == "crypto") "  24h" else "  today", cx, cy - 40f * f, 18f * f, col, bold = true)

        // sparkline
        if (it.spark.size >= 2) {
            val l = cx - 170f * f; val rr = cx + 170f * f; val t = cy - 26f * f; val b = cy + 40f * f
            var lo = Float.MAX_VALUE; var hi = -Float.MAX_VALUE
            for (v in it.spark) { if (v < lo) lo = v; if (v > hi) hi = v }
            val span = (hi - lo).coerceAtLeast(hi * 0.001f + 1e-6f)
            Draw.line.color = Draw.gridColor; Draw.line.strokeWidth = 1f * f
            c.drawLine(l, b, rr, b, Draw.line)
            val path = android.graphics.Path()
            for ((i, v) in it.spark.withIndex()) {
                val x = l + (rr - l) * i / (it.spark.size - 1).toFloat()
                val y = b - (b - t) * ((v - lo) / span)
                if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
            }
            Draw.line.color = col; Draw.line.strokeWidth = 2.5f * f
            c.drawPath(path, Draw.line)
            label(c, money(hi.toDouble(), it.currency, short = true), rr, t - 4f * f, 9f * f, Draw.mutedColor, Paint.Align.RIGHT)
            label(c, money(lo.toDouble(), it.currency, short = true), rr, b + 11f * f, 9f * f, Draw.mutedColor, Paint.Align.RIGHT)
            label(c, if (it.kind == "crypto") "24 h" else "today", l, b + 11f * f, 9f * f, Draw.mutedColor, Paint.Align.LEFT)
        }

        // the others, compact
        var y = cy + 72f * f
        val others = items.filter { o -> o !== it }.take(4)
        for (o in others) {
            val oc = when { o.changePct == null -> Draw.mutedColor; o.changePct >= 0 -> up; else -> down }
            Draw.monoLabel(c, o.sym.padEnd(6), cx - 130f * f, y, 13f * f, Draw.textColor)
            Draw.monoLabel(c, money(o.price, o.currency, short = true), cx + 30f * f, y, 13f * f, Draw.textColor, Paint.Align.RIGHT)
            Draw.monoLabel(c, pct(o.changePct), cx + 130f * f, y, 13f * f, oc, Paint.Align.RIGHT)
            y += 18f * f
        }
        if (updated > 0) {
            val age = (System.currentTimeMillis() / 1000 - updated)
            val live = it.live
            label(c, (if (live) "\u25cf live  \u00b7  " else "") + (if (age < 90) "updated just now" else fmt("updated %d min ago", age / 60)) + (if (cycleSec > 0) "  \u00b7  cycling ${cycleSec}s" else ""),
                cx, cy + 150f * f, 10f * f, if (live) up else Draw.gridColor)
        }
    }

    override fun onTick() {
        val now = System.currentTimeMillis()
        if (cycleSec > 0 && items.size > 1 && now - lastCycle >= cycleSec * 1000L && now - userTouchedAt > 20_000L) {
            lastCycle = now
            expectedPage = (curPage + 1) % items.size
            requestPage(expectedPage)
        }
        invalidate()
    }

    override fun drawIcon(c: Canvas, x: Float, y: Float, size: Float, selected: Boolean) {
        val col = if (selected) color else Draw.mutedColor
        Draw.line.strokeWidth = size * 0.09f; Draw.line.color = col
        val pts = listOf(-0.4f to 0.25f, -0.15f to -0.05f, 0.05f to 0.15f, 0.4f to -0.35f)
        for (i in 0 until pts.size - 1) c.drawLine(x + pts[i].first * size, y + pts[i].second * size, x + pts[i + 1].first * size, y + pts[i + 1].second * size, Draw.line)
        c.drawLine(x + 0.2f * size, y - 0.35f * size, x + 0.4f * size, y - 0.35f * size, Draw.line)
        c.drawLine(x + 0.4f * size, y - 0.35f * size, x + 0.4f * size, y - 0.15f * size, Draw.line)
    }
}
