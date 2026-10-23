"""Real TCP/HTTP acquisition and exact recording replay with no source installed."""
import importlib.util
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import threading
from urllib.request import build_opener,ProxyHandler
import numpy as np
from radar_skill.sample_stream import SampleBlock,SampleStreamWriter
from radar_skill.sample_recording import SampleRecordingWriter,iter_recorded_windows,replay
from radar_skill.transceiver import configuration_id


def fixture(config,*,distance_m=123.,velocity_m_s=-12.,epoch=0):
    p=config['acquisition'];fs=config['sample_rate_hz'];fc=config['carrier_hz'];c=299792458.
    stride=round(p['pri_s']*fs);start=round(p['adc_start_s']*fs);fd=2*velocity_m_s*fc/c
    fast=p['adc_start_s']+np.arange(p['num_samples'])/fs;slow=np.arange(100)*p['pri_s']+epoch*1e-9
    cube=np.zeros((100,stride),np.complex64)
    cube[:,start:start+len(fast)]=.02*np.exp(2j*np.pi*((2*distance_m*p['bandwidth_hz']/p['chirp_duration_s']/c+fd)*fast[None,:]+fd*slow[:,None]))
    return cube.ravel()


def main():
    for name in ('airsim_rf','sionna','projectairsim','mitsuba','drjit','cupy','df_skill'):
        assert importlib.util.find_spec(name) is None,'Unexpected source/application dependency: '+name
    client=build_opener(ProxyHandler({}))
    def port():
        with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
    def get(http,path):
        with client.open(f'http://127.0.0.1:{http}'+path,timeout=2) as response:return json.load(response)
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary);config=json.loads((Path(__file__).resolve().parents[1]/'examples/receiver-config.json').read_text())
        mode=json.loads((Path(__file__).resolve().parents[1]/'examples/radar-mode.json').read_text())
        mode_path=root/'mode.json';mode_path.write_text(json.dumps(mode));commands=[]
        # A neutral instrument harness, deliberately without a simulator SDK.
        class Radio(BaseHTTPRequestHandler):
            def command(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                commands.append((self.path,payload))
                result={'configuration_id':configuration_id(mode)}
                if self.path=='/v1/configuration':assert payload==mode
                else:result.update(payload)
                data=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)))
                self.end_headers();self.wfile.write(data)
            do_PUT=command
            do_POST=command
            def log_message(self,*_):pass
        radio=ThreadingHTTPServer(('127.0.0.1',0),Radio)
        threading.Thread(target=radio.serve_forever,daemon=True).start()
        path=root/'receiver.json';path.write_text(json.dumps(config));processes=[]
        def start(name,*,active=False):
            iq,http=port(),port()
            while http==iq:http=port()
            command=[sys.executable,'-m','radar_skill','--arrays',str(path),'--results-dir',str(root/name),
                '--iq-port',str(iq),'--http-port',str(http)]
            command+=['--radio-url',f'http://127.0.0.1:{radio.server_port}','--mode',str(mode_path)] if active else ['--replay']
            process=subprocess.Popen(command)
            processes.append(process);deadline=time.monotonic()+15
            while True:
                try:
                    if get(http,'/health')['ingress_ready']:return iq,http
                except OSError:pass
                if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Radar service failed startup')
                time.sleep(.02)
        try:
            iq,http=start('live',active=True);saved=[];recording=SampleRecordingWriter(root/'iq',config)
            assert commands==[('/v1/configuration',mode),('/v1/state',{'transmit':True,'receive':True})]
            try:
                with socket.create_connection(('127.0.0.1',iq),timeout=10) as connection:
                    writer=SampleStreamWriter(connection,carrier_hz=config['carrier_hz'])
                    for i in range(2):
                        epoch=i*4_000_000;values=fixture(config,epoch=epoch)
                        block=SampleBlock('radar_rx0',values,i*len(values),epoch,config['sample_rate_hz'],config['carrier_hz'],0.)
                        recording.write_window([block])
                        writer.send_samples(block.receiver,block.samples,timestamp_ns=epoch,sample_rate_hz=block.sample_rate_hz)
                        writer.commit_window();frame=get(http,f'/frames/{epoch}');d=frame['radar']['detection']
                        assert d['detected'] and abs(d['range_m']-123)<4 and abs(d['range_rate_m_s']+12)<1,d
                        assert frame['complete_windows']==i+1 and (root/'live'/f'{epoch}.json').is_file()
                        saved.append((values.tobytes(),frame['radar']))
                    assert writer.finish()=={'radar_rx0':80000}
            finally:recording.close()
            for index,blocks in enumerate(iter_recorded_windows(root/'iq')):assert blocks[0].samples.tobytes()==saved[index][0]
            processes[0].terminate();processes[0].wait(timeout=5)
            assert commands[-1]==('/v1/state',{'transmit':False,'receive':False})
            command_count=len(commands)
            replay_iq,replay_http=start('replayed');outcome=replay(root/'iq',port=replay_iq,timeout=10)
            assert outcome['windows']==2
            for i,(_,expected) in enumerate(saved):
                result=get(replay_http,f'/frames/{i*4000000}')
                assert result['radar']==expected,'Unchanged I/Q must produce identical radar results'
            assert len(commands)==command_count,'Replay must not command a transmitter'
            print(json.dumps({'status':'passed','actual_tcp_http_journal':True,'windows':2,
                'source_dependencies_absent':True,'active_tx_rx_control':True,'stop_commanded':True,
                'replay_does_not_transmit':True,'recording_exact':True,'unchanged_skill_replay':True,'detection':d}))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
            radio.shutdown();radio.server_close()


if __name__=='__main__':main()
