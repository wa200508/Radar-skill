# RF Skill Display v1

An RF skill owns its display semantics. Implement `describe_display(receiver_config)`
in the skill's `display.py`, return that description from `GET /display`, and
include the identical object as `display` in status and every timestamped result.
The dashboard receives JSON only: no skill Python imports, JavaScript, HTML,
expressions or remote assets execute in the simulator or dashboard.

The [JSON schema](schemas/rf-skill-display-v1.schema.json) is identical in all
three repositories. `schema_version: 1` identifies this protocol; `id` names the
skill and `revision` versions its view. A description should remain stable for a
session. Each frame carries its own description, so seeking or switching skills
cannot accidentally use the current live layout for older results.

## Bindings and widgets

Panels have stable IDs, titles, captions and one of these primitives:

| Type | Data | Labels and interpretation |
|---|---|---|
| `metrics` | `items[].value` bindings, optional `valid_when` | Skill-defined labels, units, precision and boolean/empty text |
| `line` / `scatter` | `x.data` and `series` | Axis labels, units, optional ranges; 1D series or 2D series matrix; optional legend |
| `polar` | Angular `x.data` and radial `series` | Degrees, radial range/ticks/units, angular period and legend |
| `heatmap` | `x.data`, `y.data`, 2D `values` | Axes and explicit color quantity/unit/range |
| `waterfall` | Frequency `x.data` and 2D `rows` | Newest window at top, explicit color quantity/unit/range |
| `table` | `rows` binding and `columns[].path` | Skill-defined column labels and per-row values |

Bindings use [JSON Pointer, RFC 6901](https://www.rfc-editor.org/rfc/rfc6901):
`{"path":"/radar/power_db"}` reads the selected acquisition frame.
`{"value":[0,1,2]}` provides literal axis values. `each` projects an array using a
relative pointer, e.g. `{"path":"/waterfall","each":"/site_psd_db/{receiver}"}`.
Selectors declare numeric string values and labels; `{receiver}` is replaced
with the chosen selector value before pointer evaluation. Optional `available`
bindings disable options when no data exists. Missing data is never relabeled as
another receiver. No arithmetic expressions or executable formatters are supported.
If `x.data` is omitted for a line/scatter plot, `x.index_start` supplies ordinal
indices. Automatic bounds apply unless an axis has an explicit two-value range.
Polar plots close only when the supplied angular grid covers a full period;
partial searches remain visibly partial. Empty data yields an awaiting-data view.
Unsupported schema versions or widget types produce a visible diagnostic.

For example, a new communications skill can expose a constellation without any
algorithm-specific dashboard changes:

```json
{
  "schema_version":1,"id":"comms-skill","revision":"1","title":"Comms quality",
  "panels":[{
    "id":"constellation","type":"scatter","title":"Received symbols",
    "x":{"data":{"path":"/symbols/i"},"label":"In-phase","unit":"V"},
    "y":{"label":"Quadrature","unit":"V"},"series":{"path":"/symbols/q"}
  }]
}
```

The corresponding timestamped result contains `symbols.i`, `symbols.q`, and the
same `display` object. Add a new renderer primitive only when the existing
primitives cannot express a view; using a new RF algorithm alone needs no host edit.

## Synchronization and recordings

The browser resolves bindings against the matched frame already selected by the
observer. It commits the skill panels, camera, clock and source telemetry together
after decoding that camera image. Receiver selection redraws only the currently
presented skill frame; it does not advance simulation or playback time.

Descriptions persist in the independent processing-result journal. Replay needs
neither the original skill process/container nor an Internet connection. A skill
may declare `histories`, each with an output `key`, a `limit` of 1..1000 frames,
and a `row` mapping field names to bindings into an individual result. The result
writer omits the duplicated rolling collection; the observer reconstructs those
rows from saved results at or before the selected acquisition timestamp.
Rows from different description revisions/content are never mixed. For example:

```json
{"key":"quality_history","limit":60,"row":{
  "timestamp_ns":{"path":"/timestamp_ns"},"snr_db":{"path":"/snr_db"}
}}
```

A new skill's history may describe any metrics; the observer does not interpret
them as MUSIC, radar or a predefined signal type. The observer retains the neutral
`timestamp_ns` and adds its own simulation-time alias for scene synchronization.

Old recordings without this contract are migrated by frozen legacy descriptions
and result adapters in airsim-rf. These snapshots are for historical playback
only. A live skill without a description receives a missing-description message;
the dashboard does not guess its algorithm from scenario names or result fields.

## Ownership

DF-Skill owns MUSIC/polar search, covariance eigenvalues, relative element phase,
bearing candidates, receiver PSD/waterfall and their sensing limitations.
Radar-skill owns range-Doppler maps, candidate range/range rate, dechirped PSD,
color scales and detector limitations. AirSim-RF owns the generic renderer,
frame/camera synchronization, transport indicators, terrain and source telemetry.
Neither acquisition nor simulation imports a skill's display implementation.


## Matrix orientation and color presets

Heatmap matrices are `values[y_index][x_index]`. Increasing y coordinates run
from bottom to top; increasing x coordinates run left to right. The matrix must
match the supplied axis vectors. Bins use those numeric coordinates, including
when explicit axis ranges clip part of the data.

Optional `color.presets` supplies up to eight `{label, range:[low,high]}` choices.
The first is the default. The dashboard renders a local color-scale selector;
changing it redraws the currently presented frame only and never changes samples,
processing, detection or playback time. The selected preset overrides only the
color range. A display scale cannot recover physical signal buried under noise.
