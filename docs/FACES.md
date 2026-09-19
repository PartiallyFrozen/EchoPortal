# Faces

A face is one full-screen app inside the shell. The ring launcher holds them; swiping left and
right moves between a face's pages.

## Gestures

| Gesture | What it does |
| --- | --- |
| Swipe down, upper half | Ring launcher. Drag to spin it; it opens what it lands on after a moment |
| Swipe up, lower half | Quick sheet: brightness, volume, dim now, auto-dim, sleep schedule, menu |
| Swipe left or right | Next or previous page of the current face |
| Tap | Handled by the face (skip, mute, flip a page) |
| Long-press | Menu: sleep now, sleep schedule, background, settings, calibration |

Vertical swipes work anywhere in the half, not just at the edge, because Android's own gesture
areas eat the edges.

## The faces

**Clock.** Five styles (EchoPortal, Classic, Bold, Pixel, Neon), remembered per device. Local weather
with a temperature arc and rain ticks around the rim, sunrise and sunset, and CPU and GPU
complications on every style.

**PC Mon.** Six pages: an at-a-glance overview with CPU, RAM, GPU and VRAM gauges plus disks and
top processes, then CPU, memory, GPU (3D, copy, dedicated and shared VRAM), disks and network,
drawn as Task-Manager-style history graphs.

**Voice.** Tap to talk, tap again to send. Audio is captured on the Spot, transcribed by Whisper on
the PC's GPU, and typed into whatever window has focus, with a toggle to target a specific window.
Nothing leaves the LAN.

**ComfyUI.** Node progress on the inner ring and whole-job progress on the outer one, the current
node, elapsed and ETA, queue depth and jobs today. Page two shows the finished image, or loops a
generated video, muted, with an unmute button.

**Markets.** Streaming crypto prices from Coinbase and polled stock quotes, with a sparkline, 24
hour change and optional currency conversion. Pages auto-cycle at a configurable interval; symbols
are set from the tray.

**Sky.** A painting of the day's weather, chosen from a library of 200 pre-rendered images (ten
conditions by five times of day, four variants each) that was generated once with a local image
model. It rotates variants every ten minutes and cross-fades between them, so it costs no GPU time
at runtime. Tap to re-paint the current one.

**Claude.** What Claude Code is doing on the PC: a sunburst whose pulse follows the state, the
current tool and what it is acting on, the request, elapsed time and tool counts. Amber with a
chime when it needs an answer, green when it finishes. Page two is a recent activity log.

## Writing a face

Subclass `Face`, declare the channels you want, draw onto the canvas you are handed, and register
it in `MainActivity`. The shell gives you the centre, the radius and a scale factor, and calls
`onEvent` when data arrives on your channels.

```kotlin
class MyFace(ctx: Context) : Face(ctx) {
    override val id = "mine"
    override val name = "My face"
    override val color = Color.parseColor("#80CBC4")
    override val channels = setOf("stats")

    override fun onEvent(channel: String, data: JSONObject) { /* keep it cheap */ invalidate() }

    override fun draw(c: Canvas, cx: Float, cy: Float, r: Float, f: Float, page: Int, ambient: Boolean) {
        Draw.header(c, cx, cy, f, name, "hello", color)
    }

    override fun drawIcon(c: Canvas, x: Float, y: Float, size: Float, selected: Boolean) { /* ring icon */ }
}
```

Multiply every dimension by `f` so it scales, keep the corners empty because the screen is round,
and remember that one slow 2017 core is doing the work. `Draw` has the shared palette, gauges, bars
and history graphs. Faces can also ask for a page change, a looping video under the shell, or send
a message back to the agent.
