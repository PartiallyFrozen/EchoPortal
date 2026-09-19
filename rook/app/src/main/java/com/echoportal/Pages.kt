package com.echoportal

import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Typeface
import com.echoportal.Draw.fmt
import com.echoportal.Draw.label
import com.echoportal.Draw.monoLabel
import java.util.Locale
import kotlin.math.cos
import kotlin.math.sin

/**
 * A dashboard page. All pages draw into a circle of radius r centred at (cx, cy);
 * f = r / 240 scales the 480-px design to whatever size the view has.
 */
interface Page {
    val title: String
    fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, s: Stats?, h: History)
}

/** "NVIDIA RTX PRO 6000 Blackwell Workstation Edition" -> "RTX PRO 6000 Blackwell WS" */
fun shortGpuName(n: String?): String? = n
    ?.replace("NVIDIA ", "")?.replace("GeForce ", "")
    ?.replace("Workstation Edition", "WS")?.replace("Laptop GPU", "Laptop")
    ?.take(34)

// ------------------------------------------------------------------------ Overview
/** Everything at a glance: four gauges (CPU, RAM, GPU, VRAM), the disks, the top processes. */
class OverviewPage : Page {
    override val title = "Overview"

    private fun gauge(c: Canvas, x: Float, y: Float, rad: Float, f: Float, pct: Double?, color: Int, value: String, caption: String, alarm: Boolean = true) {
        Draw.ringArc(c, x, y, rad, 8f * f, pct ?: 0.0, color, alarmAbove90 = alarm)
        label(c, value, x, y + 7f * f, 22f * f, Draw.textColor, bold = true)
        label(c, caption, x, y + rad + 15f * f, 10f * f, Draw.mutedColor)
    }

    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, s: Stats?, h: History) {
        label(c, s?.host?.uppercase(Locale.US) ?: "PC", cx, cy - 152f * f, 13f * f, Draw.mutedColor)
        val rad = 40f * f
        val dx = 70f * f
        val cpu = s?.cpu; val mem = s?.mem; val g = s?.gpu
        // row 1: CPU, RAM
        gauge(c, cx - dx, cy - 82f * f, rad, f, cpu?.total, Draw.cpuColor,
            cpu?.let { fmt("%.0f%%", it.total) } ?: "--", cpu?.let { fmt("CPU  %.1f GHz", it.freqGhz) } ?: "CPU")
        gauge(c, cx + dx, cy - 82f * f, rad, f, mem?.pct, Draw.ramColor,
            mem?.let { fmt("%.0f%%", it.pct) } ?: "--", mem?.let { fmt("RAM  %.0f / %.0f GB", it.usedGb, it.totalGb) } ?: "RAM")
        // row 2: GPU, VRAM
        val vramPct = g?.let { if (it.dedTotalGb > 0) 100.0 * it.dedUsedGb / it.dedTotalGb else null }
        gauge(c, cx - dx, cy + 34f * f, rad, f, g?.util3d, Draw.gpuColor,
            g?.let { fmt("%.0f%%", it.util3d) } ?: "--", g?.let { fmt("GPU  %d\u00b0C  %.0f W", it.tempC ?: 0, it.powerW ?: 0.0) } ?: "GPU")
        gauge(c, cx + dx, cy + 34f * f, rad, f, vramPct, Draw.gpuColor,
            g?.let { fmt("%.0f", it.dedUsedGb) } ?: "--", g?.let { fmt("VRAM  %.0f / %.0f GB", it.dedUsedGb, it.dedTotalGb) } ?: "VRAM", alarm = false)

        // disks: capacity bars with live activity
        val disks = s?.disks?.take(3) ?: emptyList()
        if (disks.isNotEmpty()) {
            val w = 88f * f; val gap = 10f * f
            val total = disks.size * w + (disks.size - 1) * gap
            var x = cx - total / 2
            val y = cy + 112f * f
            for (d in disks) {
                val letter = d.letters.substringBefore(" ")
                label(c, letter, x, y - 4f * f, 11f * f, Draw.textColor, Paint.Align.LEFT, bold = true)
                label(c, fmt("%.0f%%", d.usedPct) + (d.activePct?.let { if (it >= 5) fmt("  \u00b7 %.0f%% busy", it) else "" } ?: ""), x + w, y - 4f * f, 10f * f, Draw.mutedColor, Paint.Align.RIGHT)
                Draw.bar(c, x, y, x + w, 6f * f, d.usedPct, Draw.diskColor)
                x += w + gap
            }
        }

        // top two processes
        val top = s?.top?.take(2) ?: emptyList()
        var y = cy + 140f * f
        for (p in top) {
            Draw.monoLabel(c, p.name.removeSuffix(".exe").take(18), cx - 100f * f, y, 12f * f, if (p.cpu > 25) Draw.hotColor else Draw.textColor)
            Draw.monoLabel(c, fmt("%4.1f%%", p.cpu), cx + 100f * f, y, 12f * f, Draw.mutedColor, Paint.Align.RIGHT)
            y += 16f * f
        }
        if (s != null) label(c, fmt("%d procs  \u00b7  up %s", s.procCount, Draw.uptime(s.uptimeS)), cx, cy + 172f * f, 10f * f, Draw.gridColor)
    }
}

// ------------------------------------------------------------------------ CPU
class CpuPage : Page {
    override val title = "CPU"
    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, s: Stats?, h: History) {
        val cpu = s?.cpu
        Draw.header(c, cx, cy, f, "CPU", cpu?.name?.replace("(R)", "")?.replace("(TM)", "")?.take(36), Draw.cpuColor)
        label(c, if (cpu != null) fmt("%.0f%%", cpu.total) else "--", cx - 60f * f, cy - 72f * f, 48f * f, bold = true)
        label(c, if (cpu != null) fmt("%.2f GHz", cpu.freqGhz) else "", cx + 70f * f, cy - 84f * f, 22f * f, Draw.cpuColor)
        label(c, if (cpu != null) "${cpu.cores} cores / ${cpu.threads} threads" else "", cx + 70f * f, cy - 66f * f, 12f * f, Draw.mutedColor)
        Draw.graph(c, cx - 190f * f, cy - 50f * f, cx + 190f * f, cy + 70f * f, h.series("cpu"), Draw.cpuColor)
        label(c, "60 seconds", cx - 190f * f, cy + 84f * f, 10f * f, Draw.mutedColor, Paint.Align.LEFT)
        label(c, "100%", cx + 190f * f, cy + 84f * f, 10f * f, Draw.mutedColor, Paint.Align.RIGHT)

        // per-core strip: one thin bar per thread
        cpu?.perCore?.let { cores ->
            val n = cores.size
            val l = cx - 150f * f; val w = 300f * f
            val bw = w / n
            for (i in 0 until n) {
                val v = cores[i]
                Draw.fill.color = when { v > 85 -> Draw.hotColor; v > 3 -> Draw.cpuColor; else -> Draw.dimColor }
                Draw.fill.alpha = 255
                val hh = (28f * f * v / 100.0).toFloat().coerceAtLeast(2f)
                c.drawRect(l + bw * i + 1f, cy + 128f * f - hh, l + bw * (i + 1) - 1f, cy + 128f * f, Draw.fill)
            }
        }
        if (s != null) label(c, fmt("%d processes  ·  up %s", s.procCount, Draw.uptime(s.uptimeS)), cx, cy + 150f * f, 12f * f, Draw.mutedColor)
    }
}

// ------------------------------------------------------------------------ Memory
class MemoryPage : Page {
    override val title = "Memory"
    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, s: Stats?, h: History) {
        val m = s?.mem
        Draw.header(c, cx, cy, f, "Memory", if (m != null) fmt("%.0f GB total", m.totalGb) else null, Draw.ramColor)
        label(c, if (m != null) fmt("%.1f GB", m.usedGb) else "--", cx - 50f * f, cy - 72f * f, 44f * f, bold = true)
        label(c, if (m != null) fmt("%.0f%%", m.pct) else "", cx + 90f * f, cy - 72f * f, 30f * f, Draw.ramColor)
        Draw.graph(c, cx - 190f * f, cy - 50f * f, cx + 190f * f, cy + 60f * f, h.series("mem"), Draw.ramColor)
        if (m != null) {
            val l = cx - 150f * f; val rr = cx + 150f * f
            var y = cy + 88f * f
            for ((k, v) in listOf(
                "In use" to fmt("%.1f GB", m.usedGb),
                "Available" to fmt("%.1f GB", m.availGb),
                "Committed" to fmt("%.0f / %.0f GB", m.committedGb, m.commitLimitGb),
            )) {
                label(c, k, l, y, 14f * f, Draw.mutedColor, Paint.Align.LEFT)
                label(c, v, rr, y, 14f * f, Draw.textColor, Paint.Align.RIGHT)
                y += 20f * f
            }
        }
    }
}

// ------------------------------------------------------------------------ GPU
class GpuPage : Page {
    override val title = "GPU"
    private data class Cell(val label: String, val key: String, val value: String?, val color: Int)
    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, s: Stats?, h: History) {
        val g = s?.gpu
        Draw.header(c, cx, cy, f, "GPU", shortGpuName(g?.name), Draw.gpuColor)
        // 2x2: engines on top (3D, Copy), memory below (dedicated, shared)
        val cells = listOf(
            Cell("3D", "gpu3d", g?.let { fmt("%.0f%%", it.util3d) }, Draw.gpuColor),
            Cell("Copy", "gpucopy", g?.let { fmt("%.0f%%", it.utilCopy) }, Draw.gpuColor),
            Cell("Dedicated", "gpuded", g?.let { fmt("%.1f/%.0f GB", it.dedUsedGb, it.dedTotalGb) }, Draw.gpuColor),
            Cell("Shared", "gpushared", g?.let { fmt("%.1f/%.0f GB", it.sharedUsedGb, it.sharedTotalGb) }, Draw.ramColor),
        )
        val gw = 170f * f; val gh = 66f * f; val gap = 12f * f
        for ((i, cell) in cells.withIndex()) {
            val col = i % 2; val row = i / 2
            val l = cx - gw - gap / 2 + col * (gw + gap)
            val t = cy - 112f * f + row * (gh + 26f * f)
            label(c, cell.label, l, t - 5f * f, 12f * f, Draw.mutedColor, Paint.Align.LEFT)
            label(c, cell.value ?: "--", l + gw, t - 5f * f, 12f * f, cell.color, Paint.Align.RIGHT)
            Draw.graph(c, l, t, l + gw, t + gh, h.series(cell.key), cell.color)
        }
        if (g != null) {
            val y1 = cy + 92f * f
            label(c, fmt("%d°C", g.tempC ?: 0), cx - 120f * f, y1, 26f * f, if ((g.tempC ?: 0) >= 80) Draw.hotColor else Draw.textColor, bold = true)
            label(c, "temp", cx - 120f * f, y1 + 16f * f, 11f * f, Draw.mutedColor)
            label(c, fmt("%.0f W", g.powerW ?: 0.0), cx - 40f * f, y1, 26f * f, bold = true)
            label(c, fmt("of %d W", g.powerLimitW ?: 0), cx - 40f * f, y1 + 16f * f, 11f * f, Draw.mutedColor)
            label(c, fmt("%d", g.clockSm ?: 0), cx + 45f * f, y1, 26f * f, bold = true)
            label(c, fmt("MHz / %d", g.clockSmMax ?: 0), cx + 45f * f, y1 + 16f * f, 11f * f, Draw.mutedColor)
            label(c, if (g.fanPct != null) fmt("%d%%", g.fanPct) else "--", cx + 125f * f, y1, 26f * f, bold = true)
            label(c, "fan", cx + 125f * f, y1 + 16f * f, 11f * f, Draw.mutedColor)
            // who holds the VRAM (top process) + compute engine
            val top = g.procs.firstOrNull()
            val vram = if (top != null) fmt("%s %.1f GB", top.name.removeSuffix(".exe").take(14), top.memMb / 1024.0) else ""
            label(c, listOf(vram, fmt("compute %.0f%%", g.utilCompute), g.pstate ?: "").filter { it.isNotEmpty() }.joinToString("  ·  "),
                cx, cy + 148f * f, 11f * f, Draw.mutedColor)
        }
    }
}

// ------------------------------------------------------------------------ Disks
class DisksPage : Page {
    override val title = "Disks"
    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, s: Stats?, h: History) {
        Draw.header(c, cx, cy, f, "Disks", null, Draw.diskColor)
        val disks = s?.disks ?: emptyList()
        val rowH = 92f * f
        var top = cy - 132f * f
        for (d in disks.take(3)) {
            // usable half-width shrinks toward the bottom of the circle
            val hw = (r * 0.72f).coerceAtMost(kotlin.math.sqrt((r * r - (top + 40f * f - cy) * (top + 40f * f - cy)).coerceAtLeast(0f)) - 8f * f)
            val l = cx - hw; val rr = cx + hw
            label(c, "Disk ${d.idx} (${d.letters})", l, top, 15f * f, Draw.textColor, Paint.Align.LEFT, bold = true)
            label(c, d.kind, l + 105f * f, top, 11f * f, Draw.mutedColor, Paint.Align.LEFT)
            label(c, if (d.activePct != null) fmt("%.0f%%", d.activePct) else "--", rr, top, 15f * f, Draw.diskColor, Paint.Align.RIGHT)
            val gr = l + (rr - l) * 0.58f
            Draw.graph(c, l, top + 7f * f, gr, top + 47f * f, h.series("disk${d.idx}"), Draw.diskColor)
            monoLabel(c, fmt("R %5.1f MB/s", d.readMbs), gr + 10f * f, top + 21f * f, 11f * f, Draw.mutedColor)
            monoLabel(c, fmt("W %5.1f MB/s", d.writeMbs), gr + 10f * f, top + 35f * f, 11f * f, Draw.mutedColor)
            Draw.bar(c, gr + 10f * f, top + 42f * f, rr, 5f * f, d.usedPct, Draw.diskColor)
            label(c, fmt("%.0f%% of %.0f GB  ·  %s", d.usedPct, d.totalGb, d.model.take(26)), l, top + 62f * f, 10f * f, Draw.mutedColor, Paint.Align.LEFT)
            top += rowH
        }
    }
}

// ------------------------------------------------------------------------ Network
class NetPage : Page {
    override val title = "Ethernet"
    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, s: Stats?, h: History) {
        val n = s?.nets?.firstOrNull()
        Draw.header(c, cx, cy, f, n?.name ?: "Network", if (n != null) "${n.ip}  ·  ${n.speedMbps} Mbps link" else null, Draw.netColor)
        label(c, "↓ " + (if (n != null) Draw.rate(n.downMbps) else "--"), cx - 90f * f, cy - 76f * f, 26f * f, Draw.netColor, bold = true)
        label(c, "↑ " + (if (n != null) Draw.rate(n.upMbps) else "--"), cx + 90f * f, cy - 76f * f, 26f * f, Draw.cpuColor, bold = true)
        Draw.graph(c, cx - 190f * f, cy - 56f * f, cx + 190f * f, cy + 30f * f, h.series("netdown"), Draw.netColor, max = 0f)
        Draw.graph(c, cx - 190f * f, cy + 40f * f, cx + 190f * f, cy + 110f * f, h.series("netup"), Draw.cpuColor, max = 0f)
        label(c, "receive", cx - 190f * f, cy - 60f * f, 10f * f, Draw.mutedColor, Paint.Align.LEFT)
        label(c, "send", cx - 190f * f, cy + 36f * f, 10f * f, Draw.mutedColor, Paint.Align.LEFT)
        val others = s?.nets?.drop(1)?.take(2) ?: emptyList()
        if (others.isNotEmpty()) {
            label(c, others.joinToString("   ") { "${it.name.take(14)} ↓${Draw.rate(it.downMbps)}" }, cx, cy + 140f * f, 11f * f, Draw.mutedColor)
        }
    }
}
