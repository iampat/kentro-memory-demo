## Ticket #157 – API Timeout Errors on Data Sync Endpoint

**Company:** Acme Corp
**Date:** 2026-04-21
**Status:** Open
**Severity:** High
**Subject:** API Timeout Errors on Data Sync Endpoint

---

**Description:**

Acme Corp is reporting repeated timeout errors when calling the `/api/v2/sync/data` endpoint during their nightly batch processing jobs. The requests consistently fail after approximately 30 seconds, preventing critical data from syncing to their internal systems. This issue began occurring on 2026-04-19 and has affected every scheduled sync since then. No changes were made to their integration configuration prior to the failures.

The customer has attempted reducing batch sizes and staggering request intervals without success. This is severely impacting their operations, as downstream reporting dashboards are displaying stale data. Immediate investigation into server-side timeout thresholds and endpoint performance is requested.
