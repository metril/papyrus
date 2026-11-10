# eSCL (network scanning)

Papyrus acts as an eSCL (AirScan/Mopria) scanner server and advertises itself over mDNS
(`_uscan._tcp`). It lives at `/eSCL` on the server root (not under `/api/v1`) and is not in the
OpenAPI document. Standard eSCL operations: `GET /eSCL/ScannerCapabilities`, `GET /eSCL/ScannerStatus`,
`POST /eSCL/ScanJobs`, `GET /eSCL/ScanJobs/{id}/NextDocument`, `DELETE /eSCL/ScanJobs/{id}`.
Finished scans appear in the scan history like any other (and are broadcast on the `scans` channel).

## Access rule

There is no authentication (real eSCL clients send none), so every route requires a LAN client:

- the TCP peer must be a private, loopback or link-local address;
- only then, if `X-Forwarded-For` is present, its **rightmost** entry is evaluated instead (the hop
  appended by the trusted same-host proxy); that address must be LAN too;
- a non-LAN peer or XFF entry, or unparseable/missing input, gets `403` (fail closed).

## Feeder behaviour (`brothernet:` devices, DCP-L2540DW)

- The hardware pulls every sheet in the feeder at once and cannot be stopped mid-feed.
- Platen requests with paper in the feeder are taken over by the feeder (a full-page glass request
  feeds all sheets). A JPEG/PNG platen job with paper becomes a held-pages job; a PDF one a single
  document. If the printer can't be read, the scan decides at run time and the job becomes a PDF.
- A Feeder request with an empty feeder returns `409` before a job is created.
- JPEG/PNG feeder jobs serve pages one by one through `NextDocument` (served pages are deleted; `404`
  when exhausted; `503` while still scanning; `410` after a failed job). `ScannerStatus` shows
  `Processing` with `ImagesCompleted`/`ImagesToTransfer` until the last page is served.
- `ScannerStatus` includes `AdfState` (`ScannerAdfLoaded`/`ScannerAdfEmpty`) from a 10 s cache when
  known; capabilities add `FeederCapacity` 35 (and `AdfOption` `DetectPaperLoaded` on native
  devices).
- The stored scan row is always the merged PDF with the correct page count.
