package com.echoportal

/** Fixed-length ring buffers of recent samples, one per metric, for the graphs. */
class History(private val size: Int = 60) {
    private val buf = HashMap<String, FloatArray>()
    private val head = HashMap<String, Int>()
    private val count = HashMap<String, Int>()

    fun push(key: String, v: Double) {
        val a = buf.getOrPut(key) { FloatArray(size) }
        val h = head[key] ?: 0
        a[h] = v.toFloat()
        head[key] = (h + 1) % size
        count[key] = ((count[key] ?: 0) + 1).coerceAtMost(size)
    }

    /** Oldest → newest, right-aligned into a size-length array with NaN for missing. */
    fun series(key: String): FloatArray {
        val out = FloatArray(size) { Float.NaN }
        val a = buf[key] ?: return out
        val n = count[key] ?: 0
        val h = head[key] ?: 0
        for (i in 0 until n) out[size - n + i] = a[(h - n + i + size) % size]
        return out
    }

    fun record(s: Stats) {
        push("cpu", s.cpu.total)
        push("mem", s.mem.pct)
        s.gpu?.let {
            push("gpu3d", it.util3d); push("gpucopy", it.utilCopy)
            push("gpuenc", it.utilEnc); push("gpudec", it.utilDec); push("gpucompute", it.utilCompute)
            push("gpuded", if (it.dedTotalGb > 0) it.dedUsedGb / it.dedTotalGb * 100 else 0.0)
            push("gpushared", if (it.sharedTotalGb > 0) it.sharedUsedGb / it.sharedTotalGb * 100 else 0.0)
            push("gputemp", (it.tempC ?: 0).toDouble())
            push("gpupower", if ((it.powerLimitW ?: 0) > 0) (it.powerW ?: 0.0) / it.powerLimitW!! * 100 else 0.0)
        }
        s.disks.forEach { push("disk${it.idx}", it.activePct ?: 0.0) }
        s.nets.firstOrNull()?.let { push("netdown", it.downMbps); push("netup", it.upMbps) }
    }
}
