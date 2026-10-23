# Copyright (c) 2026 wa200508
# SPDX-License-Identifier: Apache-2.0
# Extracted from airsim-rf 3b36fe0; original MIT notice is retained in licenses/.
"""Active FMCW radar skill: command a transceiver and process complex ADC I/Q.

The receiver configuration supplies chirp timing, not targets or environment.
No simulator, propagation backend or source trajectory is imported or opened.
"""
import argparse
from collections import deque
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import signal
import threading
import time
from urllib.parse import urlsplit
import numpy as np
from .sample_stream import iter_stream
from .results import ResultWriter
from .processing import range_doppler
from .transceiver import TransceiverClient, validate_mode



class RadarSkill:
    def __init__(self,arrays,*,results_dir=None):
        self.arrays=json.loads(Path(arrays).read_text());self.acquisition=self.arrays['acquisition']
        if self.acquisition['type']!='fmcw-dechirped':raise ValueError('FMCW dechirped ADC configuration required')
        self.channels=[n for s in self.arrays['sites'] for n in s['channels']]
        if len(self.channels)!=1:raise ValueError('This radar skill expects one complex receiver channel')
        self.lock=threading.Lock();self.started=time.monotonic();self.frames=deque(maxlen=128);self.waterfall=deque(maxlen=60)
        self.results=ResultWriter(results_dir) if results_dir else None
        self.radio=None
        self.state={'status':'starting','ingress_ready':False,'payload_bytes':0,'complex_samples':0,'complete_windows':0,'payload_bytes_per_second':0.}

    def start_transceiver(self,address,mode,*,ready_timeout=600):
        mode=validate_mode(mode,self.arrays)
        self.radio=TransceiverClient(address)
        result=self.radio.start(mode,ready_timeout=ready_timeout)
        with self.lock:self.state['transceiver']={'configuration_id':result['configuration_id'],
            'transmit':result['transmit'],'receive':result['receive'],'requested_mode':mode}
        return result

    def stop_transceiver(self):
        if self.radio is not None:
            result=self.radio.stop()
            with self.lock:self.state.setdefault('transceiver',{}).update(transmit=False,receive=False)
            return result

    def set_transceiver_state(self,state):
        if self.radio is None:raise ValueError('Replay has no transceiver control')
        if set(state)!={'transmit','receive'} or any(type(value) is not bool for value in state.values()):
            raise ValueError('Boolean transmit and receive states required')
        result=self.radio.set_state(**state)
        with self.lock:self.state['transceiver'].update(state)
        return result

    def snapshot(self,epoch=None):
        with self.lock:
            if epoch is not None:return dict(next(f for f in reversed(self.frames) if f['timestamp_ns']==epoch))
            return {**self.state,'available_epochs':[f['timestamp_ns'] for f in self.frames]}

    def process(self,block):
        start=time.monotonic();fs=block.sample_rate_hz
        if (fs,block.carrier_hz)!=(self.arrays['sample_rate_hz'],self.arrays['carrier_hz']):raise ValueError('Receiver sample rate/carrier mismatch')
        rd=range_doppler(block.samples,fs,block.carrier_hz,self.acquisition)
        stride=round(self.acquisition['pri_s']*fs);a=round(self.acquisition['adc_start_s']*fs);n=self.acquisition['num_samples']
        gates=block.samples.reshape(-1,stride)[:64,a:a+n];spectrum=np.fft.fft(gates*np.hanning(n),axis=1)[:,:n//2]
        psd={'frequency_khz':(np.fft.fftfreq(n,1/fs)[:n//2]/1000).tolist(),
             'density_db_v2_hz':(10*np.log10(np.maximum(np.mean(abs(spectrum)**2,axis=0)/(fs*np.sum(np.hanning(n)**2)),1e-30))).tolist()}
        self.waterfall.append({'timestamp_ns':block.timestamp_ns,'psd_db':psd['density_db_v2_hz'],'site_psd_db':[psd['density_db_v2_hz']]})
        now=time.monotonic()
        with self.lock:
            payload=self.state['payload_bytes']+block.samples.nbytes
            self.state.update(status='processing',timestamp_ns=block.timestamp_ns,sample_start=block.sample_start,
                sample_rate_hz=fs,carrier_hz=block.carrier_hz,samples_per_channel=len(block.samples),
                payload_bytes=payload,complex_samples=self.state['complex_samples']+len(block.samples),
                complete_windows=self.state['complete_windows']+1,payload_bytes_per_second=payload/max(now-self.started,.001),
                psd=psd,receiver_spectra=[{'site_id':self.arrays['sites'][0]['id'],'psd':psd}],
                radar=rd,waterfall=list(self.waterfall),processing_latency_ms=(now-start)*1000,
                analysis_duration_s=64*self.acquisition['pri_s'],application='fmcw-radar-rf-skill',algorithm='Hann-windowed range/Doppler FFT with median-noise contrast threshold')
            frame=dict(self.state)
            if self.results:self.results.write(frame)
            self.frames.append(frame)

    def serve_stream(self,port):
        try:
            with socket.socket() as server:
                server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);server.bind(('0.0.0.0',port));server.listen(1)
                with self.lock:self.state.update(status='listening',ingress_ready=True)
                while True:
                    conn,_=server.accept()
                    with conn:
                        conn.settimeout(3600);completed=0
                        def validate():
                            if not completed:raise ValueError('Stream contains no radar windows')
                        for block in iter_stream(conn,validate_end=validate,validate_window=validate):
                            if block.receiver!=self.channels[0]:raise ValueError('Unknown radar receiver channel')
                            self.process(block);completed+=1
                        with self.lock:self.state['status']='complete'
        except Exception as error:
            with self.lock:self.state.update(status='failed',ingress_ready=False,error=str(error))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--arrays',required=True);parser.add_argument('--results-dir')
    parser.add_argument('--mode',help='Hardware-neutral radar waveform, TX power and ADC gate JSON')
    parser.add_argument('--radio-url',help='Physical or simulated transceiver adapter HTTP URL')
    parser.add_argument('--radio-ready-timeout',type=float,default=600)
    parser.add_argument('--replay',action='store_true',help='Process recorded/external I/Q without commanding any transmission')
    parser.add_argument('--iq-port',type=int,default=int(os.environ.get('RF_IQ_PORT','8811')))
    parser.add_argument('--http-port',type=int,default=int(os.environ.get('RF_HTTP_PORT','8812')));args=parser.parse_args()
    if args.replay:
        if args.radio_url or args.mode:parser.error('--replay cannot be combined with radio control')
    elif not args.radio_url or not args.mode:parser.error('Active radar requires --radio-url and --mode; use --replay for recorded I/Q')
    if not 0<args.radio_ready_timeout<=1800:parser.error('radio-ready-timeout must be in (0,1800] seconds')
    skill=RadarSkill(args.arrays,results_dir=args.results_dir)
    def terminate(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,terminate)
    threading.Thread(target=skill.serve_stream,args=(args.iq_port,),daemon=True).start()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if urlsplit(self.path).path!='/transceiver/state':self.send_error(404);return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=1024:raise ValueError('State request must contain 1..1024 bytes')
                self.connection.settimeout(5)
                result=skill.set_transceiver_state(json.loads(self.rfile.read(length)))
            except (ValueError,KeyError,TypeError) as error:self.send_error(400,str(error));return
            except OSError as error:self.send_error(502,str(error));return
            data=json.dumps(result,allow_nan=False).encode();self.send_response(200)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data)
        def do_GET(self):
            url=urlsplit(self.path).path
            try:
                if url in ('/health','/status'):state=skill.snapshot()
                elif url.startswith('/frames/'):state=skill.snapshot(int(url.rsplit('/',1)[-1]))
                elif url.startswith('/epochs/'):state={k:v for k,v in skill.snapshot(int(url.rsplit('/',1)[-1])).items() if k in ('timestamp_ns','complete_windows')}
                else:self.send_error(404);return
            except (ValueError,StopIteration):self.send_error(404);return
            data=json.dumps(state,allow_nan=False).encode()
            self.send_response(503 if url=='/health' and (not state['ingress_ready'] or state['status']=='failed') else 200)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def log_message(self,*args):pass
    try:
        if not args.replay:skill.start_transceiver(args.radio_url,json.loads(Path(args.mode).read_text()),ready_timeout=args.radio_ready_timeout)
        ThreadingHTTPServer(('0.0.0.0',args.http_port),Handler).serve_forever()
    except KeyboardInterrupt:pass
    finally:
        # Radio stop is independent of the stream/journal shutdown. Physical
        # adapters must also implement their own disconnect/watchdog interlock.
        if skill.radio is not None:
            try:skill.stop_transceiver()
            except OSError:pass


if __name__=='__main__':main()
