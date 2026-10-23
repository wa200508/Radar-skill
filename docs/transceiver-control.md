# Transceiver adapter contract v1

The radar skill is the controller. An adapter implements these endpoints for a
physical or simulated radio, and independently sends complex dechirped ADC volts
using receiver TCP v2. No endpoint contains scene geometry, flight, target RCS,
range, velocity or detector results. AirSim implements the adapter without
importing this skill or knowing its detection algorithm.

## Configure

PUT /v1/configuration accepts exactly:

```json
{
  "schema_version": 1,
  "waveform": {
    "type": "fmcw-linear-up",
    "carrier_hz": 77000000000.0,
    "bandwidth_hz": 50000000.0,
    "chirp_duration_s": 0.000032,
    "pri_s": 0.000040,
    "power_dbm": 12.0
  },
  "receiver": {
    "channel": "radar_rx0",
    "sample_rate_hz": 10000000.0,
    "num_samples": 256,
    "adc_start_s": 0.000003,
    "gain_db": 60.0
  }
}
```

The linear sweep runs from carrier minus half the bandwidth to carrier plus
half the bandwidth over chirp_duration_s; the ramp restarts every PRI. Carrier
is the sweep center. RF hardware synthesizes this ramp. The complex dechirped
ADC mixer convention is TX times conjugate(RX). Its gate starts relative to
each ramp; gate gaps in the full-rate sample stream are zero-filled. All chirps
retain their actual acquisition timestamps and slow-time phase.

Power is conducted RF power in dBm before antenna/feed effects. gain_db is
receiver IF voltage gain in dB. These are requested electrical settings, not
radiated EIRP or calibrated detection performance. An adapter must reject an
unsupported setting rather than silently choose another waveform, power or gate.
The current adapter models 76..81 GHz, <=4 GHz sweeps, <=25 MS/s, -60..12 dBm
conducted TX, 0..100 dB IF gain, and its configured IF/ADC hardware constraints.
The wide lower TX/gain ranges are adapter modeling bounds, not a claim that a
particular TI firmware exposes every such value.

Return JSON with configuration_id: the lowercase SHA-256 of the received
configuration serialized using sorted keys, compact comma/colon separators,
finite JSON numbers, preserving integer versus floating-point JSON types.
The skill sends Python's canonical JSON representation and checks the identity;
an adapter can compute it from parsed JSON using the same reference algorithm
in transceiver.configuration_id. This first version requires matching numeric
serialization; a future cross-language adapter must implement that convention.
Repeated identical configuration requests are idempotent. The current acquisition
adapter rejects changed configuration after the first captured window: start a
new session with matching receiver/recording metadata to retune or change ramps.

## Enable, mute and stop

POST /v1/state accepts exactly boolean transmit and receive flags:

```json
{"transmit": true, "receive": true}
```

The response includes configuration_id and the accepted transmit/receive flags.
A fresh adapter starts with both disabled; enabling either before successful
configuration is an error. TX false/RX true means receive without transmitted
radar echoes (noise and ADC effects continue). Both false stops acquisition.
The skill requests both true after configuration and both false on normal exit.
Replay mode sends no control traffic. External callers can use the skill's
POST /transceiver/state endpoint to issue these state requests through the skill.

Control acknowledgements report requested state, not instantaneous radiated
power. Settings apply atomically at the next acquisition-window boundary; an
in-flight window completes under its previous settings. GET /v1/status reports
configuration_id, transmit, receive, applied_configuration_id,
applied_timestamp_ns, applied_transmit and application_boundary. The adapter
updates applied fields when it begins a window. The simulator pauses further
acquisition/physics advancement while receive is disabled. This initial monostatic
adapter does not model TX-only illumination into other receivers.

Malformed/unsupported requests return HTTP 400 and leave prior valid settings
unchanged. The API currently targets a trusted radio-control network; it has no
authentication, capability negotiation or native AMS-GRA MEL binding. Hardware
adapters own RF limits, watchdog/interlock handling and calibration. This repo
does not supply a TI serial/DCA1000 driver.
