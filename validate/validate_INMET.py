#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Oct 31 17:30:12 2022

Created by:
    Danilo Couto de Souza
    Universidade de São Paulo (USP)
    Instituto de Astornomia, Ciências Atmosféricas e Geociências
    São Paulo - Brazil
    
Contact:
    danilo.oceano@gmail.com

    Scripts for validaitng MPAS-A output with ground station data from the
INMET (Brazilian Meteorological Service).

@author: daniloceano
"""

import os
import sys
import argparse
import xarray as xr
import glob
import f90nml
import datetime
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import metpy.calc as mpcalc
import seaborn as sns



def df_data(model_data,inmet_data,variable):
    # Dictionaries containing naming convections for each variable
    model_variables = {'temperature':'t2m', 'pressure':'surface_pressure'}
    inmet_variables = {'temperature':
                       'TEMPERATURA DO AR - BULBO SECO, HORARIA (°C)',
                       'precipitation':'PRECIPITAÇÃO TOTAL, HORÁRIO (mm)',
                       'pressure':
                       'PRESSAO ATMOSFERICA AO NIVEL DA ESTACAO, HORARIA (mB)',
                       'windspeed':'VENTO, VELOCIDADE HORARIA (m/s)'}
    
    # If windpeed, calculate from components
    if variable == 'windspeed':
        model_var = mpcalc.wind_speed(model_data['u10'],model_data['v10'])
    # Sum grid-scale and convective precipitation
    elif variable == 'precipitation':
        model_var = model_data['rainnc']+model_data['rainc']
    else:
        model_var = model_data[model_variables[variable]]
        
    # Get model data for the gridpoint closest to station   
    model_var_station = model_var.sel(latitude=lat_station,
                method='nearest').sel(longitude=lon_station,method='nearest'
                                      ).values
    mpas_df = pd.DataFrame(model_var_station,columns=['value'],
                             index=times)
    
    # Convert temperature to celcius
    if variable == 'temperature':
        mpas_df = mpas_df-273.15
    # Convert pressure to hPa
    elif variable == 'pressure':
        mpas_df = mpas_df/100
        
    mpas_df['source'], mpas_df['variable'] = 'MPAS', variable 
    
    # Get INMET data for the same dates as the model experiment
    inmet_var = station_data[
        (station_data['DATA (YYYY-MM-DD)_HORA (UTC)'] >= times[0]) &
        (station_data['DATA (YYYY-MM-DD)_HORA (UTC)'] <= times[-1])
        ].resample(dt)
    
    # If using precipitation, get accumulated values between model time steps
    if variable == 'precipitation':
        inmet_var = inmet_var.sum()[inmet_variables[variable]].cumsum()
    # Else, get mean values between model time steps
    else:
        inmet_var = inmet_var.mean()[inmet_variables[variable]]
        
    inmet_df = pd.DataFrame(inmet_var.rename('value'))
    inmet_df['source'],inmet_df['variable'] = 'INMET', variable
    station_df = pd.concat([inmet_df,mpas_df])
    # Add date as column and revert indexes to a range of numbers
    station_df['date'] = station_df.index
    station_df.index = range(len(station_df))
    return station_df


def convert_to_sns_fmt(df_list):                          
    met_data_station = pd.concat(df_list)
    met_data_station['date'] = met_data_station.index
    met_data_station.index = range(len(met_data_station))
    return met_data_station

## Workspace ##
work_dir = os.getenv('MPAS_DIR')
if work_dir is None:
    print('Error: MPAS_DIR environment variable not defined! It should direct\
to the MPAS-BR path')
    sys.exit(-1)
# work_dir = '~/Documents/MPAS/MPAS-BR/'
INMET_dir = work_dir+'/met_data/INMET/'

## Parser options ##
parser = argparse.ArgumentParser()
parser.add_argument('-m','--model', type=str, required=True,
                        help='''path to model data in nc format, regridded to \
latlon format (convert_mpas utility)''')

parser.add_argument('-s','--station', type=str, required=True,
                        help='''station name to compare model data to''')

parser.add_argument('-o','--output', type=str, default=None,
                        help='''output name to append file''')

# ## Dummy arguments for debugging ##
# args = parser.parse_args(['--model','/home/daniloceano/Downloads/latlon.nc',
#                '--station','Florianopolis', '--variable', 'temperature'])
args = parser.parse_args()

## Open model data with xarray ##
if not os.path.isfile(args.model):
    print("MPAS-A file was not found")
    sys.exit(-1)
else:
    print('Opening MPAS-A file: '+args.model)
    model_data = xr.open_dataset(args.model)
    
## Open namelist.atmosphere file ##
namelist_path = "/".join((args.model).split('/')[:-1])+"/namelist.atmosphere"
if not os.path.isfile(namelist_path):
    print("namelist.atmosphere file was not found")
    sys.exit(-1)
else:
    print('namelist.atmosphere file found')
    namelist = f90nml.read(glob.glob(namelist_path)[0])
    

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
finish_date_str = finish_date.strftime('%Y-%m-%d_%H:%M:%S')

## Create a range of dates ##
times = pd.date_range(start_date,finish_date,periods=len(model_data.Time))
# Get time interval
dt = times[1] - times[0]

## Get station data ##
station = (args.station).upper()
# Get data for corresponding year
try:
    station_file = glob.glob(INMET_dir+'/'+str(start_date.year)+'/*'+station+'*')[0]
except:
    print('Error: station file not found!')
    sys.exit(-1)
# Open file with Pandas
station_data = pd.read_csv(station_file,header=8,sep=';',encoding='latin-1',
                           parse_dates=[[0,1]],decimal=',')
station_data.index = station_data['DATA (YYYY-MM-DD)_HORA (UTC)']
# Get station lati and lon
with open(station_file, 'r',encoding='latin-1') as file:
    for line in file:
        if 'LATITUDE' in line:
            lat_station = float((line[10:-1].replace(',','.')))
        elif 'LONGITUDE' in line:
            lon_station = float((line[11:-1].replace(',','.')))
            pass

# Get data in a seaborn-readable dataframe format

# # Put all data together with seaborn format
# met_data_station = convert_to_sns_fmt([station_temp_data,station_prec_data,
#                               station_wspeed_data])
    
## Plot with Seaborn
sns.set_theme(style="ticks")

variables = ['temperature','precipitation','windspeed','pressure']
fig, axes = plt.subplots(2, 2, figsize=(18, 10))
i = 0
for row in range(2):
    for col in range(2):
        var = variables[i]
        data = df_data(model_data, station_data, var)
        sns.lineplot(x="date", y="value", hue="source", markers=True,
                     ax=axes[row,col],data=data, lw=4, 
                     palette=['#e63946', '#1d3557'])
        axes[row,col].set(ylabel=var, xlabel=None)
        i +=1
if args.output is not None:
    fname = args.o
else:
    fname = (args.model).split('/')[-1].split('.nc')[0]
plt.savefig(fname+'_timeseries_'+station)
