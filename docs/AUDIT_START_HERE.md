# Start here — a second opinion on AutoClip

Thanks for doing this. Here's what I'm asking for, what you need to get going, and what
would actually be useful to hear back.

**The ask, in two sentences:** use the product like a real creator would, then skim the code
behind it, and tell me what you like and what you don't. I'm not looking for a formal audit
— I want an outside gut reaction from someone who has seen a lot of software.

> **One request on order: use it first, before you read any code or any other doc in this
> repo.** First impressions only happen once, and I've been staring at this for months —
> I can't see it fresh anymore. Once you've formed a view, poke around as much as you like.

---

## What it is

Creators record long videos. Short vertical clips are what actually grows a channel, but
cutting them by hand is slow, and every AI tool that does it automatically produces
generic results — it finds a funny moment and cuts around it.

AutoClip tries to do it *for your specific channel*. It reads your YouTube analytics — which
videos held attention, where people dropped off, who's watching, when they're online — and
builds a profile it calls your **Creator DNA**. Then when you give it a long video, it finds
moments that fit *that* profile, and cuts them starting at the **setup** rather than the
punchline, so a viewer lands with enough context to care.

You review the clips one at a time — keep, drop, skip, or trim — and it learns from that,
weighting what you said recently more heavily than what you said months ago.

**The one thing it deliberately does not do is promise you'll go viral.** It predicts *fit*
with your style and audience, and says so everywhere. That's a product principle, not
marketing hedging — if you spot anywhere that language slips into promising reach, that's a
real finding and I want to know.

It's live at **autoclip.studio**. Nobody outside a tiny group has used it yet. That's the
problem I'm trying to solve by asking you.

---

## Before you start — three things I need to set up for you

**Tell me if any of these are a problem, because two of them are hard gates.**

1. **I have to add your Google account to the allowlist.** The app is still in Google's
   "Testing" mode, so only accounts I've explicitly added can sign in. Send me the Google
   address you'll use and I'll add it before you start.

2. **⚠️ You need a YouTube channel with real analytics history.** This is the big one. The
   product can't do anything meaningful without it — it needs **at least 10 long-form videos
   or 5 Shorts** that have actual view/engagement data behind them. If your channel is empty
   or tiny, you'll hit a wall at onboarding and everything downstream is unreachable.

   If you don't have a qualifying channel, **say so now** and we'll do this differently — I
   can walk you through the product on a call using my channel, and you can spend your time
   on the code instead. Don't burn an evening discovering this.

3. **You get 60 free minutes automatically, for 7 days.** No payment, nothing to enter. But
   the clock starts the moment you sign in, so don't sign in until you're ready to actually
   use it. One minute of source video costs one minute of balance. 60 minutes is enough for
   two or three real videos.

---

## Part 1 — use it

Rough path, about 30–45 minutes including waiting for processing. There's a fuller tutorial
at `walkthrough.md` in the repo root if you want more detail, but you shouldn't need it —
**and if you do need it, that itself is the finding.**

1. **Sign in** at autoclip.studio with Google. Connect your channel.
2. **Let it pull your channel data.** Takes 30–60 seconds.
3. **Answer the questions about yourself** — your niche, your audience, what you won't do.
   This step is mandatory and I'd like to know whether it feels worth the friction.
4. **Build your DNA**, then read the brief it writes about your channel. **This is the
   moment that matters most.** Does it describe your channel accurately? Does it tell you
   anything you didn't already know? Or does it read like horoscope copy that would fit
   anyone?
5. **Give it a video** — upload one or link one from your channel.
6. **Generate clips**, then open the review queue. Go through them properly. Hit
   "Why this clip?" on a few.
7. **Poke at the rest** — Insights, titles, thumbnails, hooks, the chat assistant.

### What I actually want to know

Don't hunt for bugs. React to it.

- **Where did you get confused, stuck, or annoyed?** Exact moments are gold.
- **Are the clips any good?** The honest answer. Would you post one? This is the whole
  product — everything else is packaging.
- **Does it start clips in the right place?** That's the core claim: the setup, not the
  punchline. Does it deliver?
- **Did anything feel slow, or leave you unsure whether it was working?**
- **Does the DNA brief feel like it knows your channel, or could it be about anyone?**
- **Would you pay for this?** At $18 for 200 minutes. If not, what would have to be true?
- **What's missing that you expected to be there?**

---

## Part 2 — skim the code

Only after you've used it. No need to be systematic — I want the reaction of someone who
has maintained a lot of code, not a line-by-line review.

It's a Python backend (FastAPI, background job queue, Postgres) with a React frontend.
About 134,000 lines of Python and 35,000 of TypeScript. Written by one person, fast, with
heavy AI assistance — which is exactly why I want someone else's eyes on it.

Reasonable places to look:

| Where | What's there |
|---|---|
| `clip_engine/` | The actual clip-finding logic — scoring, ranking, the setup-detection rule, ffmpeg rendering |
| `routers/` | The API. `clips.py` is the biggest surface |
| `worker/tasks.py` | The background pipeline. **It's 7,179 lines in one file** and I know that's a lot — I'd like your read on whether it genuinely needs splitting |
| `frontend/src/` | The React app |
| `billing/` | Stripe checkout and the minutes ledger |
| `docs/` | Design decisions, the issue backlog, launch checklist |

### What I want to know

- **What would you not want to inherit?** If you took this over on Monday, what's the first
  thing you'd want changed?
- **What's over-built?** 134k lines for a product with no users yet feels like a lot to me.
  What would you delete?
- **What's under-built?** Where does it feel thin or rushed relative to everything else?
- **Anything that made you wince.**
- **Anything that genuinely impressed you.** Useful in both directions — I can't tell
  anymore which parts are solid and which just *feel* solid because I wrote them.

One warning that will otherwise mislead you: **there are essentially no `TODO` comments in
this codebase.** That's not because there's no unfinished work — it's a project rule that
known problems go into `docs/issues.md` and `docs/OFF_COURSE_BUGS.md` instead. If you grep
for TODO you'll find one hit and get a much rosier picture than is accurate.

---

## Things that look broken but aren't

So you don't spend findings on them:

- **"Google hasn't verified this app"** on the sign-in screen. Expected — verification is
  submitted separately and hasn't been done yet. It's safe to click through.
- **You may be asked to reconnect YouTube after 7 days.** A Google restriction on unverified
  apps, not a bug.
- **A page that says you need more videos.** That's the data gate from prerequisite 2, not a
  failure.
- **The name is inconsistent.** The code says "CreatorClip", the product says "AutoClip".
  Mid-rename. I know.

---

## Getting it back to me

However is easiest — a doc, a list of notes, or just a call where you talk and I take notes.
No format requirements.

If something's a maybe rather than a definite, say so. **A short list of things you're
confident about is far more useful than a long list padded with hedges.**

If you'd rather go deeper on the technical side, there's a more detailed brief in
`docs/AUDIT_BRIEF.md` and a list of problems I already know about in
`docs/AUDIT_KNOWN_ISSUES.md` — but **please don't read that second one until you've told me
what you think.** I want your reaction, not your agreement with mine.
