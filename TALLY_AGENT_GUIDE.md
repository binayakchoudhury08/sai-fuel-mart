# 3.28.7 update

- `tally check 2/11/2026` or `tally daybook 2/11/2026`: read-only Day Book screenshot for the selected company and date. Supply the PIN/company when requested. This does not import or modify vouchers.
- `tally ledger svt nov 2026 pdf`: SVT ledger for 1–30 November 2026. The agent verifies company, ledger and period on the report and in the downloaded PDF before sending.
- Folder selection now explicitly opens Specify Path, clears the folder field and verifies it closed on the intended month before entering the filename. It never falls back to October for a November file.
- An import result is followed by a separate Day Book screenshot. Visible rows alone are not a full reconciliation; an empty/filtered report is not treated as proof that no import ever occurred. Compare voucher numbers and amounts before any retry.
- Previous import dialogs are dismissed with Escape before entering a company name.
- PDF export configuration uses visible setting labels; if the installed Tally version has a different layout, the agent stops with a screenshot instead of sending a stale/wrong PDF.
- PDF verification installs pypdf 5.1.0 on the agent runner if missing.
- 40 offline regression tests passed, including period parsing, folder rejection, read-only report commands, PDF content identity, and failed document delivery. No live Day Book or SVT PDF was retrieved during code preparation.

References: https://help.tallysolutions.com/day-book-tally/ and https://help.tallysolutions.com/export-data-in-tally/ .

# Tally agent repair — 3.28.6

## What the supplied screenshot proves
The import form and its File Formats selector are still open. The old green success caption is not evidence that vouchers were imported. Check Day Book for 2 November 2026 and 15 October 2026 before attempting the same dates again. This patch does not change existing Tally vouchers or relabel past jobs.

## Deploy together
Deploy this full project to the existing repository and website. The website endpoints and agent must be updated together. Let any existing Tally workflow finish or cancel it before updating; do not rerun an old workflow job. No database schema migration is needed.

The existing workflow installs Playwright and Requests. The agent checks for Pillow and Tesseract and installs missing dependencies on the GitHub-hosted runner. Workflow files are unchanged because the publishing credential lacks workflow scope. Existing secrets remain required:
- GitHub Actions: SFM_SITE, SFM_SECRET, TALLY_EMAIL, TALLY_PASSWORD.
- Website: CRON_SECRET (same as SFM_SECRET), GITHUB_TOKEN, GITHUB_REPO, and the existing WhatsApp configuration.
- GITHUB_REF defaults to main.

Replace the GitHub token exposed in chat. Never commit credentials. Live Tally imports were not performed while preparing this patch. Publishing code alone does not import vouchers.

## Use from WhatsApp
1. Send `tally import 2/11` (only after checking the previous attempt).
2. Supply the secure PIN, then the company name when asked.
3. Inspect the screenshot: company, full folder path and exact filename must be correct. Each filename now has a unique `_job123` suffix to prevent reusing an old upload.
4. Reply `IMPORT` to submit the displayed file or `STOP` to cancel. The agent rechecks the form after approval.
5. Success requires a result screenshot with created + altered equal to the expected voucher count, errors = 0, and ignored = 0. The import form itself never qualifies.
6. Send `tally status` if a message is missing. This reports stored status, not a new inspection of Tally.

`review_required` means Tally may already have imported some or all vouchers. Check Day Book before retrying. The existing `tally import again …` override remains explicit; it can cause duplicates. There is no automatic retry of the import keystroke.

## Fixes
- OCR checks for the company, full filename, path, and import result; failed or ambiguous readings stop the flow.
- Pre-import screenshot and IMPORT/STOP confirmation; failed screenshot delivery blocks progression. The provider acknowledging a message is not proof it reached the handset.
- Strict media delivery is used for agent screenshots. Twilio's current text-only media fallback cannot approve an import; this agent path needs the existing Meta image capability.
- Validated XML is frozen per job and hash checked; workbook changes during login cannot replace the queued content. Conversion runs once instead of twice.
- Unique filenames, correct XML selector text, replacement rather than appended field text, and no fallback to an unrelated month folder.
- Job claims block workflow replays; shared-desktop job creation is serialized. Uncertain submissions retain duplicate warnings.
- Popup handling no longer sends blind Enter presses. An already open matching company is reused; if a different company is open, select the intended one manually and restart.
- Persistent HTTP connection, two-second answer polling, and removal of the remote-desktop network-idle wait. No measured end-to-end speed claim; GitHub queue/cold start and cloud startup still affect duration.
- Accounting calculations, converter reconciliation, existing adjustments, website layout, and ledger export commands are retained.

Workflow caching and automated regression runs are not enabled in this deployment. The regression suite runs locally without production credentials or import submissions.

## Verification performed
`python -m pytest -q tests/unit/test_agent_safety.py`: 26 passed.
`python -m compileall -q sfm agent tests`: passed.
Tests include real Tesseract execution against a generated result image, mocked remote keyboard/approval flows, failure states, API authentication, immutable XML, and strict image sending.

The older database integration script `tests/test_tally_jobs.py` was updated to the new protocol but was not run: the required test database/workbook fixture was not supplied. Production WhatsApp delivery, the live file manager, actual Tally field navigation, and live result-screen OCR remain unverified. The screenshot is too small to establish those behaviours. OCR can reject a valid but unreadable screen; it intentionally does not turn ambiguous characters into a success claim.

For a first live check, use a backed-up/test company and a previously unimported date. Confirm the exact file and company before approving, then compare the result to Day Book. No credentials, PINs or account data are included in the new tests.
