# Copyright (c) 2026 wa200508
# SPDX-License-Identifier: Apache-2.0
# Extracted from airsim-rf 3b36fe0; original MIT notice is retained in licenses/.
"""FMCW range/Doppler processing from ADC samples and receiver timing only."""
import numpy as np

C=299792458.


def range_doppler(samples,rate,carrier,acquisition,*,chirps=64,threshold_db=18.):
    stride=round(acquisition['pri_s']*rate);start=round(acquisition['adc_start_s']*rate);n=acquisition['num_samples']
    if len(samples)%stride or len(samples)//stride<chirps or start+n>stride:raise ValueError('Need complete ADC gates and at least 64 chirps')
    cube=np.asarray(samples).reshape(-1,stride)[:chirps,start:start+n]
    cube=cube-cube.mean(axis=1,keepdims=True)  # Remove per-ramp ADC DC offset.
    spectrum=np.fft.fftshift(np.fft.fft2(cube*np.hanning(chirps)[:,None]*np.hanning(n)[None,:]),axes=0)[:,:n//2]
    power=abs(spectrum)**2;floor=max(float(np.median(power)),1e-30)
    snr=10*np.log10(np.maximum(power/floor,1e-30))
    slow=np.fft.fftshift(np.fft.fftfreq(chirps,acquisition['pri_s']))
    beat=np.fft.fftfreq(n,1/rate)[:n//2];slope=acquisition['bandwidth_hz']/acquisition['chirp_duration_s']
    # TX*conj(RX): positive slow-time frequency denotes receding motion.
    velocities=slow*C/(2*carrier);ranges=beat*C/(2*slope)
    y,x=np.unravel_index(power.argmax(),power.shape)
    detected=bool(snr[y,x]>=threshold_db)
    # Beat contains positive range rate; subtract that contribution from range.
    detection={'detected':detected,'range_m':float((beat[x]-slow[y])*C/(2*slope)) if detected else None,
               'range_rate_m_s':float(velocities[y]) if detected else None,'contrast_db':float(snr[y,x]),'threshold_db':threshold_db}
    # Display matrices are [range row][Doppler column]. Preserve every finite
    # contrast value; detector statistics use the original, untransposed FFT.
    coherent_gain=(np.hanning(chirps).sum()*np.hanning(n).sum())**2
    median_dbv2=float(10*np.log10(max(float(np.median(power))/coherent_gain,1e-30)))
    return {'range_m':ranges.tolist(),'range_rate_m_s':velocities.tolist(),'doppler_khz':(slow/1000).tolist(),
            'power_db':snr.T.tolist(),'power_axes':['range','doppler'],
            'median_bin_power_dbv2':median_dbv2,
            'peak_bin_power_dbv2':float(10*np.log10(max(float(power.max())/coherent_gain,1e-30))),
            'detection':detection,'chirps':chirps,'range_resolution_m':C/(2*slope*(n/rate)),
            'velocity_resolution_m_s':C/(2*carrier*chirps*acquisition['pri_s'])}

