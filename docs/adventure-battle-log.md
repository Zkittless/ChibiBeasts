# Adventure and Battle log

Adventure now shows the current destination, a compact team summary, the scene and its next choices. Returning home leaves one short notice. Detailed results live behind **Battle log** within the same Field Journal message.

The log lists three trips per page, newest first. Select a trip for its encounter outcome, expedition notes, rewards, catches and team EXP. In-progress trips clearly mark rewards and catches as pending return. **All trips** returns to the same history page; **Back to Adventure** resumes the current route.

History reads the existing owner-scoped `expeditions` records, including older trips. It needs no schema migration. The controls use the journal's existing acknowledgement, ownership, expiry and input-order guards. Browsing the log changes no trip revision, charge, destination preset, team or reward.

`tests/test_journal_log.py` covers history pagination, ownership, deleted entries, pending rewards, Discord limits and returning to the original choices without mutation. The existing journal and exploration suites cover dispatch, battle return and destination gates.

To export actual Discord component payloads and test the offline preview:

```powershell
python scripts/preview_journal_flow.py --output docs/adventure-log-preview --capture
```

The exporter uses a disposable example database and packaged artwork. Browser capture requires Playwright and Chrome. It checks desktop, mobile and narrow layouts, log navigation, portraits, team editors and the encounter return flow.
