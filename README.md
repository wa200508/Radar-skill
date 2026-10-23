# Radar RF Skill

An independent **active FMCW radar skill**: it chooses the transmit waveform,
programs TX power and coherent chirp timing, configures the complex ADC receive
gate and gain, enables TX/RX, and processes the returned I/Q into range/Doppler
candidates. It has no simulator, propagation, terrain, trajectory, RCS or target
truth dependency. NumPy is its only runtime dependency.

The same hardware-neutral adapter API applies to simulated and physical radios.
The skill programs an RF chirp synthesizer; it does not try to sample a 77 GHz
carrier or a 50 MHz sweep using the 10 MS/s dechirped ADC stream.

## Run an active radar

For the complete simulated radio and dashboard, select the radar scenario in
[airsim-rf](https://github.com/wa200508/airsim-rf):

```bash
bash scripts/run_dashboard.sh --scenario mmwave-radar --open
```

For development against a compatible radio adapter:

```bash
git clone https://github.com/wa200508/Radar-skill.git
cd Radar-skill
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
radar-skill --arrays examples/receiver-config.json   --mode examples/radar-mode.json --radio-url http://RADIO_ADAPTER:8814   --results-dir results
```

Replace RADIO_ADAPTER with the adapter's reachable address. The adapter must
implement [the transceiver contract](docs/transceiver-control.md) and send
framed TCP v2 ADC I/Q to this service's port 8811. The sample mode requests
77 GHz, a 50 MHz/32 us up-chirp, 40 us PRI, 12 dBm TX, and a 10 MS/s receive
gate of 256 complex samples beginning 3 us into each chirp, at 60 dB IF gain.
Those are explicit demonstration settings within the modeled IWR1843 limits,
not measured board calibration or vendor firmware defaults.

This repository supplies the skill and protocol, not a TI/DCA1000 hardware
driver. Physical radios need a compatible adapter that programs their hardware
and preserves the declared I/Q convention, gate timing and voltage calibration.

The mode lives in `examples/radar-mode.json` for standalone development and in
`scenarios/mmwave-radar/radar-mode.json` in airsim-rf. The skill sends the mode
using PUT /v1/configuration, checks its identity, then POST /v1/state enables
both transmit and receive. Adapter acknowledgements mean command acceptance;
application occurs at an acquisition-window boundary. Status reports the applied
configuration and timestamp. Unsupported settings fail without fallback.

The skill's HTTP port 8812 provides /health, /status, /frames/<timestamp_ns>
and /epochs/<timestamp_ns>. POST /transceiver/state forwards boolean transmit
and receive flags to the radio. Muting transmit while keeping receive enabled
allows noise-only acquisition. Normal termination requests TX and RX off.
Adapters remain responsible for hardware watchdogs and disconnect handling.

## Public container

The public immutable image is selected by
[airsim-rf's scenario and release pins](https://github.com/wa200508/airsim-rf/blob/main/docs/radar-skill.md).
Its release workflow builds this repository's own Dockerfile at an exact source
commit and verifies a fresh anonymous pull followed by offline qualification.
It is hosted under the existing public airsim-rf GHCR package because the separate
radar-skill GHCR package rejects anonymous pulls. This hosting location introduces
no AirSim runtime dependency. Source CI builds/tests its current checkout;
the pinned released image is qualified separately.

An active container needs receiver-config.json, radar-mode.json, a writable
results directory and a reachable radio adapter. Its default command targets
http://rf-acquisition:8814 on a shared Docker network, matching the AirSim launcher.
No GPU, Docker socket or privileged mode is required by the skill. For rootful
Docker, use --user "$(id -u):$(id -g)" with a host-owned results directory; for
rootless Docker use --user 0:0 so its mapped UID owns that directory.

## Recorded I/Q replay (no transmission)

```bash
radar-skill --replay --arrays /path/to/iq/receiver-config.json --results-dir replay-results
# In another terminal:
python -m radar_skill.sample_recording inspect /path/to/iq --verify
python -m radar_skill.sample_recording replay /path/to/iq --host 127.0.0.1 --port 8811
```

Replay is explicit: --replay rejects --radio-url and --mode and never opens a
radio-control connection. The archive contains native GNU Radio gr_complex
cf32 little-endian *.sigmf-data, metadata and windows.jsonl. Acquisition time,
channel identity, phase and volts survive replay exactly. Gate gaps must not be
compressed. Replay runs as fast as acknowledged; --speed 1 paces original time.
Use a fresh consumer and output directory per replay.

## Processing and ownership

- [transceiver.py](src/radar_skill/transceiver.py): mode validation, waveform and
  receive settings, configure/start/stop radio client.
- [app.py](src/radar_skill/app.py): active skill lifecycle, received I/Q validation,
  status, and processing-result journal.
- [processing.py](src/radar_skill/processing.py): first 64 complete chirps per
  window, per-chirp DC removal, Hann windows, 2D FFT and positive range beats.
- [sample_stream.py](src/radar_skill/sample_stream.py): acknowledged TCP v2
  complex ADC transport; ACK only follows successful processing and journaling.
- [sample_recording.py](src/radar_skill/sample_recording.py): neutral lossless
  recording/replay, also usable by GNU Radio developers.

The detector reports one strongest candidate above 18 dB contrast relative to
median map power. This is a teaching detector, not calibrated CFAR, MIMO angle
estimation or target tracking. Its mixer convention is TX times conjugate(RX):
positive reported range rate means receding. Peak range is corrected for
range/Doppler coupling; the displayed range map retains coupling. Doppler
comes from measured slow-time phase, never supplied target velocity.
The receiver currently expects one channel, at least 64 complete chirps/window,
and a fixed carrier, sample rate and waveform/gate configuration per session.
Changing that configuration requires a new stream session and matching metadata.

This HTTP control contract and receiver TCP v2 data plane do not claim native
AMS-GRA MEL/VITA or stock SQUALL UDP compatibility.

## Qualification

```bash
python -m pytest -q
docker build -t radar-skill:test .
docker run --rm --network none --entrypoint python radar-skill:test scripts/qualify_independence.py
```

The standalone qualifier requires AirSim, Sionna, CUDA and DF-Skill to be
physically absent. It commands a neutral instrument harness over actual HTTP,
verifies configure/start/stop, receives I/Q over real TCP, checks detection and
result files, records exactly and replays through a fresh unchanged consumer.
Replay must produce identical radar results without issuing radio commands.
The AirSim cross-repository tests separately exercise the actual Sionna model,
including commanded TX power, TX-off suppression and a nondefault sweep.
These CPU checks do not assert native GPU flight performance.

## License

Apache-2.0, matching DF-Skill and the public AMS-GRA starter kit RF example.
The original airsim-rf MIT notice is retained in [licenses/airsim-rf-MIT.txt](licenses/airsim-rf-MIT.txt)
and [NOTICE](NOTICE).


## Skill-owned dashboard display

[display.py](src/radar_skill/display.py)
owns the plots, labels, units, scales, receiver selection and result bindings.
`GET /display` serves the description; status and timestamped results include it
as `display`. The dashboard renders those primitives without knowing which RF
algorithm produced them. See [the display contract and extension guide](docs/skill-display.md).
Saved results carry their view description for offline synchronized playback.
