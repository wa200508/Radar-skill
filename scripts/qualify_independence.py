"""Real TCP/HTTP acquisition and exact recording replay with no source installed."""
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import build_opener,ProxyHandler
import numpy as np
from radar_skill.sample_stream import SampleBlock,SampleStreamWriter
from radar_skill.sample_recording import SampleRecordingWriter,iter_recorded_windows,replay


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
        path=root/'receiver.json';path.write_text(json.dumps(config));processes=[]
        def start(name):
            iq,http=port(),port()
            while http==iq:http=port()
            process=subprocess.Popen([sys.executable,'-m','radar_skill','--arrays',str(path),'--results-dir',str(root/name),
                '--iq-port',str(iq),'--http-port',str(http)])
            processes.append(process);deadline=time.monotonic()+15
            while True:
                try:
                    if get(http,'/health')['ingress_ready']:return iq,http
                except OSError:pass
                if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Radar service failed startup')
                time.sleep(.02)
        try:
            iq,http=start('live');saved=[];recording=SampleRecordingWriter(root/'iq',config)
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
            replay_iq,replay_http=start('replayed');outcome=replay(root/'iq',port=replay_iq,timeout=10)
            assert outcome['windows']==2
            for i,(_,expected) in enumerate(saved):
                result=get(replay_http,f'/frames/{i*4000000}')
                assert result['radar']==expected,'Unchanged I/Q must produce identical radar results'
            print(json.dumps({'status':'passed','actual_tcp_http_journal':True,'windows':2,
                'source_dependencies_absent':True,'recording_exact':True,'unchanged_skill_replay':True,'detection':d}))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=='__main__':main()
