# Split monitor runner

The split workflow uses two execution environments:

1. `acquire` runs on an Ubuntu/Linux self-hosted runner labeled
   `transit-monitor-linux`. It retrieves agency pages from the approved
   network and uploads only the normalized source snapshot.
2. `publish` runs on a GitHub-hosted Ubuntu runner. It downloads the snapshot,
   downloads the canonical workbook, validates and publishes the workbook to
   OneDrive, and uploads publication evidence.

To configure the acquisition runner, use **Repository Settings → Actions →
Runners → New self-hosted runner**, select Linux x64, install Python 3.12 and
Chromium dependencies, and register it with the label `transit-monitor-linux`.
Run it as a service on an always-on machine. Do not use this runner for
untrusted pull-request workflows; it executes repository code on the local
network.

The original workflow is now manual-only so it cannot duplicate the scheduled
split workflow. The first split run should be manually dispatched and checked
for source rows and the publication artifact before relying on the schedule.
