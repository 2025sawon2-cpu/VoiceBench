# Idle model resource API

`POST /v1/resources/release-idle` uses the same external Bearer authentication as
`/v1/tts` and accepts an empty JSON object. The server returns HTTP 200 with either
`{"status":"released","released":true,"active_jobs":0,"active_requests":0}`
or `status: "busy"`, `released: false`, and the active counts.

The empty-queue check includes **all** queued/running jobs and Studio requests,
including benchmarks and requests still creating their segment jobs. The server
holds `external_request_lock` and a SQLite `BEGIN IMMEDIATE` write reservation
through worker shutdown, preventing enqueue/claim from racing that check. The
transaction is rolled back without modifying data. Only workers owned by this
server's existing worker registry are released; unrelated/reused unowned
processes are untouched.

Engine settings, selected reference, default voice, Seed, HQ policy, recordings,
history, and generated audio are unchanged. The next synthesis lazily reloads
the selected engine through the existing worker lifecycle. This endpoint does
not reserve the GPU for the caller: another client may enqueue after it returns.
Hotpost separately serializes its own local script, visual review, and voice
requests in one process; there is no cross-process/distributed GPU lease.

Both services must load the updated code before release is available. Older
VoiceBench versions return 404; Hotpost treats 404/405/501 and an unavailable
service as optional compatibility cases. No model release happens on import or
through a health check. Regression tests use temporary databases and mock worker
shutdown; they never stop live workers.
