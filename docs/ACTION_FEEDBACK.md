# Creative action feedback (Studio #101)

Studio 1.2.1 keeps the primary creative action buttons clickable when settings
are incomplete, invalid or unavailable. A click displays and focuses named
problems without sending a request, allocating a new request UUID or changing
the draft. Correction updates the explanation but never automatically submits;
press the action again explicitly. In-flight operations still prevent duplicate
submissions. Image, Video, Music and Speech forms bypass native validation
bubbles so numeric/required HTML constraints cannot silently intercept clicks.
Existing payload and backend validation remain authoritative.

| Operation | Examples of feedback |
| --- | --- |
| Image Generate | Prompt, model, template, reference availability, dimensions, LoRAs |
| Video Generate | Recipe identity, start/end images, dimensions/pixel budget, 4n+1 frames, FPS, duration, seed |
| Music Generate | Style/lyrics limits, duration, steps, both seeds, availability |
| Music Transcribe | Select and attach a WAV Asset, maximum length, operation availability |
| Speech Generate | Text/description limits, duration, steps, seed, reference WAV, availability |
| Intelligence Run | Prompt/attachment byte budget, model/operation, fresh availability, context/output budget, temperature |
| Assistant Send | Question/UTF-8 byte bound, active model match, fresh session, pending switch, valid attached Image draft |
| Assistant model controls | Choose an available model; reconcile an uncertain switch using the same saved identity |
| Personality Save | Unsaved changes, display name, ordered unique section titles/bodies, byte limit, shared-personality acknowledgment |

An active or unconfirmed media request also explains why new work cannot be
submitted. The existing request UUID and owner-scoped recovery/result controls
remain intact; clicking Generate cannot override their fences. Intelligence and
Assistant preserve their explicit new-request/conversation actions. A model
switch retry keeps its original immutable identity. Read-only personality
permissions and frozen uncertain saves remain enforced, with their existing
status/recovery explanations. Structural controls such as moving the first item
up, pagination boundaries and controls inside a read-only editor remain bounded.
This change covers the creative generation/send/save actions above, rather than
turning every disabled UI control into a network action.

Image's Service ready badge describes discovery availability, not completeness
of its draft or GPU/model quality. The internal PHASE 1A header badge is removed.
The historical Ready-but-disabled trigger remains unknown; no live generation,
GPU scheduling or automatic recovery behavior is changed by this UI repair.

Validation includes invalid click/keyboard actions, native numeric constraints,
correction without automatic submission, active/unknown no-replay fences, owner
switches, unchanged request identities and existing read-only permissions.
Source/CI success is separate from actual browser/operator acceptance.
