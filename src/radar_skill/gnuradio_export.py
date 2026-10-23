# Copyright (c) 2026 wa200508
# SPDX-License-Identifier: Apache-2.0
# Extracted from airsim-rf 3b36fe0; original MIT notice is retained in licenses/.
"""Write portable stock-block GNU Radio Companion graphs; no GNU Radio import.

cf32_le SigMF data is already GNU Radio's native gr_complex File Source layout.
The graphs reference it in place and add no duplicate sample files.
"""
import json
from pathlib import Path


def flowgraph(manifest, *, gui=False):
    channels=manifest['channels'];count=len(channels)
    def block(name,identifier,parameters,x,y):
        return {'name':name,'id':identifier,'parameters':parameters,
            'states':{'coordinate':[x,y],'rotation':0,'state':'enabled'}}
    blocks=[
        block('recording_dir','parameter',{'type':'str','value':'.','label':'Recording directory'},20,20),
        block('samp_rate','variable',{'value':str(manifest['sample_rate_hz'])},240,20),
        block('carrier_hz','variable',{'value':str(manifest['carrier_hz'])},440,20),
        block('sample_count','variable',{'value':str(manifest['samples_per_channel'])},640,20),
        block('start_sample','parameter',{'type':'intx','value':'0','label':'Start sample (all channels)'},20,100),
        block('playback_speed','parameter',{'type':'eng_float','value':'1','label':'Playback speed'},260,100),
        block('coherent_join','blocks_streams_to_vector',{'type':'complex','num_streams':str(count),'vlen':'1'},460,240),
        block('shared_pacer','blocks_throttle',{'type':'complex','samples_per_second':'samp_rate * playback_speed',
            'vlen':str(count),'ignoretag':'True'},680,240),
        block('coherent_split','blocks_vector_to_streams',{'type':'complex','num_streams':str(count),'vlen':'1'},900,240)]
    connections=[['coherent_join','0','shared_pacer','0'],['shared_pacer','0','coherent_split','0']]
    for index,name in enumerate(channels):
        blocks.append(block('file_'+name,'blocks_file_source',{'type':'complex',
            'file':f"recording_dir + '/{name}.sigmf-data'",'repeat':'False','vlen':'1',
            'offset':'start_sample','length':'sample_count - start_sample','begin_tag':'pmt.PMT_NIL'},20,220+index*100))
        blocks.append(block('dsp_'+name,'blocks_null_sink',{'type':'complex','vlen':'1'},1160,220+index*100))
        connections.extend([['file_'+name,'0','coherent_join',str(index)],
            ['coherent_split',str(index),'dsp_'+name,'0']])
    if gui:
        blocks.extend([
            block('view_channel','variable_qtgui_chooser',{'type':'int','value':'0',
                'label':'Antenna channel','num_opts':'0','options':str(list(range(count))),
                'labels':repr(channels),'widget':'combo_box'},480,100),
            block('channel_selector','blocks_selector',{'type':'complex','num_inputs':str(count),
                'num_outputs':'1','input_index':'view_channel','output_index':'0','enabled':'True','vlen':'1'},1340,240),
            block('spectrum','qtgui_freq_sink_x',{'type':'complex','name':'Receiver I/Q (complex volts)',
                'fftsize':'2048','fc':'carrier_hz','bw':'samp_rate','nconnections':'1',
                'ymin':'-160','ymax':'0','label':'FFT magnitude','units':'dB',
                'autoscale':'True','grid':'True'},1560,240)])
        for index in range(count):connections.append(['coherent_split',str(index),'channel_selector',str(index)])
        connections.append(['channel_selector','0','spectrum','0'])
    return {'options':{'parameters':{'id':'iq_view' if gui else 'iq_replay',
        'title':'Recorded receiver I/Q','author':'airsim-rf','output_language':'python',
        'generate_options':'qt_gui' if gui else 'no_gui','run_options':'run',
        'description':'Stock complex File Sources; connect your DSP to coherent_split outputs. '
            'All channels share one vector pacer. No scaling/resampling/repeat. '
            'Start in this archive directory or set recording_dir. '
            'Acquisition timestamps/window boundaries are in windows.jsonl, not stream tags.'},
            'states':{'coordinate':[0,0],'rotation':0,'state':'enabled'}},
        'blocks':blocks,'connections':connections,'metadata':{'file_format':1}}


def write_flowgraphs(directory, manifest):
    """Create editable .grc files beside data, with portable relative file paths.

    JSON is a YAML subset accepted by GRC; no extra YAML dependency is needed.
    The source's close path calls this only once, after the final sample count.
    """
    root=Path(directory)
    for filename,gui in (('replay.grc',False),('view.grc',True)):
        temporary=root/(filename+'.tmp')
        temporary.write_text(json.dumps(flowgraph(manifest,gui=gui),indent=2)+'\n')
        temporary.replace(root/filename)
