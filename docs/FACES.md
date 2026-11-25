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

## Sleep schedule

When the PC is not keeping the Spot awake: **weekdays off 10:30 pm–7:30 am**, **weekends off
11:30 pm–9:00 am**. With **Always on while PC is on** enabled, a connected PC overrides the off
schedule. Weekdays **7:30–9:15 am** are a hard awake window — the Spot comes up fully bright even
if the PC is offline or the screen would otherwise idle-dim. Once that window is past (**9:15 am**
weekdays, **9:00 am** weekends), an offline PC puts the Spot straight to sleep; a touch wake or a
wake-up-light ramp still holds it awake temporarily. An explicit **Sleep now** always wins.

## The faces

**Clock.** Five styles (EchoPortal, Classic, Bold, Pixel, Neon), remembered per device. Local weather
with a temperature arc and rain ticks around the rim, sunrise and sunset, CPU and GPU
complications on every style, and the next calendar event below them.

**Video Clock.** The same time and date over a muted loop streamed from the PC's media server
(`media/fireplace.mp4` by default). The clip is centre-cropped to fill the circle behind a soft
scrim and plays at 0.50× so the motion stays calm; the time and date are sized and lifted to sit in
the middle of the circle. If the PC is off or the file is missing it falls back to a dark clock instead of a black
screen, and it retries with backoff when the agent comes back. Long-press → **Video clip…** names a
different file in the agent's `media` folder, no rebuild needed.

**PC Mon.** Six pages: an at-a-glance overview with CPU, RAM, GPU and VRAM gauges plus disks and
top processes, then CPU, memory, GPU (3D, copy, dedicated and shared VRAM), disks and network,
drawn as Task-Manager-style history graphs.

**Voice.** Tap to talk, tap again to send. Audio is captured on the Spot, transcribed on the PC
(by Gemini, or by local Whisper in a source install), and typed into whatever window has focus,
with a toggle to target a specific window. With Gemini, the clip leaves the LAN for Google's API;
with local Whisper, nothing does.

**ComfyUI.** Node progress on the inner ring and whole-job progress on the outer one, the current
node, elapsed and ETA, queue depth and jobs today. Page two shows the finished image, or loops a
generated video, muted, with an unmute button.

**Timers.** The nearest countdown or wake alarm on a progress ring, with the rest of the list on
page two. The PC owns the clock, so timers keep running while the Spot sleeps or drops off Wi-Fi.
Tap a running timer to cancel it, or a finished one to dismiss it. Voice phrases such as *"set a
timer for 10 minutes"*, *"wake me at 6:30"* and *"cancel the timer"* are recognised on the PC and
confirmed on the voice face. A wake alarm sends `{"op":"wake","minutes":N}`; the shell wakes the
screen and eases the backlight up from near-black over that many minutes.

**Agenda.** The next week of calendar events, pushed from read-only ICS feeds. Page one is the next
few events, page two is a day-by-day view; tap either to ask the PC to refresh now. With no
`calendar_urls` configured the face says so and stays quiet. The clock's ambient mode also shows
the next event as a complication.

**Sky.** A painting of the day's weather, chosen from a library of 200 pre-rendered images (ten
conditions by five times of day, four variants each) that was generated once with a local image
model. It rotates variants every ten minutes and cross-fades between them, so it costs no GPU time
at runtime. Tap to re-paint the current one.

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
