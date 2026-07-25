[![HACS](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://hacs.xyz/)
[![GitHub Release](https://img.shields.io/github/v/release/jonavdbrink/zonneplan_bms?style=for-the-badge&label=Release)](https://github.com/jonavdbrink/zonneplan_bms/releases)
![GitHub License](https://img.shields.io/github/license/jonavdbrink/zonneplan_bms?style=for-the-badge)
![stability-stable](https://img.shields.io/badge/stability-stable-red.svg?style=for-the-badge&color=red)

<p align="center">
  <img src="./images/logo.png" width="220" alt="Zonneplan Battery Optimizer">
  <h1 align="center">Zonneplan Battery Optimizer (BMS)</h1>
</p>

Repository based on: [fsaris/home-assistant-zonneplan-one](https://github.com/fsaris/home-assistant-zonneplan-one)

---

**Zonneplan Battery Optimizer** is a Home Assistant custom integration that implements an **Energy Arbitrage Scheduler** for your Battery Energy Storage System (BESS). It calculates an optimal battery charging and discharging schedule based on hourly or quarterly (15-minute) energy prices, maximizing profit by charging when prices are low (valleys) and discharging when prices are high (peaks).

---

## 🚀 Features

- **Optimal Action State**: Exposes a main sensor that dictates whether your battery should `Charge`, `Discharge`, or `Stop` in real-time.
- **Native Quarterly (15-Minute) Support**: Built to dynamically detect and support both **hourly** (60-minute) and **quarter-hourly** (15-minute) price forecasts. It seamlessly scales your configuration settings (e.g. `charge_quarters` is adjusted dynamically depending on interval spacing) and evaluates state matches at exact interval resolutions.
- **Intelligent Wave-Segmentation Algorithm**: Divides the forecast into localized price "waves" to detect multiple profitable cycles within a single day.
- **Dynamic Energy Balancing**: Constrains the charge slots in each interval by the number of profitable discharge slots to maintain energy balance.
- **Detailed Arbitrage Schedule**: Provides full access to the scheduled actions for every hour or quarter of the upcoming day via sensor attributes.
- **HASS UI Configuration**: Fully configurable and reconfigurable via the standard Home Assistant Integrations UI, with full backwards-compatibility.

---

## 🧠 How the "Wave" Algorithm Works

Instead of simply choosing global daily minimums and maximums, the Zonneplan Battery Optimizer splits the pricing forecast into separate cycles (intervals/waves) to maximize daily arbitrage opportunities:

1. **Valley Detection**: Starting from the current slot, the algorithm tracks prices to find a local valley (dip). The search for a valley stops when prices recover by more than your configured minimum profit threshold (`min_profit_c_kwh`).
2. **Peak Detection**: After the local valley, the algorithm tracks prices to find a subsequent local peak (hump). This peak search stops when prices drop by more than the minimum profit threshold, indicating the end of the wave and the start of the next cycle.
3. **Threshold Check**: An interval is only deemed profitable if the maximum peak price minus the minimum valley price is greater than or equal to your configured minimum profit.
4. **Slot Selection**:
   - **Charge Slots**: Selects the cheapest slots *before* the valley within the current wave where the price is low enough to yield the required profit.
   - **Discharge Slots**: Selects the most expensive slots *after* the valley within the current wave where the price is high enough to yield the required profit.
5. **Balancing & Resolution Scaling**:
   - The scheduler dynamically detects the forecast interval duration (e.g., 15 minutes or 60 minutes) from the input sensor.
   - Your configured `charge_quarters` and `discharge_quarters` are automatically scaled to the correct number of slots (e.g. if the forecast is hourly instead of quarterly, a configuration of 8 quarters is scaled down to 2 hourly slots).
   - To prevent over-charging/under-discharging, the number of scheduled charging slots is constrained by the number of available profitable discharging slots in that wave. All other slots are marked as `Stop`.

---

## ⚙️ Configuration Parameters

During the integrations setup flow (or via **Configure**), you can customize the following settings:

| Parameter | Key / Config Name | Default | Description |
| :--- | :--- | :--- | :--- |
| **Forecast Entity** | `forecast_entity` | `sensor.zonneplan_current_quarter_hourly_electricity_tariff` | The Home Assistant entity that provides the electricity price forecast attribute. Supports both standard hourly and quarter-hourly formats. |
| **Minimum Profit** | `min_profit_c_kwh` | `6` | The minimum price difference (in cents per kWh) required between charge and discharge intervals to trigger an action. |
| **Charge Quarters** | `charge_quarters` | `8` | Maximum charging duration (in 15-minute quarters) allowed per price wave/interval (e.g., `8` quarters = 2 hours). |
| **Discharge Quarters** | `discharge_quarters` | `8` | Maximum discharging duration (in 15-minute quarters) allowed per price wave/interval (e.g., `8` quarters = 2 hours). |
| **Price Delta %** | `price_delta_percent` | `20` | Percentage threshold used for calculating price multipliers in attributes. |

*Note: If you are upgrading from an older version, your existing `charge_hours` and `discharge_hours` settings are automatically converted to quarters (`hours * 4`) for seamless backwards compatibility.*

---

## 📊 Entity & Attributes

The integration registers a single sensor, `sensor.battery_optimizer_action` (Entity ID is dynamic based on setup).

### State
- **`Charge`**: Battery should be charging from the grid.
- **`Discharge`**: Battery should be exporting to the grid / powering the home.
- **`Stop`**: Battery should stand by (neither charge nor discharge).

### Attributes
- **`intervals`**: The number of profitable arbitrage intervals/cycles currently scheduled.
- **`min_profit_required_eur_kwh`**: The configured minimum profit threshold, converted to €/kWh (e.g., `0.06`).
- **`charge_quarters`**: Configured maximum charging duration in quarters.
- **`discharge_quarters`**: Configured maximum discharging duration in quarters.
- **`schedule`**: A structured list mapping actions and details for each slot of the upcoming forecast:
  ```json
  [
    {
      "datetime": "2026-07-25T14:00:00+02:00",
      "price_eur_kwh": 0.28,
      "price_multiplier": 1.15,
      "action": "Stop",
      "interval_id": 0
    },
    {
      "datetime": "2026-07-25T14:15:00+02:00",
      "price_eur_kwh": 0.18,
      "price_multiplier": 0.74,
      "action": "Charge",
      "interval_id": 0
    }
  ]
  ```

---

## 🛠️ Requirements

1. **Home Assistant** 2025.10.1 or newer.
2. A forecast sensor (default: `sensor.zonneplan_current_quarter_hourly_electricity_tariff`) providing a `forecast` attribute containing hourly or quarterly electricity price data.
   - Supports both standard Day-Ahead schemas (`datetime` / `electricity_price`) and modern Zonneplan nested schemas (e.g., `start_date` / `price_tax_included.amount`).
   - *Example format expected in `forecast` attribute:*
     ```yaml
     forecast:
       - start_date: "2026-07-25T14:00:00+02:00"
         price_tax_included:
           amount: 2800000  # Raw integer in deci-micro-euro (€0.28/kWh)
       - start_date: "2026-07-25T14:15:00+02:00"
         price_tax_included:
           amount: 1800000  # Automatically detects 15-minute spacing!
     ```

---

## 💾 Installation

### Method 1: HACS (Recommended)
[![Direct link to Zonneplan in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=JonavdBrink&repository=zonneplan_bms)

1. Ensure [HACS](https://hacs.xyz/) is installed.
2. Search for **Zonneplan Peak Detect** (or add `https://github.com/jonavdbrink/zonneplan_bms` as a Custom Repository under **Integration**).
3. Click **Download**.
4. **Restart** Home Assistant.
5. In Home Assistant, go to **Settings** -> **Devices & Services** -> **Add Integration**, and search for **Zonneplan Peak Detect** to configure it.

### Method 2: Manual Installation
1. Copy the `custom_components/zonneplan_peakdetect` directory from this repository into the `custom_components` directory in your Home Assistant configuration directory (e.g., `/config/custom_components/`).
2. **Restart** Home Assistant.
3. In Home Assistant, go to **Settings** -> **Devices & Services** -> **Add Integration**, search for **Zonneplan Peak Detect** and configure it.

---

## 🤖 Automation Example

Use the state of the Battery Optimizer sensor in your Home Assistant automations to automatically trigger battery controls (e.g. for Victron ESS, Solax, Growatt, Huawei BESS).

```yaml
alias: "Battery - Grid Arbitrage Control"
description: "Automatically charge, discharge, or idle the home battery based on Zonneplan BMS schedule"
trigger:
  - platform: state
    entity_id: sensor.battery_optimizer_action
condition: []
action:
  - choose:
      - conditions:
          - condition: state
            entity_id: sensor.battery_optimizer_action
            state: "Charge"
        sequence:
          - service: number.set_value
            target:
              entity_id: number.battery_grid_charge_limit
            data:
              value: 100
          - service: select.select_option
            target:
              entity_id: select.battery_mode
            data:
              option: "Charge from grid"
              
      - conditions:
          - condition: state
            entity_id: sensor.battery_optimizer_action
            state: "Discharge"
        sequence:
          - service: select.select_option
            target:
              entity_id: select.battery_mode
            data:
              option: "Export to grid"
              
      - conditions:
          - condition: state
            entity_id: sensor.battery_optimizer_action
            state: "Stop"
        sequence:
          - service: select.select_option
            target:
              entity_id: select.battery_mode
            data:
              option: "Self-consumption / Idle"
mode: restart
```

---

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
