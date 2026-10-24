# Covered-call candidate production verification

Application release: `e08dd338ed644894e7db6af1beb19234818488e5`. The user explicitly authorized the public prod push and automatic deployment after the pending TSM-data publication question.

- `source.json`: every running backend's Python src/shared digest matches the application release (12 services).
- `production.json`: 15 healthy containers, prepared/running frontend build identity, HTTP 200 on five public pages and zero observed ledger persistence/capture errors in the checked log window.
- `served.json`: TSM/tracker pages serve the comparison controls, Greek labels and updated tracker entry from the expected build.
- `tsm.json`: deployed calculation helpers reproduced the original target selection and screened alternatives over stored data in a repeatable-read, read-only transaction with a 10-second statement limit and rollback. Source session is October 22; this is not a live quote or an authenticated browser acceptance run.

The dump, credentials and service tokens are not included. Earlier synthetic regression and predeployment evidence remain unchanged in their own directory.
