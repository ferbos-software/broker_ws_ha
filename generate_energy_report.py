# generate_energy_report.py (Multi-HA with per-site config)
import json
import os
import asyncio
import websockets
from datetime import datetime, timedelta
import argparse
import time

# Load site config dan entity mapping
parser = argparse.ArgumentParser()
parser.add_argument("--site", required=True, help="Nama site untuk memilih file config")
args = parser.parse_args()

mapping_file = f"mapping_{args.site}.json"

with open(mapping_file) as f:
    entity_map = json.load(f)

# Get configuration from the mapping file
site_config = entity_map.get("config", {})
HA_WS_URL = site_config.get("ha_url")
HA_TOKEN = site_config.get("ha_token")

async def get_current_states():
    async with websockets.connect(HA_WS_URL) as ws:
        await ws.recv()  # Hello
        await ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
        auth_response = json.loads(await ws.recv())
        if auth_response.get("type") != "auth_ok":
            raise Exception("Auth failed to Home Assistant WebSocket API")

        await ws.send(json.dumps({"id": 1, "type": "get_states"}))
        msg = json.loads(await ws.recv())
        states = {e['entity_id']: e['state'] for e in msg.get("result", [])}
        return states

async def get_historical_data(ws, entity_id, start_time, end_time):
    """Get historical data for an entity between start_time and end_time."""
    if not entity_id:
        # print("Warning: Empty entity_id provided")
        return []
        
    # print(f"Fetching historical data for {entity_id} from {start_time} to {end_time}")
    
    try:
        # Split the time range into 2-day chunks to avoid message size limits
        chunk_size = timedelta(days=2)
        current_start = start_time
        all_data = []
        
        while current_start < end_time:
            current_end = min(current_start + chunk_size, end_time)
            
            # Create a unique message ID for each request
            message_id = int(time.time() * 1000)
            
            # Request historical data for this chunk
            await ws.send(json.dumps({
                "id": message_id,
                "type": "history/history_during_period",
                "start_time": current_start.isoformat(),
                "end_time": current_end.isoformat(),
                "entity_ids": [entity_id]
            }))
            
            # Wait for and process the response
            response = await ws.recv()
            data = json.loads(response)
            
            if data.get("success"):
                entity_data = data["result"].get(entity_id, [])
                all_data.extend(entity_data)
            
            # Move to next chunk
            current_start = current_end
            
        if all_data:
            print(f"Received {len(all_data)} data points for {entity_id}")
            print(f"First data point: {all_data[0]}")
            print(f"Last data point: {all_data[-1]}")
            
        return all_data
    except Exception as e:
        # print(f"Error fetching data for {entity_id}: {str(e)}")
        return []

def process_hourly_data(historical_data, entity_id):
    # Initialize array with 24 zeros
    hourly_values = [0.0] * 24
    
    if not historical_data:
        # print(f"Warning: No historical data for {entity_id}")
        return hourly_values
        
    # Sort data points by timestamp
    sorted_data = sorted(historical_data, key=lambda x: (
        datetime.fromisoformat(x.get("last_updated", "").replace("Z", "+00:00")) 
        if x.get("last_updated") else 
        datetime.fromtimestamp(float(x.get("lu", 0)))
    ))
    
    # print(f"Processing {len(sorted_data)} data points for {entity_id}")
    
    # Process each data point
    for i in range(1, len(sorted_data)):
        try:
            current = sorted_data[i]
            previous = sorted_data[i-1]
            
            # Get timestamps
            current_timestamp = None
            previous_timestamp = None
            
            if current.get("last_updated"):
                current_timestamp = datetime.fromisoformat(current.get("last_updated").replace("Z", "+00:00"))
            elif current.get("lu"):
                current_timestamp = datetime.fromtimestamp(float(current.get("lu")))
                
            if previous.get("last_updated"):
                previous_timestamp = datetime.fromisoformat(previous.get("last_updated").replace("Z", "+00:00"))
            elif previous.get("lu"):
                previous_timestamp = datetime.fromtimestamp(float(previous.get("lu")))
            
            if not current_timestamp or not previous_timestamp:
                # print(f"Skipping data point due to missing timestamp for {entity_id}")
                continue
                
            # Get values
            current_value = float(current.get("state") or current.get("s") or 0)
            previous_value = float(previous.get("state") or previous.get("s") or 0)
            
            # Calculate hourly difference
            hour = current_timestamp.hour
            hourly_diff = current_value - previous_value
            
            # Only add positive differences to avoid negative values
            if hourly_diff > 0:
                hourly_values[hour] += hourly_diff
                # print(f"Hour {hour}: Added {hourly_diff} to total (current: {current_value}, previous: {previous_value})")
                
        except (ValueError, TypeError) as e:
            # print(f"Error processing data point for {entity_id}: {e}")
            # print(f"Problematic data point: {current}")
            continue
    
    # Round all values to 1 decimal place
    hourly_values = [round(value, 1) for value in hourly_values]
            
    # print(f"Final hourly values for {entity_id}: {hourly_values}")
    return hourly_values

def process_daily_data(historical_data, entity_id):
    """Process historical data into daily totals."""
    # Dictionary to accumulate daily totals
    daily_totals = {}
    if not historical_data:
        # print(f"Warning: No historical data for {entity_id}")
        return []
    # Sort data points by timestamp
    sorted_data = sorted(historical_data, key=lambda x: (
        datetime.fromisoformat(x.get("last_updated", "").replace("Z", "+00:00")) 
        if x.get("last_updated") else 
        datetime.fromtimestamp(float(x.get("lu", 0)))
    ))
    # print(f"Processing {len(sorted_data)} data points for {entity_id} (daily)")
    # Group by day and sum positive differences
    for i in range(1, len(sorted_data)):
        try:
            current = sorted_data[i]
            previous = sorted_data[i-1]
            # Get timestamps
            current_timestamp = None
            previous_timestamp = None
            if current.get("last_updated"):
                current_timestamp = datetime.fromisoformat(current.get("last_updated").replace("Z", "+00:00"))
            elif current.get("lu"):
                current_timestamp = datetime.fromtimestamp(float(current.get("lu")))
            if previous.get("last_updated"):
                previous_timestamp = datetime.fromisoformat(previous.get("last_updated").replace("Z", "+00:00"))
            elif previous.get("lu"):
                previous_timestamp = datetime.fromtimestamp(float(previous.get("lu")))
            if not current_timestamp or not previous_timestamp:
                continue
            # Get values
            current_value = float(current.get("state") or current.get("s") or 0)
            previous_value = float(previous.get("state") or previous.get("s") or 0)
            # Calculate daily difference
            day = current_timestamp.date()
            daily_diff = current_value - previous_value
            if daily_diff > 0:
                daily_totals.setdefault(day, 0.0)
                daily_totals[day] += daily_diff
        except (ValueError, TypeError):
            continue
    # Sort by day and round to 1 decimal
    result = [round(daily_totals[day], 1) for day in sorted(daily_totals.keys())]
    # print(f"Final daily values for {entity_id}: {result}")
    return result

async def get_energy_data():
    async with websockets.connect(HA_WS_URL) as ws:
        await ws.recv()  # Hello
        await ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
        auth_response = json.loads(await ws.recv())
        if auth_response.get("type") != "auth_ok":
            raise Exception("Auth failed to Home Assistant WebSocket API")

        end_time = datetime.now()
        start_time = end_time - timedelta(days=1)
        daily_start_time = end_time - timedelta(days=31)

        # Dynamically fetch all arrays from mapping file
        arrays = entity_map.get("arrays", {})
        array_results = {}
        for key, entity_id in arrays.items():
            if key == "device_hourly_usage_kwh":
                continue  # Skip, handled separately below
            # print(f"Fetching array: {key} for entity: {entity_id}")
            if key.startswith("hourly_"):
                hist = await get_historical_data(ws, entity_id, start_time, end_time)
                array_results[key] = process_hourly_data(hist, entity_id)
            elif key.startswith("daily_"):
                hist = await get_historical_data(ws, entity_id, daily_start_time, end_time)
                array_results[key] = process_daily_data(hist, entity_id)
            else:
                # fallback: treat as hourly
                hist = await get_historical_data(ws, entity_id, start_time, end_time)
                array_results[key] = process_hourly_data(hist, entity_id)

        # print("\nFetching device data...")
        # Get device hourly data
        device_hourly_usage = {}
        device_mappings = arrays.get("device_hourly_usage_kwh", {})
        if isinstance(device_mappings, dict):
            for device_id, entity_id in device_mappings.items():
                device_data = await get_historical_data(ws, entity_id, start_time, end_time)
                device_hourly_usage[device_id] = process_hourly_data(device_data, entity_id)

        array_results["device_hourly_usage_kwh"] = device_hourly_usage
        return array_results

def get_days_in_period():
    """Calculate the number of days in the current month."""
    now = datetime.now()
    # Get the last day of the current month
    if now.month == 12:
        last_day = datetime(now.year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = datetime(now.year, now.month + 1, 1) - timedelta(days=1)
    return last_day.day

def get_previous_period():
    """Get the previous month in YYYY-MM format."""
    now = datetime.now()
    if now.month == 1:
        prev_month = datetime(now.year - 1, 12, 1)
    else:
        prev_month = datetime(now.year, now.month - 1, 1)
    return prev_month.strftime("%Y-%m")

async def main():
    states = await get_current_states()
    energy_data = await get_energy_data()

    def safe_float(entity_id):
        try:
            return float(states.get(entity_id, 0.0))
        except:
            return 0.0

    # Process devices using mappings from entity_map
    devices = []
    for device in entity_map.get("devices", []):
        device_data = {
            "device_id": device.get("device_id"),
            "device_name": device.get("device_name"),
            "device_type": device.get("device_type"),
            "location": device.get("location"),
            "energy_usage_kwh": safe_float(device.get("energy_usage_kwh")),
            "average_power_watt": safe_float(device.get("average_power_watt")),
            "max_power_watt": safe_float(device.get("max_power_watt")),
            "operating_hours": safe_float(device.get("operating_hours")),
            "previous_energy_usage_kwh": safe_float(device.get("previous_energy_usage_kwh")),
            "previous_average_power_watt": safe_float(device.get("previous_average_power_watt")),
            "previous_operating_hours": safe_float(device.get("previous_operating_hours"))
        }
        devices.append(device_data)

    output = {
        "customer_id": site_config.get("customer_id"),
        "customer_name": site_config.get("customer_name"),
        "report_period": datetime.now().strftime("%Y-%m"),
        "timezone": site_config.get("timezone"),

        "days_in_period": get_days_in_period(),
        "total_energy_usage_kwh": safe_float(entity_map.get("total_energy_usage_kwh")),
        "total_energy_cost_idr": round(safe_float(entity_map.get("total_energy_usage_kwh")) * 1699),

        "grid_import_kwh": safe_float(entity_map.get("grid_import_kwh")),
        "grid_export_kwh": safe_float(entity_map.get("grid_export_kwh")),
        "grid_import_cost_idr": round(safe_float(entity_map.get("grid_import_kwh")) * 1699),
        "grid_export_earning_idr": safe_float(entity_map.get("grid_export_kwh")) * 1699,
        
        "solar_production_kwh": safe_float(entity_map.get("solar_production_kwh")),
        "solar_to_home_kwh": safe_float(entity_map.get("solar_to_home_kwh")),
        "solar_to_grid_kwh": safe_float(entity_map.get("solar_to_grid_kwh")),

        "battery_charge_kwh": safe_float(entity_map.get("battery_charge_kwh")),
        "battery_discharge_kwh": safe_float(entity_map.get("battery_discharge_kwh")),
        "battery_net_kwh": safe_float(entity_map.get("battery_discharge_kwh")) - safe_float(entity_map.get("battery_charge_kwh")),

        "solar_system_capacity_kwp": site_config.get("solar_system_capacity_kwp"),
        "battery_capacity_kwh": site_config.get("battery_capacity_kwh"),
        "battery_average_soc_percent": safe_float(entity_map.get("battery_average_soc_percent")),
        "battery_cycles": round(safe_float(entity_map.get("battery_cycles")), 0),

        "devices": devices,
        "untracked_consumption_kwh": safe_float(entity_map.get("untracked_consumption_kwh")),
        "previous_untracked_consumption_kwh": safe_float(entity_map.get("previous_untracked_consumption_kwh")),

        # Add all arrays from energy_data
        **energy_data,

        "previous_period": get_previous_period(),
        "previous_total_energy_usage_kwh": safe_float(entity_map.get("previous_total_energy_usage_kwh")),
        "previous_total_energy_cost_idr": safe_float(entity_map.get("previous_total_energy_usage_kwh")) * 1700,
        "previous_grid_import_kwh": safe_float(entity_map.get("previous_grid_import_kwh")),
        "previous_solar_production_kwh": safe_float(entity_map.get("previous_solar_production_kwh")),
        "previous_battery_charge_kwh": safe_float(entity_map.get("previous_battery_charge_kwh")),

        "grid_co2_factor_kg_per_kwh": site_config.get("grid_co2_factor_kg_per_kwh", 0.85),
        "solar_co2_factor_kg_per_kwh": site_config.get("solar_co2_factor_kg_per_kwh", 0),
        "battery_round_trip_efficiency_percent": site_config.get("battery_round_trip_efficiency_percent", 90),
        "car_co2_per_km_kg": site_config.get("car_co2_per_km_kg", 0.2),
        "tree_absorption_kg_per_year": site_config.get("tree_absorption_kg_per_year", 21)
    }

    with open(f"energy_report_output_{args.site}.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"✅ energy_report_output_{args.site}.json berhasil dibuat.")

if __name__ == "__main__":
    asyncio.run(main())
