"""Google Cloud deployment adapters (Firebase Hosting -> Cloud Run API -> Cloud Run Job worker).

Nothing in here is imported by the local server (`quantfolio.serve`). The local
SQLite job store and supervisor stay the default; this package swaps in:

* Firestore for run metadata, ownership, idempotency, the bounded queue, the
  single global lease and compact progress events (`jobstore`);
* a private Cloud Storage bucket for results, counts and uploaded datasets
  (`blobs`, `datasets`);
* no sign-in: per-client limits use a salted hash of the client address
  (`client`); only the scheduled sweep checks a Google OIDC token (`auth`);
* an on-demand Cloud Run Job as the only place heavy work runs (`worker`),
  started by the API through the Cloud Run Admin API (`launcher`).

Google client libraries are imported lazily so the package (and its tests,
which use in-memory fakes) works without them installed.
"""
