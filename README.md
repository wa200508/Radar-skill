# Radar RF Skill

Independent FMCW radar processing from timestamped complex ADC I/Q. This is the
receiver skill extracted from [airsim-rf](https://github.com/wa200508/airsim-rf),
packaged separately like [DF-Skill](https://github.com/wa200508/DF-Skill).
It accepts live receiver adapters and recorded streams through the same interface.
There is no simulator, propagation, scene, platform trajectory or target model
in this repository or its container. NumPy is its only runtime dependency.

## Run the container

```bash
docker pull ghcr.io/wa200508/radar-skill:latest
mkdir -p results
# Rootful Docker: match the writable output directory's host owner.
docker run --rm --user "$(id -u):$(id -g)" \
  -p 127.0.0.1:8811:8811 -p 127.0.0.1:8812:8812 \
  -v "$PWD/receiver-config.json:/config/arrays.json:ro" \
  -v "$PWD/results:/results" \
  ghcr.io/wa200508/radar-skill:latest
```

For **rootless Docker**, use `--user 0:0` so its mapped UID owns a directory
created by your login user. For rootful Docker, use your host UID/GID as above.
No GPU, Docker socket, privileged mode or host network is required. HTTP status
is available at `http://localhost:8812/status`; I/Q ingress is TCP 8811.
Use the provided [receiver configuration example](examples/receiver-config.json),
or the receiver-only configuration alongside an airsim-rf I/Q recording.
The AirSim dashboard selects an immutable `:sha-<commit>` image automatically.

## Develop without containers

```bash
git clone https://github.com/wa200508/Radar-Skill.git
cd Radar-Skill
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
radar-skill --arrays examples/receiver-config.json --results-dir results
```

Start the service with an adapter sending the framed TCP v2 complex-voltage
contract. Acquiring, tuning or steering hardware remains the receiver adapter's
job. The skill cannot control a transmitter, receiver or simulation.

## What it computes

[processing.py](src/radar_skill/processing.py) extracts the first 64 complete
chirps from each acquisition window, removes per-ramp DC, applies Hann windows
and a 2D FFT, and searches positive range beats. A candidate must exceed the
median map power by 18 dB. This is a teaching detector; it does not claim a
calibrated CFAR false-alarm probability. It currently reports one strongest
candidate, rather than target tracks, MIMO angles or classified targets.

Its mixer convention is **TX × conjugate(RX)**: positive reported range rate
means receding, negative means approaching. The peak range is corrected for
FMCW range/Doppler coupling; the displayed map's range axis retains coupling.
Doppler comes from received slow-time phase, never a supplied target velocity.
Hardware using the opposite I/Q convention needs a deliberate adapter conversion.

The receiver configuration supplies carrier/sample rate, channel identity,
sweep bandwidth/duration, PRI, ADC gate offset and sample count. It describes
**dechirped complex ADC volts**, with zero-filled gate gaps preserving original
time. It contains no target position, RCS, flight plan, source waveform buffer
or terrain. A hardware adapter must preserve the same chirp/gate timing.

[app.py](src/radar_skill/app.py) performs ingress validation and result
journaling. [sample_stream.py](src/radar_skill/sample_stream.py) defines the
versioned cf32 little-endian TCP protocol. A window ACK is sent only after
processing and journaling succeed. The current application expects one receiver
channel, complete chirps, at least 64 chirps per window, and a fixed carrier/rate.

## Recordings, GNU Radio and replay

The neutral archive stores native GNU Radio `gr_complex` files as per-channel
`*.sigmf-data`, with SigMF metadata, `receiver-config.json` and a coherent
`windows.jsonl` journal. Stock `.grc` viewers/replay graphs accompany recordings.
Acquisition time, channel order, amplitude and phase survive replay exactly.
ADC gate gaps remain in the stream; they must not be compressed away.

Run the consumer using the archive's configuration, then replay in another shell:

```bash
radar-skill --arrays /path/to/iq/receiver-config.json --results-dir results
python -m radar_skill.sample_recording inspect /path/to/iq --verify
python -m radar_skill.sample_recording replay /path/to/iq --host 127.0.0.1 --port 8811
```

Replay is as fast as acknowledged by default; use `--speed 1` to pace original
acquisition time. A new replay should use a fresh consumer process and output
directory. GNU Radio developers can connect their processing directly to the
stock complex File Source outputs; no simulation rerun or file conversion is needed.

## Service contract

- `RF_IQ_PORT` / `--iq-port`: input TCP port, default 8811.
- `RF_HTTP_PORT` / `--http-port`: observer HTTP port, default 8812.
- `--arrays`: receiver-only JSON configuration path.
- `--results-dir`: writable journal of `<timestamp_ns>.json` results.
- HTTP: `/health`, `/status`, `/frames/<timestamp_ns>`, `/epochs/<timestamp_ns>`.
- Results include neutral acquisition `timestamp_ns`, transport counters,
  receiver PSD/waterfall, processing latency and `radar` range/range-rate map.

This is the existing airsim-rf receiver-TCP-v2 contract. It does not claim native
AMS-GRA MEL/VITA or bare SQUALL UDP compatibility. Unlike DF-Skill, this initial
radar consumer has no gRPC control endpoint; scenario control port is `null`.

## Verification and publishing

```bash
docker build -t radar-skill:test .
docker run --rm --network none --entrypoint python radar-skill:test scripts/qualify_independence.py
python -m pytest -q
```

The standalone qualifier requires AirSim, Sionna, CUDA and DF-Skill packages to
be **physically absent**. It exercises real TCP/HTTP and result files, verifies
lossless sample recording, then replays the exact data through a fresh unchanged
consumer and requires identical radar results. Run it in the container or a
clean NumPy-only environment, not an environment also containing airsim-rf.
CI repeats these checks and publishes `ghcr.io/wa200508/radar-skill:latest` and
immutable `:sha-<full commit>` images, then checks anonymous registry pulls.

## License and attribution

Apache-2.0, matching DF-Skill and the public AMS-GRA Hello World Starter Kit RF
skill's license selection. The original airsim-rf MIT notice is preserved in
[licenses/airsim-rf-MIT.txt](licenses/airsim-rf-MIT.txt); see [NOTICE](NOTICE).
This does not assert U.S. Government authorship or change NumPy's license.
