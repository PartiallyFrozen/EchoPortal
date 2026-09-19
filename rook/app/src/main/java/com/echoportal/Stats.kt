package com.echoportal

import org.json.JSONArray
import org.json.JSONObject

/** One snapshot from the PC agent (see pc-agent/agent.py for the JSON shape). */
data class ProcInfo(val pid: Int, val name: String, val cpu: Double, val memMb: Int)

data class CpuInfo(
    val name: String, val total: Double, val perCore: DoubleArray,
    val freqGhz: Double, val baseGhz: Double, val cores: Int, val threads: Int,
)

data class MemInfo(
    val usedGb: Double, val totalGb: Double, val pct: Double,
    val availGb: Double, val committedGb: Double, val commitLimitGb: Double,
)

data class GpuInfo(
    val name: String,
    val util3d: Double, val utilCopy: Double, val utilEnc: Double, val utilDec: Double, val utilCompute: Double,
    val utilNvml: Int?, val dedUsedGb: Double, val dedTotalGb: Double, val sharedUsedGb: Double, val sharedTotalGb: Double,
    val tempC: Int?, val powerW: Double?, val powerLimitW: Int?, val clockSm: Int?, val clockSmMax: Int?, val clockMem: Int?,
    val fanPct: Int?, val pstate: String?, val procs: List<ProcInfo>,
)

data class DiskInfo(
    val idx: Int, val letters: String, val model: String, val kind: String, val totalGb: Double,
    val activePct: Double?, val readMbs: Double, val writeMbs: Double, val usedPct: Double,
)

data class NetInfo(val name: String, val ip: String, val speedMbps: Int, val downMbps: Double, val upMbps: Double)

data class Stats(
    val host: String,
    val uptimeS: Long,
    val procCount: Int,
    val cpu: CpuInfo,
    val mem: MemInfo,
    val gpu: GpuInfo?,
    val disks: List<DiskInfo>,
    val nets: List<NetInfo>,
    val top: List<ProcInfo>,
) {
    companion object {
        private fun JSONObject.dbl(k: String, d: Double = 0.0) = if (isNull(k)) d else optDouble(k, d)
        private fun JSONObject.intOrNull(k: String): Int? = if (isNull(k)) null else optInt(k)
        private fun JSONObject.dblOrNull(k: String): Double? = if (isNull(k)) null else optDouble(k)
        private fun JSONObject.strOrNull(k: String): String? = if (isNull(k)) null else optString(k)

        private fun procs(a: JSONArray?): List<ProcInfo> = if (a == null) emptyList() else List(a.length()) {
            val p = a.getJSONObject(it)
            ProcInfo(p.optInt("pid"), p.optString("name", "?"), p.dbl("cpu"), p.optInt("mem_mb"))
        }

        fun parse(json: String): Stats {
            val o = JSONObject(json)
            val c = o.getJSONObject("cpu")
            val cores = c.getJSONArray("per_core")
            val m = o.getJSONObject("mem")
            val g = o.optJSONObject("gpu")
            val gpu = g?.let {
                GpuInfo(
                    name = it.optString("name", "GPU"),
                    util3d = it.dbl("util_3d"), utilCopy = it.dbl("util_copy"),
                    utilEnc = it.dbl("util_enc"), utilDec = it.dbl("util_dec"), utilCompute = it.dbl("util_compute"),
                    utilNvml = it.intOrNull("util_nvml"),
                    dedUsedGb = it.dbl("ded_used_gb"), dedTotalGb = it.dbl("ded_total_gb"),
                    sharedUsedGb = it.dbl("shared_used_gb"), sharedTotalGb = it.dbl("shared_total_gb"),
                    tempC = it.intOrNull("temp_c"), powerW = it.dblOrNull("power_w"), powerLimitW = it.intOrNull("power_limit_w"),
                    clockSm = it.intOrNull("clock_sm"), clockSmMax = it.intOrNull("clock_sm_max"), clockMem = it.intOrNull("clock_mem"),
                    fanPct = it.intOrNull("fan_pct"), pstate = it.strOrNull("pstate"),
                    procs = procs(it.optJSONArray("procs")),
                )
            }
            val da = o.optJSONArray("disks")
            val disks = if (da == null) emptyList() else List(da.length()) {
                val d = da.getJSONObject(it)
                DiskInfo(
                    d.optInt("idx"), d.optString("letters"), d.optString("model"), d.optString("kind"),
                    d.dbl("total_gb"), d.dblOrNull("active_pct"), d.dbl("read_mbs"), d.dbl("write_mbs"), d.dbl("used_pct"),
                )
            }
            val na = o.optJSONArray("nets")
            val nets = if (na == null) emptyList() else List(na.length()) {
                val n = na.getJSONObject(it)
                NetInfo(n.optString("name"), n.optString("ip"), n.optInt("speed_mbps"), n.dbl("down_mbps"), n.dbl("up_mbps"))
            }
            return Stats(
                host = o.optString("host", "PC"),
                uptimeS = o.optLong("uptime_s"),
                procCount = o.optInt("proc_count"),
                cpu = CpuInfo(
                    c.optString("name", "CPU"), c.dbl("total"), DoubleArray(cores.length()) { cores.getDouble(it) },
                    c.dbl("freq_ghz"), c.dbl("base_ghz"), c.optInt("cores"), c.optInt("threads"),
                ),
                mem = MemInfo(m.dbl("used_gb"), m.dbl("total_gb"), m.dbl("pct"), m.dbl("avail_gb"), m.dbl("committed_gb"), m.dbl("commit_limit_gb")),
                gpu = gpu,
                disks = disks,
                nets = nets,
                top = procs(o.optJSONArray("top")),
            )
        }
    }
}
