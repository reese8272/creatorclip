# CreatorClip — Clipping Principles

Named principles the engine cites when explaining clip selections or content briefs.
Every clip score must cite at least one principle from this list.
When a new principle is applied, add it here before merging.

---

## The Core Mechanic

**Clip the setup, not the aftermath** — competitors react to peaks (chat spike, loud audio) that
occur *after* a moment lands, so they clip the aftermath. CreatorClip holds a rolling 60–90s
context window; when a peak signal fires it looks **backwards** to where the setup began (a
sentence/beat boundary in the word-level transcript, preceded by a quieter audio baseline) and
starts the clip there. The clip ends after the payoff resolves — not at the reaction.

---

## Named Principles

| # | Name | Description |
|---|------|-------------|
| 1 | **Hook in the first 3 seconds** | The opening determines retention; weak openings lose the audience before the payoff. |
| 2 | **Clip the setup, not the aftermath** | Start where the beat begins, not where the reaction peaks. This is the engine's core differentiator. |
| 3 | **Tension and release** | A clip needs a setup and a payoff; a payoff with no setup feels random. |
| 4 | **Pattern interrupt** | A change of beat every few seconds holds attention. |
| 5 | **Dead-air elimination** | Trim silence and filler; momentum is retention. |
| 6 | **Retention curve is ground truth** | Rewatch spikes mark genuinely high-value moments; lean on the creator's own data over generic heuristics. |
| 7 | **Loop-ability** | Shorts that loop cleanly retain; favor cut points that resolve. |
| 8 | **Front-load value** | Never bury the payoff late in the clip. |
| 9 | **One idea per Short** | A single clear beat outperforms a montage. |
| 10 | **Native length over generic length** | Match *this creator's* proven optimal Short length, not a fixed 60s. |
| 11 | **Audience-fit over generic virality** | Every score is against this creator's DNA and audience, never a one-size signal. |
| 12 | **Clean Context Boundary** | Clips must never start or end mid-sentence. Both cut points are snapped to the nearest terminal-punctuation token or silence gap so every clip opens and closes on a complete thought. This is the direct fix for the #1 user complaint about every competing tool. |

---

## Usage in the Engine

The engine cites the principle in one line when explaining a clip or improvement brief.
A creator can ask "why this clip?" to get the extended reasoning on demand.
The engine never lectures unprompted — one-line citation only in automated output.
