# SoilGrids REST status — checked 17 November 2026

REST is responding to live queries, including a complete validated profile at
one comparison point. It is still a beta service without an uptime commitment.
Austin's incomplete response is a location-specific data gap, not evidence of
a general outage. ISRIC's public pause announcement and observed responses
currently disagree; neither a working documentation page nor one successful
query establishes reliable availability everywhere.

## What the linked documentation specifies

The [Swagger documentation](https://rest.isric.org/soilgrids/v2.0/docs) and its
[OpenAPI schema](https://rest.isric.org/soilgrids/v2.0/openapi.json) describe API
version 2.0, with `/properties/query`, `/properties/layers`, and
`/classification/query` routes. The property query takes one latitude/longitude
and array-valued `property`, `depth`, and `value` parameters. EMS requests its six
required properties, six depth intervals and mean predictions together in one
request. The schema also lists other properties/statistics that EMS does not
currently use. Metadata and documentation availability do not verify the raster
query service.

The [REST entry page](https://rest.isric.org/) states a fair-use limit of five
calls per minute and warns that uptime is not guaranteed. The
[SoilGrids access notice](https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_02.html)
and [main product page](https://isric.org/explore/soilgrids/) still describe REST
as temporarily paused, with no restoration date. No restoration announcement
was found in the official pages checked. The notice may lag actual operation;
that is an inference, not a confirmed ISRIC status update.

## Live observations

The OpenAPI schema returned HTTP 200 in 0.46 seconds. Separate full-profile
queries, checked by EMS's existing coordinate/unit/depth/physical validation,
produced:

| Location | Result | Foreground time |
|---|---|---:|
| Austin, 30.2672, -97.7431 | REST responded, but mean bulk density at 0–5 cm was missing; profile rejected | 13.1 s |
| Comparison point, 42.0, -93.7 | Complete valid six-depth profile | 15.4 s |

These are point-in-time measurements, not an uptime survey or accuracy
assessment. Evidence is recorded in
`verification/soilgrids_rest_status_2026-11-17.json` and the captured schema in
`verification/soilgrids_openapi_snapshot.json`.

## How EMS handles it

Automatic REST access is enabled by default. When a fresh SoilGrids lookup is
needed, EMS attempts the batched request with a 20-second foreground limit,
then uses validated numeric WMS/WCS backups. A transient provider failure creates
a shared five-minute cooldown; later lookups retry REST automatically after it
expires. A missing profile creates only a short cooldown for that location.
The shared request-start budget still enforces five REST calls per minute.

Compatible saved profiles are reused for up to 30 days, so an already valid
profile does not need to be downloaded again just because a different access
route becomes available. HWSD priority remains a separate user choice. If no
mapped profile passes validation, EMS pauses before any assumed-soil run.
