# CreatorClip — Sub-Processor List (Art. 28 / Art. 30 Record)

This document serves as both the publicly-linkable sub-processor list required by GDPR Art. 28
and the Art. 30(2) record of processing categories carried out on behalf of controllers.

**Last updated:** 2026-06-23 (Issue 251)

All sub-processors are bound by a Data Processing Agreement (DPA) or equivalent contractual
instrument. Where personal data is transferred outside the EEA, the transfer mechanism is noted.

---

## Sub-Processors

| Vendor | Purpose | Personal Data Categories Processed | Region | Transfer Mechanism | DPA / Opt-out |
|--------|---------|-----------------------------------|---------|--------------------|--------------|
| **Anthropic** | LLM inference (DNA synthesis, clip scoring, chat) | Creator analytics summaries and behavioral patterns (no raw PII, no email, no OAuth tokens enter prompts — redacted at call sites) | US | Standard Contractual Clauses (SCCs) | Anthropic Commercial Terms (API account, not consumer); prompt-caching enabled; no data used to train models under commercial API terms |
| **Voyage AI** | Text embeddings for semantic search + DNA similarity | Anonymized transcript segments, creator DNA profile text | US | SCCs | Voyage AI API Terms; training opt-out enforced via commercial API terms |
| **Deepgram** | Speech-to-text transcription | Creator audio (spoken word only; no video frames uploaded) | US | SCCs | Deepgram DPA; **`mip_opt_out=True` enforced on every API call** (Issue 251) — audio is NOT enrolled in the Model Improvement Partnership program |
| **Cloudflare R2** | Object storage (source video + rendered clips) | Uploaded video bytes, rendered Shorts clips (creator-owned content) | US (configurable) | SCCs / Cloudflare Data Processing Addendum | Cloudflare DPA signed; source video purged within 72h of ingest completion per `SOURCE_MEDIA_RETENTION_HOURS` |
| **Stripe** | Payment processing | Name, email, payment card data (Stripe-tokenized; CreatorClip never sees raw card numbers) | US | SCCs | Stripe DPA + PCI DSS compliance |
| **Google / YouTube** | OAuth identity + YouTube Data API (channel metadata, analytics) | YouTube channel ID, channel title, video metadata, aggregated analytics (retention, demographics) | US / EU | SCCs / Google Cloud DPA + Supplementary Measures | Google API Services Terms of Service; data must be deleted within 30 days if authorization cannot be re-verified (§III.E.4.b) — enforced by Wave-4 Fix 3 Beat task |

---

## Ops Runbook (external — not codeable)

Before public launch, verify the following for each sub-processor:

- [ ] **Anthropic**: confirm account is under the commercial API agreement (not consumer
  `claude.ai`); no-train clause confirmed in API ToS.
- [ ] **Voyage AI**: confirm commercial API account; verify storage/training opt-out
  is active in the vendor dashboard.
- [ ] **Deepgram**: confirm DPA is signed with the account team; confirm MIP opt-out
  is reflected in account settings (code enforces `mip_opt_out=True` — this is belt-and-suspenders).
- [ ] **Cloudflare R2**: confirm Data Processing Addendum is accepted in Cloudflare
  dashboard; confirm no cross-region replication that would change the transfer mechanism.
- [ ] **Stripe**: confirm Stripe DPA is signed; confirm PCI SAQ-A compliance posture
  (card data never touches CreatorClip servers — Stripe.js + Payment Element).
- [ ] **Google**: confirm Google Cloud DPA + Supplementary Measures are in place for
  the project; confirm YouTube API Services ToS compliance posture (OAuth verification
  completed — Issue 29).

---

## Reference

- GDPR Art. 28: https://gdpr-info.eu/art-28-gdpr/
- GDPR Art. 30: https://gdpr-info.eu/art-30-gdpr/
- docs/COMPLIANCE.md — full data class table + privacy posture
- docs/DECISIONS.md — 2026-06-23 entry (Issue 251)
