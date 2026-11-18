# Oversyte labeling guide

We measure Oversyte's accuracy by comparing its alerts against human labels on real store
footage. **The score is only as good as the labels.** If two people label the same video
differently, the score moves because of us, not because of the AI. This guide keeps everyone
consistent.

> Rules marked **[owner decision]** are product choices. If a situation isn't covered, don't
> guess: mark it as **Other** with a note and raise it with the project owner. The answer then
> goes into this guide.

---

## 1. One-time setup

```bash
git clone <repo-url> oversyte && cd oversyte
python -m venv .venv
.venv\Scripts\activate                     # macOS/Linux: source .venv/bin/activate
pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision
pip install -r requirements.txt
```

1. Copy `config.example.yaml` to `config.yaml` and set your own `dashboard_password`.
  Leave the camera at `enabled: false`; labeling doesn't need a live camera.
2. Download the test videos from the team AWS bucket into `eval/videos/`. They're already
  named `video-01.mp4`, `video-02.mp4`, …, so everyone can say "video 7" and mean the same clip.
  **Don't rename them yourself.** Labels are matched to videos by file name.
  If you still have videos with the old long names, push your labels, pull, then run
  `python tools/rename_videos.py --apply`. It renames your videos, labels and AI cache to the
  team names in `eval/video_names.json`.
3. Start the dashboard with `python run.py`, open [http://localhost:8000](http://localhost:8000), and go to **Accuracy**.

Videos are **never** committed to git (they're large and show real customers). Only the
label files in `eval/labels/` are shared.

---



## 2. Labeling a video, step by step

1. **Angles first.** In *Camera angles & shelf zones*, click **Find angle changes automatically**.
  Most recorder clips switch between cameras or zoom levels. Check that each angle really is
   a new view; delete false ones (trash icon) and add missed ones (**New angle starts here**).
2. **Zones for every angle.** Click **Draw zones** on each angle (or **Same view as…** if it
  shows a view you've already drawn). See the zone rules in section 4.
3. **Watch the whole video** and mark every theft (section 3). Use 2× speed for quiet parts,
  then slow down, zoom in (mouse wheel) and step back with **Shift+←** to find the exact moment.
4. **Notes:** anything unusual, such as "back to camera", "blurry", "unsure about 1:12, see note".
5. Fill in **Labeled by**, then tick **Finished labeling this video**. Only finished videos
  are scored, and an unmarked moment in a finished video counts as "no theft".

Keyboard: `Space` play/pause · `←/→` 2 s · `Shift+←/→` small step · `1`–`6` mark a theft ·
`+/−` zoom · `0` reset zoom.

---



## 3. Marking thefts



### When to mark

Mark the frame where the **item disappears from view into the pocket, bag or clothing**.
Not when they pick it up, and not when the hand comes back out.


| Key | Type                      | Mark the moment…                                                                       |
| --- | ------------------------- | -------------------------------------------------------------------------------------- |
| `1` | Into pocket               | the item goes into a trouser/jacket pocket                                             |
| `2` | Into bag                  | the item goes into a backpack, purse or shopping bag they brought                      |
| `3` | Waistband / under clothes | the item goes down the waistband, up a sleeve or under a shirt/hoodie                  |
| `4` | Grab and run              | the person **crosses the exit door** with unpaid goods openly in hand (no concealment) |
| `5` | Other theft               | theft that fits nothing above, or you're unsure. **Always add a note.**                |
| `6` | Employee theft            | a staff member pockets cash or merchandise (register skimming, etc.)                   |




### Rules

- **One mark per concealment.** Several items in one motion = 1 mark. The same person
concealing again 3+ seconds later = a second mark.
- **Several people** concealing at once: one mark per person.
- **Concealed but later paid?** Still mark it, and add the note "paid". **[owner decision]**
Oversyte's job is to flag concealment; staff decide what happens next.
- **Can't see the moment** (blocked, out of frame) but it clearly happened: mark your best
estimate as **Other** with a note like "occluded, estimated".
- **Unsure if it's theft:** mark **Other** + note. Never skip it silently. A skipped theft
becomes a "false alarm" in the score if Oversyte catches it.



### NOT theft (don't mark)

- Picking an item up and putting it back
- A customer putting their **own** phone, wallet or keys in a pocket
- Carrying items openly to the register, or putting them in a store basket
- Staff restocking shelves or handling stock behind the counter
- Paying, bagging at the register (the store's bag)

---



## 4. Zones

Oversyte treats a hand inside a **shelf zone** as "reaching for a product". Zones that are
too big break detection, so be precise.

**Shelf zones (green): outline only where products sit**

- Shelf faces, cooler/fridge doors, counter displays, hot food cases, candy racks
- **One shape per shelving unit** is enough; you don't need one per shelf row
- Include **every** shelf customers can reach, not just the one stolen from. In a real store
nobody knows in advance where theft will happen.
- **Never include floor or walkways.** A person standing in a zone would look like they're
reaching the whole time.
- Skip shelves that are tiny or so far away you can't see hands.

**Ignore zones (grey): outline the floor where staff stand**

- Behind the counter / register, so cashiers aren't flagged
- Doesn't need to be exact; cover the area where staff feet are

**Camera angles:** every angle has its own zones. A wrong or missing angle puts zones in the
wrong place for that part of the video. Check them before you mark thefts.

---



## 5. Quality and duplicates

- **Label poor-quality videos too.** Real store footage is often bad, and we need to know how
Oversyte copes with it. Note the problem ("dark", "far away").
- **Duplicate clips** (the same incident in two files, e.g. `name.mp4` and `name(1).mp4`):
label one, write "duplicate of X" in the other's notes, leave it unfinished, and tell the
project owner so it can be removed.

---



## 6. Calibration round (before splitting the work)

Everyone labels the **same 3 videos** on their own, without talking about them. The videos
are listed in `eval/calibration/videos.txt`. Nobody has labeled them yet, so nobody sees
anyone else's marks first.

```bash
# 1. label the 3 calibration videos in the dashboard and tick "Finished"
# 2. move your labels for THOSE videos into your calibration folder and commit them
#    (it only ever touches the videos in videos.txt, never anyone else's labels)
python tools/compare_labels.py --save YOURNAME
git add eval/calibration/YOURNAME && git commit -m "Calibration labels: YOURNAME" && git push
# 3. once everyone has pushed, compare any two people
git pull
python tools/compare_labels.py alex sam
```

The comparison lists every theft only one person marked, every theft marked at a different
moment or with a different type, and how much your shelf zones overlap. **Talk through every
difference** and update this guide where a rule was unclear. Aim for **90%+ agreement**
before splitting up the remaining videos.

Afterwards the project owner picks the agreed version of each calibration video, copies it
from `eval/calibration/<name>/` back into `eval/labels/` (fixing anything discussed), and
pushes it, so those 3 videos count in the score too.

---



## 7. Sharing labels with git

- Each person labels **only their assigned videos** (see the table below), so nobody edits the
same label file and there are no merge conflicts.
- `git pull` before you start, and commit + push `eval/labels/` at the end of each session:
  ```bash
  git pull
  git add eval/labels && git commit -m "Labels: <videos>" && git push
  ```
- **Spot-checks:** each person reviews about 1 in 5 of someone else's finished videos. If you
disagree, don't silently change it; talk to the labeler and fix the guide if needed.


| Video          | Assigned to   | Status                  |
| -------------- | ------------- | ----------------------- |
| *6-10**11-15* | FaarizzJacob | In-ProgressIn-Progress |


---



## 8. Scoring

```bash
python eval.py --check   # once per new video: analyses it and rates quality (slow the first time)
python eval.py           # scores every finished video (seconds, thanks to the cache)
```

When you post a score for the team, always include the command you ran and the first lines
of the output (the model, `alert_threshold`, number of videos), so scores are comparable.
**Don't change detection settings in** `config.yaml` **to make a score look better.** Score
changes should come from code changes that everyone can review.