#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Dec 21 09:05:41 2022


Created by:
    Danilo Couto de Souza
    Universidade de São Paulo (USP)
    Instituto de Astornomia, Ciências Atmosféricas e Geociências
    São Paulo - Brazil
    
Contact:
    danilo.oceano@gmail.com
    
    Script for interpolating MPAS-A model variables that are used for the
Lorenz Energy Cycle computations from hybrid vertical levels to pressure levels.
Also, some other procedures are performed:
    
    
The input must be the structured "latlon.nc" file created by the "convert_mpas"
program. As MPAS-A data created by the "convert_mpas" does not (at least at the
moment) present a time dimension with actual dates, there is the option for 
using the namelist.atmosphere to assign dates to the variables time dimension. 
This can be done using the -n flag.

"""

import argparse
import datetime
import f90nml
import glob
import pandas as pd
import xarray as xr
import numpy as np

from metpy.units import units
from metpy.calc import (temperature_from_potential_temperature, 
                        height_to_geopotential, vertical_velocity_pressure,
                        pressure_to_height_std)
from metpy.constants import g
from metpy.interpolate import log_interpolate_1d

from wrf import interplevel, vinterp

from scipy.interpolate import RegularGridInterpolator



def ext_temperature(temperature_IsobaricLevels_height, pressure_levels):
    '''
        Function for extrapolating temperature bellow the topography after the
    interpolation of the MPAS-A data from sigma levels to pressure levels.
    
    Where temperature values are missing, it will search for data in levels
    above and then assume standard atmosphere lapse-rates for extrapolating 
    those values for the layers bellow (first loop in the function). 
    
    Also, there is the case where the data was interpolated to pressure levels
    above the limit in the data (for example: interpolated from 1000 to 10 hPa
    but pressure data only goes until 20 hPa), so there are NaNs also in the
    uppermost levels. For those cases, it is made a bottom-up extrapolation.
    '''
    t_isob_z = temperature_IsobaricLevels_height
    plevs = pressure_levels
    t_isob_z = t_isob_z.metpy.dequantify()
    # First start a loop from the top to the bottom of the atmosphere.
    plevs_sorted = np.sort(plevs)
    for i in range(len(plevs_sorted)):
        
        # Get values for each layer for simplicity
        lev = plevs_sorted[i]
        t_p = t_isob_z.sel(level=lev)
                
        # Assuming standard atmosphere, lapse-rate for the troposphere is
        # 6.5°C per km.
        if lev >= 226.32 * units.hPa:
            lapse = 6.5
        # tropopause
        elif lev < 226.32 * units.hPa and lev > 54.74 * units.hPa:
            lapse = 0
        # levels above that
        else:
            lapse = -1
        
        # Search for NaNs (places where there is no data for temperature).
        # As missing values can be due to 1) topography or 2) highest plev 
        # level being higher than model data, firstly, let's fill levels
        # related to the first case.
        if np.isnan(t_p.values).any() and lev > 50 * units.hPa:
            print('interpolating data for level: '+str(lev))
            # Change NaN values to -99999 for easier indexing
            dummy = t_p.metpy.dequantify().fillna(-99999)
            # Now, get only values where there are NaNs in the original variable
            nans = dummy.where(dummy == -99999)
            # Get temperature from level above that:
            # If first interaction, t_above comes from initial data, but
            # on later interactions, get already interpolated data
            t_above = t_isob_z.isel(level=i-1).metpy.dequantify()
            # Get difference in height
            dz = t_above.standard_height - nans.standard_height
            # Temperature as extrapolated from lapse rate
            dtemp = (dz*lapse).metpy.dequantify()
            # Assign new temperature values to Nan dataset
            extp_temp =  nans + 99999+t_above+dtemp
            # Now, assign new values to where are the NaNs in the original data
            t_isob_z.loc[dict(level=lev)] = dummy.where(dummy != -99999,
                                                        extp_temp)
            
    # Now, a loop from bottom to top, so we can extrapolate values where plevs
    # are above available data
    bottom_top = plevs_sorted[::-1]
    for i in range(len(bottom_top)):
        
        lev = bottom_top[i]
        lev_below = bottom_top[i-1]
        t_p = t_isob_z.sel(level=lev)
        
        # Lapse rates
        if lev >= 8.68 * units.hPa:
            lapse = -2.8
        else:
            lapse = 0
            
        if np.isnan(t_p.values).any() and lev < 50 * units.hPa:
            print('interpolating data for level: '+str(lev))
            # Change NaN values to -99999 for easier indexing
            dummy = t_p.metpy.dequantify().fillna(-99999)
            # Now, get only values where there are NaNs in the original variable
            nans = dummy.where(dummy == -99999)
            # Get temperature from level above that:
            # If first interaction, t_above comes from initial data, but
            # on later interactions, get already interpolated data
            t_bellow = t_isob_z.sel(level=lev_below).metpy.dequantify()
            # Get difference in height
            dz = t_bellow.standard_height - nans.standard_height
            # Temperature as extrapolated from lapse rate
            dtemp = (dz*lapse).metpy.dequantify()
            # Assign new temperature values to Nan dataset
            extp_temp =  nans + 99999+t_bellow+dtemp
            # Now, assign new values to where are the NaNs in the original data
            t_isob_z.loc[dict(level=lev)] = dummy.where(dummy != -99999,
                                                        extp_temp)
    return t_isob_z


def get_times_nml(namelist,model_data):
    ## Identify time range of simulation using namelist ##
    # Get simulation start and end dates as strings
    start_date_str = namelist['nhyd_model']['config_start_time']
    run_duration_str = namelist['nhyd_model']['config_run_duration']
    # Convert strings to datetime object
    start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d_%H:%M:%S')
    
    run_duration = datetime.datetime.strptime(run_duration_str,'%d_%H:%M:%S')
    # Get simulation finish date as object and string
    finish_date  = start_date + datetime.timedelta(days=run_duration.day,
                                                   hours=run_duration.hour)
    ## Create a range of dates ##
    times = pd.date_range(start_date,finish_date,periods=len(model_data.Time)+1)[1:]
    return times

#------------------------------------------------------------------------------ 
def main():
    data = xr.open_dataset(infile)
    print('Data have '+str(len(data.nVertLevels))+
          ' levels in the nVertLevels dimension')
    
    #If requested, open namelist so we can get the time dimension
    if args.namelist:
        print('opening namelist.atmosphere...')
        namelist_path = infile.split("latlon.nc")[0]+"namelist.atmosphere"
        namelist = f90nml.read(glob.glob(namelist_path)[0])
        time = get_times_nml(namelist,data)
        print('ok, times:',time)
    pressure = (data['pressure'] * units(data['pressure'].units)
                ).metpy.convert_units('hPa')
    
    # Levels to interpolate to
    plevs = [10.,   20.,   30.,   50.,   70.,
            100.,  125.,  150.,  175.,  200.,  225.,  250.,  300.,  350.,  400.,
            450.,  500.,  550.,  600.,  650.,  700.,  750.,  775.,  800.,  825.,
            850.,  875.,  900.,  925.,  950.,  975., 1000.] * units.hPa
    print("data will be interpolated to:",plevs)
    
    # Open variables
    u = data['uReconstructMeridional'] * units('m/s')
    v = data['uReconstructZonal'] * units('m/s')
    theta = data['theta'] * units('K')
    z = data['zgrid'] * units.m
    w = data['w'] * units('m/s')
    pressure = (data['pressure'] * units(data['pressure'].units)
                ).metpy.convert_units('hPa')
    mixing_ratio  = data['qv']
    
    # Create a 4D height field to interpolate it into pressure levels
    lat, lon = data.latitude, data.longitude
    nVertLevels = data.nVertLevels
    # Do not get last vertical level
    matching_z = z.isel(nVertLevelsP1=slice(0,len(nVertLevels)))
    # create dataarray using existing dimensions
    z_4D = xr.DataArray(matching_z,
                        coords={'latitude': lat, 'longitude': lon, 
                                'nVertLevels': nVertLevels},
             dims=['nVertLevels', 'latitude', 'longitude'])
    z_4D = z_4D.expand_dims(dim={"Time": time})
    # Interpolate z to pressur elevels
    z_isob =  interplevel(z_4D, pressure, plevs)

    # Get temperature and interpolate to pressure levels
    t = temperature_from_potential_temperature(pressure, theta)    
    t_isob = interplevel(t, pressure, plevs) * t.metpy.units
    # Add a coordinate with heights using standard atmosphere
    standard_height = pressure_to_height_std(t_isob.level* units. hPa)
    t_isob_z = t_isob.copy().assign_coords(standard_height=standard_height)
    t_isob_z = t_isob_z.assign_coords(Time=time)
    # Extrapolate temperature on pressure levels bellow topography
    t_ext = ext_temperature(t_isob_z, plevs) * units.K
    # Velocity components will be interpolated
    u_isob = interplevel(u, pressure, plevs) * u.metpy.units
    v_isob = interplevel(v, pressure, plevs) * v.metpy.units
    
    
    u_intp = u_isob.copy()
    while np.isnan(u_intp.values).any():
        print('interpolating u..')
        u_intp = u_intp.interpolate_na(dim='latitude',method='cubic')
    
    # geopotential = height_to_geopotential(height)
    # geopotential_height = geopotential/g
    # omega = vertical_velocity_pressure(w, pressure, temperature, mixing_ratio)
    
    # if args.output and ".nc" not in args.output:
    #     fname = args.output+".nc"
    # elif args.output and ".nc" in args.output:
    #     fname = args.output
    # else:
        # fname = infile.split("/")[-1].split(".nc")[0]+"_isobaric.nc"
        
        
if __name__ == "__main__":
    ## Parser options ##
    parser = argparse.ArgumentParser()
    parser.add_argument('-i','--infile', type=str, required=True,
                            help='''Model output to interpolate''')
    parser.add_argument('-o','--output', type=str, default=None,
                            help='''output name to append file''')
    parser.add_argument('-n','--namelist', type=str, default="",
                            help='''Use this flag if namelist.atmosphere is on\
the same path as the infile so the program is able to create a time dimension.''')
    args = parser.parse_args()
    infile = args.infile
    print('Opening:',infile)
    main()
