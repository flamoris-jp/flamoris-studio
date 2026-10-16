# FLAMORIS Studio 1.0.10

Speech output length now starts blank (Automatic). A blank duration requests Irodori's estimated length; explicit numbers keep fixed duration. Matches Controller 1.0.7 / Generation MCP 1.0.10's exact nullable-duration contract, including reference speech. Existing accounts, jobs, numeric request snapshots, reference inputs and unknown/no-replay fences stay intact; no DB schema or runtime configuration changes.

Updater compatibility includes 1.0.9. Source/CI checks and production output quality are separate.
