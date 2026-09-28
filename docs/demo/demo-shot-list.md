# Demo video — shot list

Target length **4:40**. A 3-minute cut is marked at the end.

Record the screen with no audio, read `demo-narration.txt` separately, then lay the audio under the
video in your editor. The timings below are cumulative and match the narration's timecodes.

---

## Before you press record

| # | Check | Why |
|---|---|---|
| 1 | Restart the engine so the deployed build is live | The box was serving older code until the last deploy |
| 2 | Sign in once as each of the three accounts, then sign out | Proves the passwords work *before* you are live. Five wrong tries locks an account for 15 minutes. |
| 3 | Confirm the collection agent account exists and is linked to `C01` | `/agent` returns 403 without the roster link |
| 4 | Confirm **no run exists for the bank's current business date** | You need Today to offer a clean start |
| 5 | Browser at 100% zoom, 1920×1080, bookmarks bar hidden | Text stays legible after compression |
| 6 | Pick light or dark in the user menu and stay there | Switching mid-recording looks like a glitch |
| 7 | Have a second browser profile or private window ready | For the collector, so you don't sign out and back in on camera |
| 8 | Close every other tab | No stray notifications |

**Where to record it:** the EC2 box, `https://54.227.18.251.sslip.io` — it is deployed, clean, and the
URL in shot 1 is itself evidence. Run locally only if the network is unreliable.

**Do not plan to show Business KPIs.** Outcomes mature 30 days after an action and this instance has no
history, so the page will honestly say "waiting". Showing an empty chart costs you more than skipping it.

---

## The shots

### 0 · Title card — 0:00–0:08
Optional. Open `docs/architecture/solution-at-a-glance.html` full screen, or a plain title slide.
Hold still. Cut straight to the browser at 0:08.

### 1 · The worklist problem — 0:08–0:25
- Sign in as **manager**.
- Land on **Today**. Do not click anything yet — let the empty day sit there for two seconds.
- Hover the business date chip in the top bar so the viewer sees it is a real date, not a mock.

### 2 · Today's team — 0:25–0:48
- Click **Confirm today's team**.
- In the drawer, change **one** collector to part-time — drag or type, e.g. C03 down to 180 minutes.
- Click **Save today's team**.
- *Let the drawer close on its own.* Do not narrate over the animation.

### 3 · Start the run — 0:48–1:30
- Click **Start with this team**.
- Stay on the stage list while the eight agents run (~15–20s). Do not scroll.
- When it reaches **awaiting approval**, pause for a beat.
- Open **Runs & pipeline** in a new tab, scroll the trace so tool calls and timings are visible,
  then come back to Today. Keep this to 8 seconds — it is texture, not the point.

### 4 · The worklist is per person — 1:30–1:58
- Click the **Team queues** tab.
- Point at the collector you cut to part-time: their queue is visibly shorter.
- Click back to **Worklist**.

### 5 · One account — the heart of it — 1:58–3:00
Pick a row **before recording** that is not escalated and has at least one blocked action.

- Click the row to open the drawer.
- Scroll slowly through, pausing on each:
  1. **Recommended, rank N** — the action and its value
  2. **Why — every figure is sourced.** Hover one figure so the source tooltip appears. Hold it.
  3. **Outreach script** — what the collector will actually say
  4. **All options for this account** — show the `allowed` column and a **blocked** row with its reason
  5. **Why this account, and not another?** — click **Ask the Decision Agent** and wait for the re-solve
- Leave the drawer open on the counterfactual result.

### 6 · Prescriptive, not predictive — 3:00–3:25
- Close the drawer, click the **Sort vs Optimised** tab.
- Let the comparison render. Point at the gap between the optimised plan and the sorts.
- Do not read the dollar figures aloud — the narration refers to them generically so it matches
  whatever is on screen.

### 7 · The human gate — 3:25–3:45
- Back to **Worklist**. Click **Approve** on the open account.
- Click **Approve all non-escalated**. Note the toast: escalated items are left behind.
- Click **Release approved to bank**. Confirm.

### 8 · The collector's view — 3:45–4:20
- Switch to the second browser window, signed in as **priya**.
- She lands on **My queue** — say nothing for a second and let that land.
- Scroll her rows. Point out: **no dollar figures anywhere**.
- Click **Record outcome** on a released row. Choose **Promise to pay**, set a date, type a short
  comment, save.
- The row's status chip flips to **Worked**.

### 9 · Governance — 4:20–4:35
- Back to the manager window, **Runs & pipeline**.
- Show the **LLM calls** panel — open one and show the stored prompt and reply.
- Scroll to the **audit** section so the chain verification is visible.

### 10 · Close — 4:35–4:40
- Return to Today. Hold on the finished day. Fade out.

---

## The 3-minute cut

Drop shots **0**, **3's** Runs & pipeline detour, **6**, and **9**. Keep 1, 2, 3 (run only), 4, 5, 7, 8.
Shot 5 is the one never to cut — it is the only place the system proves it is prescriptive rather than
a sorted list with narration.

---

## If something fails live

- **Run fails at ingestion** — the bank service is down. `sudo systemctl restart lastmile-bank`, then retry.
- **"Today's actions were already released"** — a run exists for that date. Close the day, or pick up from
  the existing run and demo from shot 4.
- **`/agent` returns 403** — the collector account is not linked to a roster collector id. Fix it in
  Users & access before recording.
- **The counterfactual in shot 5 spins** — it re-solves the whole MIP. Give it ten seconds; if it does not
  return, skip it and move to shot 6. Do not narrate the wait.
