package com.echoportal

import android.content.Context
import android.graphics.Canvas
import android.graphics.Path
import com.echoportal.Draw.label

/** Placeholder for a face that is designed but not built yet; keeps the launcher's final layout. */
class StubFace(ctx: Context, override val id: String, override val name: String, override val color: Int,
               private val blurb: String, private val icon: (Canvas, Float, Float, Float, Int) -> Unit) : Face(ctx) {

    override fun preview() = "coming soon"

    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, page: Int, ambient: Boolean) {
        icon(c, cx, cy - 60f * f, 90f * f, color)
        label(c, name, cx, cy + 20f * f, 26f * f, Draw.textColor, bold = true)
        label(c, "coming soon", cx, cy + 44f * f, 13f * f, Draw.mutedColor)
        var y = cy + 78f * f
        for (line in blurb.split("\n")) { label(c, line, cx, y, 13f * f, Draw.mutedColor); y += 18f * f }
    }

    override fun drawIcon(c: Canvas, x: Float, y: Float, size: Float, selected: Boolean) =
        icon(c, x, y, size, if (selected) color else Draw.mutedColor)

    companion object {
        private val path = Path()

        fun mic(c: Canvas, x: Float, y: Float, s: Float, col: Int) {
            Draw.fill.color = col; Draw.fill.alpha = 255
            c.drawRoundRect(x - s * 0.16f, y - s * 0.42f, x + s * 0.16f, y + s * 0.08f, s * 0.16f, s * 0.16f, Draw.fill)
            Draw.ring.strokeWidth = s * 0.08f; Draw.ring.color = col
            c.drawArc(x - s * 0.32f, y - s * 0.3f, x + s * 0.32f, y + s * 0.26f, 0f, 180f, false, Draw.ring)
            Draw.line.strokeWidth = s * 0.08f; Draw.line.color = col
            c.drawLine(x, y + s * 0.26f, x, y + s * 0.42f, Draw.line)
            c.drawLine(x - s * 0.18f, y + s * 0.42f, x + s * 0.18f, y + s * 0.42f, Draw.line)
        }

        fun nodes(c: Canvas, x: Float, y: Float, s: Float, col: Int) {
            val pts = listOf(-0.34f to -0.22f, 0.0f to 0.0f, 0.34f to -0.26f, 0.32f to 0.3f, -0.3f to 0.28f)
            Draw.line.strokeWidth = s * 0.06f; Draw.line.color = col
            val edges = listOf(0 to 1, 1 to 2, 1 to 3, 1 to 4)
            for ((a, b) in edges) c.drawLine(x + pts[a].first * s, y + pts[a].second * s, x + pts[b].first * s, y + pts[b].second * s, Draw.line)
            Draw.fill.color = col; Draw.fill.alpha = 255
            for ((px, py) in pts) c.drawCircle(x + px * s, y + py * s, s * 0.09f, Draw.fill)
        }
    }
}
