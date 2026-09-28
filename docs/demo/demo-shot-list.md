# Demo video — shot list

Target length **5:30**. The narration in `demo-narration.txt` is 831 spoken words, which is
5 minutes 32 seconds at a normal 150 words a minute — so the two line up with a little slack.

Record the screen with no audio, read the narration separately, then lay the audio under the video.
Timings below are cumulative and match the narration's timecodes.

---

## Before you press record

| # | Check | Why |
|---|---|---|
| 1 | Restart the engine so the deployed build is live | The box served older code until the last deploy |
| 2 | Sign in once as each of the three accounts, then sign out | Prove the passwords work *before* you are live. Five wrong tries locks an account for 15 minutes. |
| 3 | Confirm the collection agent account exists and is linked to `C01` | `/agent` returns 403 without the roster link |
| 4 | Confirm **no run exists for the bank's current business date** | You need Today to offer a clean start |
| 5 | Pick the account you will open in shot 5 | See the note under that shot. Do not go hunting on camera. |
| 6 | Browser at 100% zoom, 1920×1080, bookmarks bar hidden | Text stays legible after compression |
| 7 | Pick light or dark in the user menu and stay there | Switching mid-recording looks like a glitch |
| 8 | Second browser profile or private window, signed in as the collector | So you don't sign out and back in on camera |
| 9 | Close every other tab and mute notifications | |

**Where to record:** the EC2 box, `https://54.227.18.251.sslip.io` — it is deployed and clean, and the
URL is itself part of the evidence. Record locally only if the network is unreliable.

**Do not plan to show Business KPIs.** Outcomes mature 30 days after an action and this instance has no
history, so the page will honestly say "waiting". An empty chart costs more than skipping it.

---

## The shots

### 0 · Title card — 0:00 → 0:08
Optional. `docs/architecture/solution-at-a-glance.html` full screen, or a plain title slide.
Hold still, then cut to the browser.

### 1 · The problem — 0:08 → 0:28
- Sign in as **manager**, land on **Today**.
- Click nothing for two seconds. Let the empty day sit.
- Hover the business-date chip so the viewer sees a real date.

### 2 · Today's team — 0:28 → 0:50
- **Confirm today's team**.
- Change **one** collector to part time — e.g. C03 down to 180 minutes.
- **Save today's team**, and let the drawer close on its own.

### 3 · Start the run — 0:50 → 1:45
- **Start with this team**.
- Stay on the stage list for the whole run (~15–20s). Do not scroll, do not click.
- When it reaches **awaiting approval**, hold for a beat.
- Open **Runs & pipeline** in the second tab, scroll the trace so tool calls and timings show,
  then return to Today. **Ten seconds maximum** — it is texture, not the point.

### 4 · One queue per person — 1:45 → 2:07
- **Team queues** tab.
- Point at the collector you cut to part time: a visibly shorter queue.
- Back to **Worklist**.

### 5 · One account — 2:07 → 3:17
**Choose this row before recording.** You want one that is *not* escalated and *does* have at least one
blocked action, so the options table has something to show. Filter by segment = Persuadable to find one.

- Open the drawer. Scroll slowly, resting on each:
  1. **Recommended, rank N** — the action and its value
  2. **Why — every figure is sourced.** Hover one figure until the source tooltip appears. Hold it.
  3. **Outreach script**
  4. **All options for this account** — show the `allowed` column and a **blocked** row with its reason
  5. **Why this account, and not another?** → click **Ask the Decision Agent**, wait for the re-solve
- Leave the drawer on the counterfactual result.

### 6 · Prescriptive, not predictive — 3:17 → 3:42
- Close the drawer, open the **Sort vs Optimised** tab.
- Let it render. Point at the gap between optimised and the two sorts.
- Do not read the figures aloud — the narration stays generic so it matches whatever the run produced.

### 7 · The human gate — 3:42 → 4:07
- **Worklist** → **Approve** the account you just opened.
- **Approve all non-escalated** — note the toast leaving escalated items behind.
- **Release approved to bank**, confirm.

### 8 · The collector's view — 4:07 → 4:55
- Switch to the collector window. She lands on **My queue** — say nothing for a second.
- Scroll her rows. The point to let land: **no money anywhere on this page**.
- **Record outcome** on a released row → **Promise to pay**, set a date, short comment, save.
- The status chip flips to **Worked**.

### 9 · Governance — 4:55 → 5:20
- Manager window → **Runs & pipeline**.
- **LLM calls** panel: open one, show the stored prompt and reply.
- Scroll to the audit section so the chain verification shows.

### 10 · Close — 5:20 → 5:30
- Back to Today. Hold on the finished day. Fade.

---

## The 3-minute cut

Drop shots **0**, the Runs & pipeline detour inside **3**, **6**, and **9**. Keep 1, 2, 3, 4, 5, 7, 8.
The narration file marks the matching paragraphs to drop.

**Never cut shot 5.** It is the only place the system proves it is prescriptive rather than a sorted
list with narration on top.

---

## If something fails live

- **Run fails at ingestion** — the bank service is down. `sudo systemctl restart lastmile-bank`, retry.
- **"Today's actions were already released"** — a run exists for that date. Close the day, or pick up the
  existing run and start from shot 4.
- **`/agent` returns 403** — the collector account has no roster collector id. Fix in Users & access.
- **The counterfactual spins** — it re-solves the whole MIP. Give it ten seconds; if nothing returns, move
  to shot 6 and do not narrate the wait.
- **A login fails twice** — stop. Three more attempts locks the account for fifteen minutes. Check the
  password before trying again.
