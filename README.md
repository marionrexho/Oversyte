# Oversyte

**Stop theft the moment it happens.** Oversyte watches a store's existing security cameras,
spots when someone moves an item from a shelf into a pocket or bag, and pushes a phone alert
with a short video clip to the owner and staff on site. A person watches the clip and decides
what to do.

This repo is the working MVP: a detection engine, an alert server, and an installable phone
dashboard (PWA). It runs on an ordinary computer in the store, with no GPU and no new cameras.

```
 cameras (RTSP / NVR)            in-store PC                              phones
 ───────────────────▶  capture ─▶ pose + tracking ─▶ concealment logic ─▶ clip ─▶ web push ─▶ 📱
                       rolling      YOLO11n-pose       reach → carry →     H.264      (VAPID)    dashboard:
                       buffer       + bag/item model   pocket/bag          MP4                   review clip,
                                                                                                 Theft / False alarm
```



## Quick start (Windows, macOS or Linux, Python 3.10+)

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision
pip install -r requirements.txt
python run.py                     # first run creates config.yaml, then downloads the AI models (~12 MB)
```
Open [http://localhost:8000](http://localhost:8000) and sign in with the [dashboard_password](config.yaml#6) from [config.yaml](config.yaml)
(default [change-me](config.yaml#6), so change it).

The example config uses webcam `0`, so you can try it right away: stand in front of the
laptop camera, reach out to the side, then put your hand in your front pocket for half a
second. Use **Cameras → Send a test alert** to see the full alert flow without acting
anything out.

## Connecting store cameras

Add each camera to `config.yaml`:

```yaml
cameras:
  - id: aisle1
    name: "Aisle 1 (snacks)"
    source: "rtsp://USER:PASS@192.168.1.64:554/Streaming/Channels/102"
  - id: register
    name: "Register"
    source: "rtsp://USER:PASS@192.168.1.65:554/cam/realmonitor?channel=1&subtype=1"
```

- Nearly every IP camera and NVR/DVR exposes **RTSP**. Common URL formats:
Hikvision `/Streaming/Channels/102`, Dahua/Amcrest `/cam/realmonitor?channel=1&subtype=1`,
Reolink `/h264Preview_01_sub`, Uniview `/unicast/c1/s1/live`.
- Use the camera's **sub-stream** (lower resolution). It's plenty for detection and much
lighter on CPU.
- A video file also works as a source (`loop: true` replays it), which is handy for demos and testing.

Then open **Cameras → (camera) → Edit zones** and draw:

- **Shelf zones** over each shelf face the camera sees. This is the biggest accuracy
improvement: a "reach" only counts when the hand enters a shelf zone.
- **Ignore zones** over areas like behind the counter, so staff aren't flagged.

The live view colours each person by state: grey idle, yellow reaching, orange holding an item, red concealing.

## Phone alerts

Alerts are standard Web Push notifications from the dashboard, so there's no app store and no fees.

1. Phones need to reach the dashboard over **HTTPS** (browsers only allow push on secure sites).
  The easiest option is a free [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/):
   `cloudflared tunnel --url http://localhost:8000` gives a public `https://…` address
   (use a named tunnel for a permanent one). Tailscale Funnel works too.
   Set `public_url` in `config.yaml` to that address.
2. On each owner/staff phone, open that address and sign in.
  - **iPhone (iOS 16.4+):** Share → **Add to Home Screen**, open Oversyte from the home screen,
   then Settings → **Turn on theft alerts**.
  - **Android:** Settings → **Turn on theft alerts** (optionally "Install app" from the browser menu).
3. Press **Send a test alert**. Tapping the notification opens the clip.



## How detection works

`oversyte/concealment.py` runs a per-person, per-hand state machine on pose keypoints:


| Step    | Signal                                                                                                                                        |
| ------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Reach   | wrist inside a shelf zone for ≥ `reach_min_seconds` (no zones drawn: arm extended away from the body)                                         |
| Carry   | wrist leaves the shelf                                                                                                                        |
| Conceal | within `conceal_window_seconds`, wrist goes to a **pocket** (hip level), **waistband**, or **into a bag** and stays ≥ `conceal_dwell_seconds` |
| Score   | bag 0.75 · pocket 0.60 · waistband 0.45, +0.10 if a product was seen in the hand, +0.15 more if it's gone afterwards                          |


An alert fires when the score reaches `alert_threshold` (default 0.6). Each person can trigger
at most one alert per `person_cooldown_seconds`. Normal shopping (picking up an item, reading
it at chest height, carrying it with the arm hanging) does not alert. See `tests/test_concealment.py`.

**Tuning:** too many false alarms → draw tighter shelf zones, add ignore zones, or raise
`alert_threshold` to 0.7. Missed events → lower it to 0.5 (waistband concealment then alerts
without needing to see the item).

**Performance:** on a laptop CPU, one camera takes about 120–250 ms per analysed frame at
`imgsz: 640` (3–6 fps per camera). Bag/item detection only runs while someone is mid-reach.
For a 4–8 camera store, use a mini-PC with a recent Intel/AMD CPU, or an NVIDIA GPU
(`device: 0`), or lower `analysis_fps` / `imgsz: 480`.

## Measuring accuracy (Accuracy lab)

1. **Add test videos:** dashboard → Settings → **Accuracy lab** → *Upload videos*, or copy files into
  `eval/videos/`. Recorder exports in H.265 are converted for the browser automatically.
2. **Label each one:** mark the moment every item disappears (keys `1`–`5`, Space plays and pauses,
  ←/→ jump 2 s, Shift+←/→ step a little), draw the shelf zones, then tick **Finished labeling**.
   Videos with no theft are just as important, because they measure false alarms.
3. **Score:** `python eval.py`. It prints the catch rate, false alarms per hour, a table of what each
  `alert_threshold` would give, and every miss and false alarm. Open a video in the lab to see the
   results on its timeline.

The first run analyses each video at roughly real time. After that the AI output is cached in  
`eval/cache/`, so trying new thresholds, zones or rule changes re-scores in seconds.

## Project layout

```
run.py                  start everything
config.example.yaml     copied to config.yaml on first run
oversyte/
  camera.py             capture thread per camera, reconnects, rolling JPEG buffer
  detector.py           YOLO11n-pose + ByteTrack, bag/item detection
  concealment.py        the theft-pattern state machine (pure logic, unit tested)
  engine.py             ties it together: incidents, clips, alerts, zones, retention
  clips.py              H.264 MP4 + thumbnail with the suspect boxed
  push.py               Web Push (VAPID keys auto-generated in data/)
  storage.py            SQLite: incidents, push subscriptions
  server.py             FastAPI: API, media, live MJPEG view
web/                    dashboard PWA (vanilla JS, no build step) + service worker
tests/                  pytest scenarios for the detection logic
data/                   created at runtime: database, clips, keys (don't commit)
```

Run tests with `pytest`.

## Privacy & data

Video never leaves the store PC except clips opened by signed-in staff. Clips are deleted
after `retention_days` (30) unless marked **Theft**, which are kept as evidence. Check local
rules on video surveillance signage and retention for your state.