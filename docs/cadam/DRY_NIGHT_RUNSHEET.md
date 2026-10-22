# Dry-night runsheet: SolidWorks seat

Before the dry-night preflight, open SolidWorks and use **File → New → Part**
with the installed Part template. Leave that disposable blank Part active in the
same SolidWorks instance used by the bridge. A welcome screen, closed document,
Assembly, or Drawing is not a ready seat. Do not close the Part after the bridge
has bound to it. If the bridge reports a stale binding, have the operator rebind
or restart it against the intended Part before retrying preflight.

Run the usual `arena preflight` with the draft queue, secrets file and paths
from [the nightly task guide](../NIGHTLY_CAD_TASK.md). For the SolidWorks entrant,
GO requires the authenticated ping, MCP launch checks, and a successful
`get_context` (`GET /context`) response with `adapter: "solidworks"`, a nonempty
`document`, and, when supplied, `document_type: "part"`. An unsaved Part is acceptable;
its title alone is not evidence of its type. The probe is read-only and never creates,
saves, activates, or closes a document.

NO-GO prints the bridge's error text, with credentials redacted. In particular,
`SldWorks.Application.ActiveDoc`, a closed bound document, or `SolidWorks does
not expose NewDocument` requires operator/bridge repair. An explicit non-Part
`document_type` is NO-GO. Older bridges omit that field; a successful context
with a nonempty document remains GO with
`WARN: document type unverified (bridge predates hwe#576)`. This compatibility
path cannot verify the document type; the operator must still open a Part.
The typed response is tracked in
[StudioPipeline/hwe #576](https://github.com/StudioPipeline/hwe/issues/576).

The nightly executor repeats the context check immediately before launching the
SolidWorks driver. If the seat was closed after preflight, it records the same
failure reason without spending driver turns. These checks do not prove that a
later modeling or export operation will succeed.
