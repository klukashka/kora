# Local journal and checkpoint durability proposal

Historical design record: the daemon-writer, pre-registration-only exit and startup per-sync clock below are superseded by the owner-approved [separate-helper contract](../specs/startup-io.md). Later running-state proposals remain unapproved; none is implemented by this design.

## Status and scope

Issue #615 is the design child of #196. The owner approved preparation and subsequently the startup-only contract on 2026-11-01: startup-before-registration, the 16 KiB record/checkpoint bounds, one operation, Running.startup and crash/cleanup semantics. Later running-state capacities, index representation and terminal formats remain proposals. The normative startup contract is recorded in protocols.md; the proposal wording below preserves the design rationale. Inspected base: `d2f14e3aa83421a0c1261c808d49d1e281d14f73`. No production implementation or platform experiment is included. Existing requirements remain authoritative until the owner accepts an amendment and it lands in the normative specification.

The [C2 decomposition](https://github.com/realkarych/kora/issues/196#issuecomment-6488037673) includes journal/checkpoint durability, transfer, command execution and adoption. [C1](https://github.com/realkarych/kora/issues/195) is complete through #614. The first durability implementation must have a real supervisor consumer and complete its own failure/cleanup paths; an unused store protocol is not a milestone. Runner-side full-disk fencing and terminal retry remain C2 obligations. Only worker fallback, public retirement/recovery, final release and provider registration remain #197.

## Existing rules retained

[Runtime protocols](../specs/protocols.md#local-runtime-issue-123) already specify canonical JSONL `{i, at, kind, data}`, append-assigned contiguous sequence, one writer, durable supervisor acknowledgement, Linux `fdatasync` and macOS `F_FULLFSYNC`, a five-second sync deadline, output batching at 50 ms or 256 records, and sparse indexing every 4,096 records. A readable complete line is not necessarily acknowledged durable. The first ENOSPC/EDQUOT append or sync failure permanently closes that process's journal for appends. Stop escalation and runner exit never wait on the writer or a registry lock.

The runtime is cooperative: same-user modification cannot be cryptographically excluded. Integrity checks detect mismatched or corrupted state but do not establish hostile-code isolation, dispatcher authority or termination. ADR-0016/0017/0018/0022/0026 continue to govern those boundaries.

## Recommended first consumer

Use startup durability **before** the existing Starting-to-Running registration CAS and readiness byte. The actual consumer is `supervise`: it requires one factual `runner.started` record and matching startup checkpoint, waits for the writer to close its owned descriptors and finish, and only then attempts registration. The checkpoint says `registration_pending`, never workload accepted, preparation admitted/completed, candidate started or termination confirmed.

Take the immutable attempt/request/workload/deadline binding and launch nonce from the checked Starting head; the workload has not yet crossed the channel. Bind the writer's own complete process identity. Recheck nonce, fence and entry at registration exactly as the current CAS does. The record/checkpoint is not a replacement for that CAS. On startup failure there is no readiness byte and no candidate; Starting remains available to its existing retirement path. A journal/checkpoint survivor never authorizes automatic restart.

`accepted` and `holds_workload` keep their #611 delivery/retention meaning. The first child adds no journal writes after registration and closes its startup writer before serving. Later running-state producer activation must bring its complete memory fence, authentic stop/terminal state and retry together. This boundary prevents a running journal from relying on an unimplemented #197 receipt path.

This recommendation changes startup ordering and requires explicit owner approval. Amend the old phrase that failed registration “writes nothing”: failed startup before registration may leave bounded private startup journal/checkpoint artifacts but creates no Running entry. A registration failure after head rename has an uncertain committed outcome and preserves potentially referenced artifacts. Neither failure produces readiness, a candidate or a completion/termination receipt. Existing registry/starting-retirement authority and deadlines remain unchanged. The runtime is still unregistered and its persisted intermediate heads exist only in tests; old heads missing the required startup reference are refused without migration/backfill, consistent with the existing interim-state policy. A populated external deployment would need a separate migration decision. Do not claim that these startup facts satisfy C2's command checkpoint or full-disk running-state obligations.

## Proposed storage layout and input discipline

Use a nonce-and-writer-specific private startup directory beneath the checked attempt control directory, opened through `DirectoryAnchor`; no pathname supplied by a workload chooses these files. Files are regular, private and single-link; open descriptor-relative with no-follow, refuse conflicting types/owners/aliases, and retain the anchor until its last actual operation finishes. Candidate directories never contain these files.

- `journal.jsonl`: one startup append-only stream, containing exactly one initial record in this child; later journal activation needs a separate owned continuation design.
- `checkpoint.json`: one atomically replaced, closed versioned startup checkpoint.
- No index is created for one startup record. The proposed later index remains derivative and is never an authority source.
- Existing registry head and lock remain in their established location. Retained peer/history records remain separate and are referenced, never copied into the checkpoint.

Reuse canonical JSON and existing bounded identity/deadline models. Reject duplicate keys, unknown members, non-finite numbers, invalid UTF-8 and lone surrogates before model acceptance. Enforce file bounds before decoding and field/collection bounds afterwards. Unknown format versions fail closed without rewriting or backfilling from a new workload.

## Proposed initial checkpoint

The first variant is `local-startup-checkpoint/1`, with exactly these fields:

| Field | Representation and source |
|---|---|
| `format` | Literal `local-startup-checkpoint/1` |
| `execution_domain_id`, `attempt_id` | Existing bounded `Identifier`, equal to the checked registry key |
| `request_digest`, `workload_digest` | Existing `Digest`, equal to the checked Starting head |
| `runner` | Existing complete `ProcessIdentity`, the actual writer; never current reader identity substituted on reopen |
| `deadline` | Existing nullable `ExecutionDeadline` wire representation, exactly the original registry binding |
| `stage` | Literal `registration_pending` |
| `launch_nonce` | Existing 32 lower-case hexadecimal nonce, equal to Starting |
| `journal_next` | Existing `Count`, exclusive sequence after the referenced startup record |
| `journal_end` | Existing `Count`, exclusive byte offset after that complete newline |
| `record_digest` | `Digest` of the complete canonical startup line excluding its newline |

Proposed encoded checkpoint cap: 16 KiB, including its complete canonical envelope. There are no arbitrary maps, payloads or optional future-phase bags. The bounded identifiers, identity and deadline require a worst-case serialization test below this cap before implementation acceptance. It contains neither full Workload nor environment, token, resolved secret/credential, secret-derived digest, mutable candidate path or fabricated executor result. Stored logical digests retain the existing #611 non-authority meaning; no new secret-derived hash is introduced.

The startup record is a supervisor `runner.started` RawEvent with closed data `{stage: "registration_pending", launch_nonce, request_digest, workload_digest}`. `i` starts at zero in a new journal and uses existing `Count`; `at` uses `Instant`. Exhaustion of sequence range refuses another append and closes admission without wrapping. This is a new exact startup-data form requiring approval; later lifecycle variants cannot be accepted through generic unvalidated data.

For this closed startup variant only, the proposed encoded line cap is 16 KiB including newline. This does not reduce the existing generic 64 KiB RawEvent data allowance: arbitrary RawEvent input is not accepted by this startup writer. Test every bounded startup field at its escaped maximum. The later general journal writer must select an envelope cap that accommodates the full existing data allowance. Arbitrary supervisor kinds are not admitted by the generic RawEvent model: each activated kind has a closed typed ingress. Adapter/output ingress cannot emit supervisor kinds.

## Commit and acknowledgement order

One startup operation owns both records. The writer assigns `i=0`, writes the complete line, syncs journal data, writes/syncs a unique checkpoint temporary, atomically replaces `checkpoint.json`, and syncs its parent directory. Every newly created directory entry is also durably published by syncing its parent, including the startup directory and any newly created attempt/control ancestors, before registration is attempted. Syncing the startup directory alone does not persist its name in its parent. Only then can the local operation report durable startup facts. The writer closes its actual descriptors and exits before the supervisor attempts the existing fresh registration CAS and emits readiness. A checkpoint is never registration authority.

Each launched process owns a freshly created startup directory with a component derived from its launch nonce and a fresh 128-bit random identifier. Creation uses no-replace/no-follow and never takes ownership of a pre-existing directory. The startup directory component is exactly `<launch_nonce>-<writer_nonce>`, two 32-character lower-case hexadecimal strings. A new required `Running.startup` closed reference contains only `directory` (that 65-character component) and `checkpoint_digest` (Digest of the whole canonical checkpoint). It remains within the existing 64 KiB head bound. The reference is stored in the same registration CAS; cleanup may delete only its own directory after the writer actually releases it and a current head read under the registry lock proves no retained reference to that directory. A registration write can rename Running successfully and then fail directory sync: that is an uncertain commit, not a CAS loser. If a matching reference exists or the current head cannot be read/validated, preserve the directory and refuse cleanup; never infer absence from a raised write error. A registry reader cannot select an unreferenced directory as evidence. On a failed CAS or crash, existing nonce retirement confirms all matching launch processes stopped before sweeping unreferenced startup directories. Orphan enumeration is streaming and bounded by the existing operation deadline; do not build a list proportional to retained directories or hold the registry lock across the full sweep. Before each destructive action, acquire the existing lock, revalidate the relevant head/reference and confirmed writer absence, and remove only the fixed owned startup files. Incomplete inspection remains retryable under the same deadline. Do not sweep or overwrite another live writer's files merely because its nonce matches. The implementing plan must account for this reference field and retirement cleanup in its production size.

The first child never retries an append in place after a failed write. Its one writer has one operation, one acknowledgement and at most one complete record. Cancellation of the waiter does not transfer ownership of native I/O or descriptors. A late completion after a failed startup is ignored for registration; no ready byte follows it. If native I/O is stuck at the original five-second sync deadline, the runner exits without interpreter shutdown waiting for that writer; the operating system closes process-owned resources. No other thread closes a descriptor while the writer uses it.

| Crash or refusal barrier | Persistent interpretation | Action |
|---|---|---|
| Before append | No startup record/checkpoint | No readiness; Starting retirement remains the resolver |
| Partial append | Incomplete final line | Ignore tail; no registration from it and no second writer admitted by it |
| Complete line, journal sync incomplete/failed | Readable record, durability unknown | No readiness or registration; exit on failure |
| Journal sync completed, checkpoint absent | No complete startup commit | No registration; preserve or clean only this writer's files after actual closure |
| Checkpoint temporary written/synced, rename absent | No committed checkpoint | Never use temporary as committed state |
| Rename completed, directory sync incomplete | A complete checkpoint may survive without a completed durability acknowledgement | No registration/ready; existence and digest validation alone do not prove completed sync |
| Durable checkpoint, writer still closing | Durable startup facts, resources still owned | No registration until actual closure; failure still exits without candidate |
| Writer closed, before registration CAS | Durable startup facts, no Running entry | No automatic restart or execution authority; current CAS remains required |
| Registration CAS is a proved no-op after nonce/fence race | Owned startup artifacts are unreferenced | No ready/candidate/receipt; after writer closure, current locked head must prove no reference before cleanup |
| Registration rename succeeds but registry directory sync fails | A Running reference may survive although acknowledgement failed | No readiness or assumed rollback; preserve startup artifacts while a reference exists or current head is uncertain; existing observation/retirement resolves the state |
| Registration succeeds, ready byte lost | Running references exact startup record/checkpoint | Existing C1 lookup/retry semantics; do not start another runner |
| Referenced checkpoint/journal mismatches identity or digest | Foreign/corrupt state | Content-free infrastructure refusal; no reconstructed success or replacement execution |

The live writer distinguishes readable and synced positions. An on-disk complete line or checkpoint cannot retrospectively prove that an acknowledgement was sent. A later consumer that needs durability must re-establish its required sync under valid exclusive ownership before acknowledging a replay; it cannot treat validation alone as the missing sync. This child does not implement fallback append, running-phase replay, journal adoption or a new process taking over the startup journal.

## Proposed index

Use fixed-width network-order pairs of unsigned 64-bit `(sequence, byte_offset)`, one entry for sequence zero and each multiple of 4,096. With the proposed 257 MiB soft-plus-overflow journal envelope, ordinary index capacity is bounded by the minimum valid record size and checked separately; exceptional terminal records are finite by the closed kind/phase set. Index lookup must verify offset alignment, sequence and complete record against the journal before using a hint. Invalid, truncated, absent or unwritable index disables it; bounded streaming scan remains correct. An index never certifies durability or permits skipping malformed interior records.

The first implementation does not expose indexed readers. The subsequent indexed-reader slice owns index construction, corrupted-hint tests, index-disabled resume and actual memory/descriptor closure. No index functionality is claimed by the initial startup checkpoint alone.

## Proposed capacities and writer ownership

The existing journal/log capacities are still called proposals in the specification. Recommend accepting them unchanged: journal 256 MiB plus its specified 1 MiB supervisor overflow; stdout/stderr 64 MiB each; runner diagnostics 16 MiB; crash output 1 MiB. Existing once-per-attempt terminal exceptions remain; this is not a strict 257 MiB total-file guarantee. Output batching and the adapter queue remain their existing 50 ms/256 records and 4 MiB/4,096-record rules. Command-output activation must explicitly preserve its own gap/count semantics.

For the first startup consumer, admission is exactly one operation with at most one 16 KiB line and one 16 KiB checkpoint, including queued and in-flight buffers. The initial journal contains exactly one record `i=0`; the checkpoint requires `journal_next=1`, `journal_end` equal to that line length and a matching record digest. These 32 KiB of final encoded buffers are not a total heap guarantee: schema-bounded serialization intermediates are measured separately. No queue of startup waiters is created. The remainder of this section is deferred design input, not part of the immediate format approval. For later running producers, the provisional recommendation is at most 16 queued-plus-in-flight operations and 2 MiB of their encoded payloads combined, including checkpoint bytes. One operation owns all of its journal/checkpoint buffers and one completion future. Reserve before allocation/encoding beyond the declared operation bound; do not create an unbounded waiting queue. A caller outside the accepted operation set receives a fixed retryable busy refusal. Required internal terminal/fault work uses fixed dedicated state, not a seventeenth ordinary operation or an unlimited priority queue. Sixteen is a proposal aligned to the current maximum control connections, not proof that internal producers can never saturate it.

One daemon writer owns journal/checkpoint descriptors and buffers until the native operation returns. A loop-owned controller owns admission, the sticky memory fence, notification and timers. The existing `TransitionExecutor` must not own this writer: its close waits for real jobs and thread shutdown, contrary to the journal stop/exit contract. Reuse filesystem atomic replacement without duplicating registry semantics; journal data uses fdatasync on Linux, and directory/registry durability keeps the existing appropriate sync operation.

Normal closure stops admission, drains acknowledged work, receives a writer-closed notification, and verifies descriptor/thread release. Forced process exit after unresolved sync does not falsely report thread closure: the OS reclaims process-owned descriptors. No other thread closes/reuses a descriptor while the blocked writer still uses it. Stop escalation is driven independently of writer completion and lock acquisition. Test actual subprocess exit, not merely cancellation of an asyncio waiter. The five-second sync clock starts at native sync entry, not enqueue; queue limits do not introduce a longer sync or shutdown deadline.

## Later running-state full-disk evidence and terminal ownership

The internal storage result distinguishes `no_space`, `quota`, `sync_timeout` and other I/O failure without exposing exception text. Existing public registry refusals stay content-free. A runner-side adapter carries the typed storage cause to its failure controller; parsing the message `registry_unavailable` is forbidden. Every activated failure path has a single transition that closes admission, installs the memory fence before stop, and permanently disables journal appends for that process when required.

Proposed bounded diagnostic portion of terminal state:

| Field | Bound and meaning |
|---|---|
| `format` | Literal `local-journal-failure/1` |
| `cause` | Closed vocabulary `no_space`, `quota`, `sync_timeout`, `io_failure` |
| `readable_next`, `durable_next`, `discarded_tail_bytes` | Existing `Count`; durability never inferred from readability |
| `unwritten` | At most one counter for each activated, closed event kind; no key derived from payload or peer identity |
| Counter value | Existing `Count` plus `saturated` boolean; addition saturates without wrapping or silently claiming an exact total |
| `failed_checkpoint` | Boolean; no checkpoint payload or filesystem path |
| `publication` | Existing publication outcome/reference when the consumer exists; never fabricate `not_dispatched` from missing observation |

Proposed encoded diagnostic portion cap: 8 KiB. It is part of the existing compact 64 KiB head budget, not an additional head exemption. Identity/stop/evidence references use existing typed models and must be budgeted with it. Fixed-kind counter overflow coalesces into one explicit `other_omitted` count, never a map with arbitrary keys. Counts are not closure evidence. No potentially effect-bearing dispatch identity may be dropped to meet a byte cap.

The full terminal tombstone schema cannot be inferred from this diagnostic portion. The running-state implementation plan must provide its exact typed extension to the existing `Retired` head and the authentic receipt scope for its actually observed stop. It may not use `no_runner_started` for a registered runner or claim normal completion after a journal failure. If no honest terminal receipt can yet be produced, recovery remains unresolved; that is a blocker to activating the proposed running-state consumer, not a license to persist a made-up receipt.

The loop holds bounded terminal evidence and owns one retry timer: first retry at one second, exponential backoff capped at 30 seconds. The retry path is independent of the stalled journal writer and merges the current compact head under the existing lock without copying retained history. It does not append to the failed journal or delete user data to free space. Public retire, once implemented, joins the scheduled write rather than starting a competing one. This runner-side controller belongs to #196; #197 owns the separate worker fallback and its own re-read/retry behavior.

## Implementation handoff and decision boundary

The first implementation combines startup consumer, closed startup checkpoint/reference, supervisor journal native durability, typed startup failure, actual writer closure/forced process exit, registry CAS and owned leftover cleanup. A preliminary allocation is 100–160 lines for closed models/reference validation, 180–260 for writer/file operations, 120–180 for lifecycle/fault handling and 140–220 for supervisor/registry/cleanup wiring: 540–820 added/deleted production lines before shared-helper extraction. This is a planning range, not a verified diff; all deleted/refactored lines count too. The implementing child must give a function-level estimate before approval. Preserve the startup reference through takeover, gate changes and subsequent state transitions until an explicit cleanup rule releases it. Forced exit must account for the actual process entry point and its diagnostics shutdown: a daemon writer alone does not prove that exit avoids blocking cleanup. Include both obligations in real subprocess and registry-transition tests.

Recommended immediate decisions: startup-before-registration as the first consumer; the explicit no-write-wording amendment; `local-startup-checkpoint/1`, its 16 KiB checkpoint and startup-line bounds; one startup operation; bounded Running reference and owner-scoped leftover cleanup. These do not approve the provisional later running supervisor queue or complete terminal schema. No release/retirement scope is invented to make a startup-only milestone look complete.

Subsequent C2 work remains explicit: continuation/ownership of the long-lived journal; running supervisor admission; exact full-disk tombstone extension and authentic receipt scope; batch/output/log caps and index/readers; transfer/spools/openat2; command/reaper/executor phases; and grants/adoption/followers. Their actual producer activation carries their own failure, cancellation, stop and release evidence. #197 retains the worker-side fallback and final provider registration. The early diagnostic portion above is a review proposal, not the complete terminal format or permission to activate Running writes.

## Executable evidence required later

- `test_journal_classes_and_caps`: exact ordering/ACK barriers, native sync, torn tail, permanent process-local closure, output/terminal accounting and disabled-index reads as their consumers activate.
- `test_startup_checkpoint_is_crash_consistent`: every row of the crash matrix, new-directory parent sync, uncertain registry commit preserving referenced startup artifacts, closed schema/bounds and foreign/corrupt state refusal using real files and subprocesses.
- `test_journal_writer_releases_resources`: actual ownership after success, cancelled waiters, failure, normal closure and forced subprocess exit with a blocked native operation.
- For later running consumers, `test_full_disk_fence_precedes_stop`: ENOSPC/EDQUOT, no later success after failure, stop independent of writer/lock, bounded terminal retry and no invented completion/receipt.
- Content-free DEBUG diagnostics and positive canary controls for checkpoint/journal/temporaries/terminal state; never use an empty capture as a secrecy proof.
- Reuse the existing Linux 3.11/3.15 and macOS 3.11 lanes and diagnostics framework. Current pressure fixtures provide ENOSPC only; EDQUOT/quota feasibility must be proved before its implementation relies on them. Current standalone diagnostics jobs do not prepare pressure mounts. Any necessary CI preparation amendment receives its own explicit review and retains deadlines.

Numerical time/RSS measurements are diagnostics unless an existing approved method makes them gates. Exact resource closure, integrity, capacity and deadlines remain blocking. No measurements were executed for this proposal.
