# Transcription timing fixtures (Issue 481)

Real known speech + recorded provider responses so word-timestamp fidelity is
tested against ground truth instead of hand-typed timings. `sentence_snap`
anchors every clip opening on these timings — a systematic offset here would
shift every clip boundary while every eval stayed green.

## Files

| File | What it is |
|---|---|
| `librispeech_1089-134691-0001_16k.wav` | 5.415 s of known speech, 16 kHz mono PCM |
| `deepgram_nova3_response.json` | **Recorded** raw Deepgram nova-3 response for that WAV (verbatim `.to_dict()`) |
| `assemblyai_schema_derived_response.json` | **Schema-derived, NOT recorded** AssemblyAI response (see below) |
| `expected_words.json` | Ground-truth word windows + tolerance (derivation below) |

## Provenance and license

Audio is LibriSpeech utterance **1089-134691-0001** (test-clean split),
speaker 1089, chapter 134691 — public-domain audiobook speech distributed
under **CC BY 4.0** (https://www.openslr.org/12):

> V. Panayotov, G. Chen, D. Povey, S. Khudanpur, "LibriSpeech: an ASR corpus
> based on public domain audio books", ICASSP 2015.

Reference transcript (from `1089-134691.trans.txt`):

> FOR A FULL HOUR HE HAD PACED UP AND DOWN WAITING BUT HE COULD WAIT NO LONGER

## Exact reproduction commands

```bash
# 1. Fetch the corpus (346 MB) and take the one utterance + its transcript
curl -sL https://www.openslr.org/resources/12/test-clean.tar.gz | tar -xz
# -> LibriSpeech/test-clean/1089/134691/1089-134691-0001.flac
#    LibriSpeech/test-clean/1089/134691/1089-134691.trans.txt

# 2. Convert to the ingestion format (16 kHz mono PCM WAV)
ffmpeg -i LibriSpeech/test-clean/1089/134691/1089-134691-0001.flac \
       -ar 16000 -ac 1 -c:a pcm_s16le librispeech_1089-134691-0001_16k.wav
```

`deepgram_nova3_response.json` was recorded 2026-08-13 with **one real
Deepgram API call** over that WAV, using byte-identical request options to
production (`ingestion.transcribe._deepgram_prerecorded_options`: model
`nova-3`, `smart_format=True`, `utterances=True`, `diarize=True`, addons
`{"mip_opt_out": True}`), then saved verbatim from `.to_dict()`.

## expected_words.json — ground-truth derivation (honest method)

No human audition was performed. The ground truth is a cross-check of two
independent sources plus structural sanity gates:

1. **Words** come from the LibriSpeech reference transcript (17 words) — the
   corpus's own labels, not the ASR output.
2. **Timings** come from the recorded Deepgram response, accepted only after
   audition-free sanity checks (`derive` script, run at freeze time):
   - the recognized word sequence equals the reference transcript exactly
     (case/punctuation-insensitive, 17/17);
   - timings are strictly monotonic and non-negative;
   - all timings fall inside the ffprobe'd file duration (5.415 s).
3. **Tolerance ±0.25 s**: Deepgram publishes no timestamp-accuracy SLA; their
   own guidance describes word timings as reliable to roughly word-boundary
   precision (https://deepgram.com/learn/working-with-timestamps-utterances-and-speaker-diarization-in-deepgram).
   0.25 s is half the shortest plausible word gap that could flip a
   `sentence_snap` boundary and comfortably above observed re-run jitter,
   while still catching the failure class this fixture exists for (systematic
   offsets ≥ one word).

Because the timings are model-derived, this fixture pins **provider-timing
drift and normalizer regressions**, not absolute phonetic onset truth — a
constant sub-250 ms bias shared by every provider would be invisible. That is
an accepted limitation, recorded in `docs/DECISIONS.md`.

## assemblyai_schema_derived_response.json — honesty label

**Schema-derived, not recorded.** No `ASSEMBLYAI_API_KEY` was available on the
build box, so this file was built from the documented AssemblyAI transcript
schema (https://www.assemblyai.com/docs/api-reference/transcripts/get):
word objects carry `text` / `start` / `end` with **millisecond integer**
timings (values here are the ground-truth windows × 1000) and letter speaker
labels. It pins the normalizer's ms→s unit-scale contract (`÷ 1000`), NOT
AssemblyAI's live behavior. If a key becomes available, re-record and drop the
`_provenance` marker.

## WhisperX

Deliberately no recorded WhisperX leg — the torch install (~2 GB) is
disproportionate for a parse-fidelity fixture; WhisperX normalizer shape stays
covered by the synthetic tests (`docs/DECISIONS.md` 2026-08-13).
