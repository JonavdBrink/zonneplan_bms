[![HACS](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://hacs.xyz/)
![Active installations](https://badge.t-haber.de/badge/zonneplan_one?kill_cache=1)
[![GitHub Release](https://img.shields.io/github/v/release/jonavdbrink/bms?style=for-the-badge&label=Release)](https://github.com/jonavdbrink/bms/releases)
![GitHub License](https://img.shields.io/github/license/fsaris/home-assistant-zonneplan-one?style=for-the-badge)
![stability-stable](https://img.shields.io/badge/stability-stable-red.svg?style=for-the-badge&color=red)

![Zonneplan peakdetection](./images/logo.png)

<p align="center">
  <img src="https://raw.githubusercontent.com/jonavdbrink/bms/images/logo.png" width="250">
</p>

Repository based on: https://github.com/fsaris/home-assistant-zonneplan-one

# Zonneplan Peak Detect

This Home Assistant custom component detects the highest and lowest price hours from Zonneplan hourly pricing data.

## Features
- Creates two sensors:
  - `sensor.zonneplan_peak_hour`: index (0-23) of highest price
  - `sensor.zonneplan_valley_hour`: index (0-23) of lowest price

## Requirements
You must have an entity like `sensor.zonneplan_energy_prices` that includes an attribute `today` with a list of 24 hourly prices.

## Installation
[![Direct link to Zonneplan in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=JonavdBrink&repository=zonneplan_bms)
1. Copy this repository into `custom_components/zonneplan_peakdetect`
2. Add it via HACS as a custom repository (category: Integration)
3. Restart Home Assistant

## License
MIT
