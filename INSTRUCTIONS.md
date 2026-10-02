# Lecture Animator: how to install and use it

Lecture Animator turns a recording of you giving a lecture into an animated lecture
video. You upload your audio; it plans the scenes from what you said, animates each one
so the visuals appear as you speak, lets you request changes in plain language, and
gives you one MP4.

It runs on your own computer. Your recordings and videos stay there; the only things
sent out are to the AI services below, using your own accounts.

---

## 1. What you need

- **A computer**: Mac (Apple silicon or Intel), Windows 10/11, or Linux, with at least
  8 GB of memory and about 6 GB of free disk space.
- **Docker Desktop** (free): runs the app in a self-contained box, so you don't install
  anything else.
- **API keys** (pay-as-you-go; you add credit to each account first). You need one key
  that can **animate** and one that can **transcribe**:

  | Key | Animates (plans + writes the animations) | Transcribes your recording |
  | --- | --- | --- |
  | Anthropic (Claude) | Yes (recommended) | — |
  | ElevenLabs | — | Yes (most precise timing) |
  | OpenAI | Yes (GPT models) | Yes (Whisper) |

  The simplest setup is **one OpenAI key**, which does both. The recommended setup is
  **Anthropic + ElevenLabs**. If you enter several, Claude animates and ElevenLabs
  transcribes.

**What it costs to use (approximate):** planning a lecture costs cents. Animating is
the main cost. A real 40-minute lecture cost about **$11** on the default model; with
several rounds of changes, expect roughly **$10–30 per 40 minutes**. The app shows what
you've spent in the Animate step. Choosing a cheaper model (Settings → Advanced)
lowers costs, but the animations are usually less polished.

## 2. Get your API keys (about 10 minutes, once)

Create only the ones you'll use (see the table above).

**Anthropic**
1. Go to <https://console.anthropic.com> and sign up.
2. Under **Billing**, add credit (e.g. $20).
3. Under **API keys**, click **Create key**, then copy it (it starts with `sk-ant-`).

**ElevenLabs**
1. Go to <https://elevenlabs.io> and sign up.
2. Open **Developers → API keys** (or your profile → API keys), click
   **Create API key**, and make sure speech-to-text is allowed. Copy it.

**OpenAI**
1. Go to <https://platform.openai.com> and sign up.
2. Under **Billing**, add credit.
3. Under **API keys**, click **Create new secret key** and copy it (it starts with `sk-`).

Keep both keys private, like passwords.

## 3. Install and start the app (about 15 minutes, once)

1. **Install Docker Desktop** from <https://www.docker.com/products/docker-desktop/>.
   Open it and wait until it says it's running. On Windows, accept its offer to set up
   WSL 2 if asked, then restart.
2. **Open a terminal.** On a Mac, open **Terminal** (Applications → Utilities). On
   Windows, open **PowerShell** (Start menu → type "PowerShell").
3. **Paste this command and press Enter:**

   ```
   docker run -d --name lecture-animator --restart unless-stopped -p 127.0.0.1:8000:8000 -v lecture-animator-data:/data ghcr.io/ahuang915/lecture-animator:latest
   ```

   The first time, it downloads the app (a few GB), which can take several minutes.
4. **Open <http://localhost:8000>** in your web browser.
5. A **Settings** window opens. Paste your keys, click **Test keys** (each should say
   "Key works"), then click **Save**.

### Every time after that

Open Docker Desktop. The app starts automatically with it. If it doesn't, find
**lecture-animator** under **Containers** and press ▶ (Start). Then go to
<http://localhost:8000>.

To stop it: press ■ (Stop) in Docker Desktop, or run `docker stop lecture-animator`.

## 4. Make your first video

### Before you record
- Record somewhere quiet, close to the microphone. A phone voice memo is fine.
- Speak the lecture the way you want it heard; the recording is the final narration.
- If you have slides, keep the PDF handy: the animation will follow their diagrams,
  equations and terms.
- You can record the whole lecture as **one file**, or each section as **its own file**
  (name them `scene_1`, `scene_2`, … so they sort in order). Separate files are easiest
  to fix later, because you can re-record just one section.

### Step 1: Upload audio
1. On the home page, type a project name and click **New project**.
2. Choose **One recording** or **One file per scene**.
3. **One recording:** pick the file and click **Upload & transcribe** (usually under a
   minute per 10 minutes of audio). **One file per scene:** select all the files at
   once and check the scene numbers.
4. Optional, but it helps:
   - **Slides or notes (PDF)**.
   - **Images** the animation may show (photos, diagrams, logos).
   - **Notes for the planner**, e.g. "White background. First-year students. Use my
     photo in the intro."
5. Click **Create scene plan** (or **Transcribe & create plan**). This takes about 1–4
   minutes.

### Step 2: Review plan
Each scene shows its slice of your audio and a description of what it will show. You
can edit the titles, the descriptions, and the overall **visual style** (colors,
background, recurring objects). The clearer the description, the better the result.
The narration itself can't be edited here, because it *is* your recording. Click
**Continue to Animate**.

### Step 3: Animate
1. Click **Animate all remaining**. Each scene takes about 2–8 minutes (longer scenes
   take longer). Keep the browser tab open. You can watch finished scenes while others
   are still being made.
2. The dots in the scene list show status: **green** means done, **red** means it needs
   a fix, **blue** means not animated yet.
3. **Watch each scene.** To change something, type it in **Request changes** and click
   **Make new version**.
4. **Red dot (render failed)?** Open the scene: the error is already filled in, so just
   click **Make new version**. This usually fixes it in one try.
5. Every version is kept. Under **Versions**, click one to watch it, and click **Use in
   final video** to choose it.

**Tips for good feedback**
- Use times from the player: "At 0:40, show the arrows one by one as I name them."
- Say what's wrong *and* what you want: "The table appears too early. Show it when I
  say 'random policy', and remove it when I move on."
- Point out factual errors directly: "The return is −1, not −20."
- One scene at a time; several changes in one message is fine.
- You can attach a screenshot to show what you mean.

**Re-recorded a section?** In that scene, click **Replace this scene's recording**,
then request changes like "Re-time the animation to the new narration."

### Step 4: Export
1. Optional: fill in a **Title** card (shown for 5 seconds at the start) and
   **Credits** (6 seconds at the end). Leave them blank to skip.
2. Click **Create final video**, then **Download MP4**.
3. **Download everything (ZIP)** also gives you each scene's clip, code and audio.

If you change a scene later, click **Re-create final video** again.

## 5. Troubleshooting

| Problem | What to do |
| --- | --- |
| "Add your … API key" / "rejected the API key" | Open **Settings**, re-paste the key, click **Test keys**. It also warns if a key is in the wrong field. |
| "out of credit" / "over its quota" | Add credit in that service's billing page (Anthropic or OpenAI). |
| A scene failed to render (red dot) | Open it and click **Make new version**: the error is pre-filled. |
| "Claude returned no code" | Try again. If it repeats, go to Settings → Advanced and lower **Thinking effort** to "medium". |
| Everything is slow | Settings → Advanced → **Video quality: 720p**. Close other heavy apps; give Docker more memory (Docker Desktop → Settings → Resources). |
| Visuals don't line up with the voice | Request changes with the time and the words: "Show X when I say 'Y' (around 1:15)." |
| A scene has no audio after planning | The planner couldn't match it to your recording. Re-plan, or use **Replace this scene's recording** with just that part. |
| The page won't load | Make sure Docker Desktop is running and the **lecture-animator** container is started, then reload <http://localhost:8000>. |
| "port is already allocated" when starting | Something else uses port 8000. Run the command again with `-p 127.0.0.1:8080:8000` and open <http://localhost:8080>. |
| "container name already in use" | The app is already installed: start it from Docker Desktop instead of running the command again. |

## 6. Updating to a new version

Your projects are kept in a separate storage area (`lecture-animator-data`), so
updating doesn't touch them. In a terminal:

```
docker pull ghcr.io/ahuang915/lecture-animator:latest
docker rm -f lecture-animator
docker run -d --name lecture-animator --restart unless-stopped -p 127.0.0.1:8000:8000 -v lecture-animator-data:/data ghcr.io/ahuang915/lecture-animator:latest
```

## 7. Privacy

- Your recording is sent to **ElevenLabs** (or **OpenAI**, if that's the transcription
  key you use) to be transcribed.
- The transcript, your notes, your slides (PDF) and the scene descriptions are sent to
  **Anthropic** (or **OpenAI**) to plan and write the animations.
- Images you add for the animation stay on your computer. Only their file names are
  sent. Screenshots you attach to a change request *are* sent, so Claude can
  see them.
- Everything else (recordings, renders, videos) stays on your computer, inside Docker's
  `lecture-animator-data` storage.
- Your API keys are stored only in your web browser.
- Deleting a project in the app deletes its files.
