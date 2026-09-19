package com.echoportal

import android.content.Context
import android.graphics.Canvas
import org.json.JSONObject

/**
 * A face is one full-screen "app" inside the shell. It draws itself onto the round canvas,
 * may have several horizontally-swiped pages, and receives events from the PC hub.
 *
 * Geometry passed to draw(): centre (cx, cy), radius r, and f = r / 240 (design scale).
 */
abstract class Face(val ctx: Context) {
    abstract val id: String
    abstract val name: String
    abstract val color: Int

    /** Number of horizontally swipeable pages. */
    open val pageCount: Int = 1

    /** Set by the shell; call to request a redraw. */
    var invalidate: () -> Unit = {}

    /** Set by the shell; call to send a message to the PC hub. */
    var send: (JSONObject) -> Unit = {}

    open fun onShow() {}
    open fun onHide() {}

    /** The visible page changed (also called on show with the current page). */
    open fun onPage(page: Int) {}

    /** Set by the shell: ask for a looping video under the circle (null = none) / toggle pause. */
    var requestVideo: (String?) -> Unit = {}
    var toggleVideo: () -> Unit = {}
    var muteVideo: (Boolean) -> Unit = {}

    /** Set by the shell: animate to another page of this face (ignored unless this face is showing). */
    var requestPage: (Int) -> Unit = {}


    /** Called once a second while visible (and while ambient, if this is the ambient face). */
    open fun onTick() {}

    /** Message from the PC hub on a channel this face is interested in. */
    open fun onEvent(channel: String, data: JSONObject) {}
    open val channels: Set<String> = emptySet()

    abstract fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, page: Int, ambient: Boolean)

    /** Small icon for the ring launcher, centred at (x, y) within a box of the given size. */
    abstract fun drawIcon(c: Canvas, x: Float, y: Float, size: Float, selected: Boolean)

    /** How much of the global background to show on a page: 0 = none (face owns the circle),
     *  1 = light content (soft blur), 2 = dense content (heavy blur + dark scrim). */
    open fun bgLevel(page: Int): Int = 2

    /** One-line live summary shown in the launcher when this face is selected. */
    open fun preview(): String = ""

    /** Return true if the tap was handled (otherwise the shell ignores it). */
    open fun onTap(x: Float, y: Float, cx: Float, cy: Float, r: Float, page: Int): Boolean = false

    /** Physical key while this face is showing. Return true to consume. */
    open fun onKey(keyCode: Int): Boolean = false
}
