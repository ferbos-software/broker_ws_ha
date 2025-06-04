import asyncio
import websockets
import json
import argparse
from datetime import datetime, timezone
import sys
from flask import Flask, request, jsonify
import aiohttp
from datetime import timedelta # Import timedelta for time calculations

class HomeAssistantClient:
    def __init__(self, url, token):
        self.url = url
        self.token = token
        self.ws = None
        # Extract base URL for HTTP requests
        self.base_url = url.replace('ws://', 'http://').replace('wss://', 'https://').split('/api/')[0]

    async def connect(self):
        """Connect to Home Assistant WebSocket API"""
        try:
            # Add timeout and ping settings
            self.ws = await websockets.connect(
                self.url,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=20
            )
            print("WebSocket connection established.")
            
            # Wait for auth_required message
            response = await self.ws.recv()
            auth_required_response = json.loads(response)
            print(f"Received initial response: {auth_required_response}")

            if auth_required_response.get("type") != "auth_required":
                 raise Exception(f"Unexpected response during connection: {auth_required_response}")

            # Authenticate
            auth_message = {
                "type": "auth",
                "access_token": self.token
            }
            print("Sending authentication request...")
            await self.ws.send(json.dumps(auth_message))
            
            print("Waiting for authentication response...")
            response = await self.ws.recv()
            auth_response = json.loads(response)
            print(f"Auth response: {auth_response}")
            
            if auth_response.get("type") == "auth_ok":
                print("Authentication successful!")
            else:
                error_msg = auth_response.get("message", "Unknown error")
                print(f"Authentication failed. Response: {auth_response}")
                raise Exception(f"Authentication failed: {error_msg}")
                
        except websockets.exceptions.InvalidStatusCode as e:
            print(f"Error: Invalid status code {e.status_code}. Please check if Home Assistant is running and the URL is correct.")
            sys.exit(1)
        except websockets.exceptions.InvalidMessage as e:
            print(f"Error: Invalid WebSocket message: {str(e)}")
            sys.exit(1)
        except ConnectionRefusedError:
            print("Error: Connection refused. Please check if Home Assistant is running and the URL is correct.")
            sys.exit(1)
        except Exception as e:
            print(f"Error connecting to Home Assistant: {str(e)}")
            if hasattr(e, '__cause__') and e.__cause__:
                print(f"Caused by: {str(e.__cause__)}")
            sys.exit(1)

    async def get_entity_state(self, entity_id):
        """Get the state of an entity"""
        if not self.ws:
            # This connect call will now correctly handle the auth_required message
            await self.connect()

        try:
            print(f"Fetching state for entity: {entity_id}")
            
            # Get current state
            get_state_message = {
                "id": 2,
                "type": "get_states"
            }
            await self.ws.send(json.dumps(get_state_message))
            
            # Wait for response with timeout
            while True:
                try:
                    response = await asyncio.wait_for(self.ws.recv(), timeout=10.0)
                    data = json.loads(response)
                    print(f"Received data: {data}") # Add logging for received data
                    
                    # Process the initial result containing all states
                    if data.get("type") == "result" and data.get("id") == 2:
                        states = data.get("result")
                        if states is None:
                            print("Error: Received a result message with id 2, but no 'result' field found.")
                            return None

                        for state in states:
                            if state.get("entity_id") == entity_id:
                                # Return the raw state object directly
                                return state
                        # If we get here, the entity wasn't found in the initial list
                        print(f"Error: Entity '{entity_id}' not found in Home Assistant")
                        return None # Return None if entity not found in initial list

                    # Optionally handle state_changed events if needed, but for a single fetch,
                    # we only care about the initial 'result' with id 2.

                except asyncio.TimeoutError:
                    print("Error: Timeout waiting for response from Home Assistant")
                    # It's possible to receive other messages (like state_changed) while waiting
                    # for the result with id 2. Continue waiting unless a critical error occurs.
                    pass # Continue waiting
                except Exception as e:
                     print(f"Error receiving or processing message: {e}")
                     raise # Re-raise to be caught by the outer try...except

        except Exception as e:
            print(f"Error getting entity state: {str(e)}")
            if hasattr(e, '__cause__') and e.__cause__:
                print(f"Caused by: {str(e.__cause__)}")
            raise # Re-raise to be caught by the calling function

    async def close(self):
        """Close the WebSocket connection"""
        if self.ws:
            await self.ws.close()

    async def get_entity_history(self, entity_id, start_time, end_time):
        """Get historical state of an entity between start_time and end_time"""
        try:
            # Convert datetime objects to ISO format strings and ensure they're in UTC
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            if end_time and end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
                
            # Format timestamps in the format Home Assistant expects (without timezone offset)
            start_time_str = start_time.strftime("%Y-%m-%dT%H:%M:%S")
            end_time_str = end_time.strftime("%Y-%m-%dT%H:%M:%S") if end_time else None
            
            # Construct the history API URL
            history_url = f"{self.base_url}/api/history/period/{start_time_str}?filter_entity_id={entity_id}"
            if end_time_str:
                history_url += f"&end_time={end_time_str}"
            
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            }
            
            print(f"Requesting history from URL: {history_url}")  # Debug log
            
            async with aiohttp.ClientSession() as session:
                async with session.get(history_url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data
                    else:
                        error_text = await response.text()
                        print(f"Error response from server: {error_text}")  # Debug log
                        raise Exception(f"Failed to fetch history: {error_text}")
                        
        except Exception as e:
            print(f"Error getting entity history: {str(e)}")
            if hasattr(e, '__cause__') and e.__cause__:
                print(f"Caused by: {str(e.__cause__)}")
            raise

    async def get_monthly_data(self, entity_id, start_time, end_time):
        """Get monthly difference data for an entity between start_time and end_time"""
        try:
            # Determine the original timezone from the input start_time, defaulting to local if none.
            original_timezone = start_time.tzinfo if start_time.tzinfo else datetime.now(timezone.utc).astimezone().tzinfo

            # Convert input times to UTC for the API request, as HA History API expects UTC.
            start_time_utc = start_time.astimezone(timezone.utc)
            end_time_utc = end_time.astimezone(timezone.utc) if end_time else None

            # Request data with a small buffer to ensure we capture points near the boundaries.
            # Use a slightly larger buffer for robustness.
            request_start_time_utc = start_time_utc - timedelta(minutes=15) # Increased buffer
            request_end_time_utc = (end_time_utc + timedelta(minutes=15)) if end_time_utc else None # Increased buffer

            # Format timestamps in the format Home Assistant expects (UTC).
            # Use the original timezone times in the URL, as the API might expect local timezone.
            request_start_time_str = start_time.strftime("%Y-%m-%dT%H:%M:%S")
            request_end_time_str = end_time.strftime("%Y-%m-%dT%H:%M:%S") if end_time else None

            history_url = f"{self.base_url}/api/history/period/{request_start_time_str}?filter_entity_id={entity_id}"
            if request_end_time_str:
                history_url += f"&end_time={request_end_time_str}"

            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            }

            print(f"[get_monthly_data] Requesting monthly data from URL: {history_url}")
            print(f"[get_monthly_data] Original start time (input): {start_time.isoformat()}")
            print(f"[get_monthly_data] Original end time (input): {end_time.isoformat() if end_time else 'None'}")
            print(f"[get_monthly_data] Request start time (Original Timezone sent to API): {request_start_time_str}")
            print(f"[get_monthly_data] Request end time (Original Timezone sent to API): {request_end_time_str if request_end_time_str else 'None'}")

            async with aiohttp.ClientSession() as session:
                async with session.get(history_url, headers=headers) as response:
                    print(f"[get_monthly_data] API Response Status: {response.status}")
                    if response.status == 200:
                        data = await response.json()
                        print(f"[get_monthly_data] Received data length: {len(data) if data else 0}")

                        # The Home Assistant history API returns a list of lists, one list per entity.
                        # We expect a list containing a single list of data points for the requested entity.
                        if data and isinstance(data, list) and len(data) > 0 and isinstance(data[0], list) and len(data[0]) > 0:
                            history_data = data[0]
                            print(f"[get_monthly_data] History data points received: {len(history_data)}")

                            if not history_data:
                                print("[get_monthly_data] No history data points found in the requested period from API.")
                                # Return error data and status code
                                error_data = {"error": "No history data found for the specified time range."}
                                print(f"[get_monthly_data] Returning: {error_data}, 404")
                                return error_data, 404

                            # Log the raw timestamps of the first and last received data points
                            print(f"[get_monthly_data] Raw first data point timestamp (from API): {history_data[0].get('last_changed')}")
                            print(f"[get_monthly_data] Raw last data point timestamp (from API): {history_data[-1].get('last_changed')}")

                            # Convert all timestamps to the original input timezone for comparison and response.
                            processed_history_data = []
                            for point in history_data:
                                if 'last_changed' in point:
                                    try:
                                        point_time_utc = datetime.fromisoformat(point['last_changed'].replace('Z', '+00:00')).replace(tzinfo=timezone.utc)
                                        point_time_local = point_time_utc.astimezone(original_timezone)
                                        processed_history_data.append({
                                            **point,
                                            'datetime_local': point_time_local,
                                            'datetime_utc': point_time_utc # Keep UTC as well for debugging if needed
                                        })
                                    except (ValueError, KeyError, AttributeError) as e:
                                        print(f"[get_monthly_data] Error processing timestamp {point.get('last_changed')}: {e}")
                                        # Optionally skip points with invalid timestamps or return an error
                                        pass # Skip this point

                            if not processed_history_data:
                                print("[get_monthly_data] No processable history data points found after timezone conversion.")
                                error_data = {"error": "No processable history data found after timezone conversion."}
                                print(f"[get_monthly_data] Returning: {error_data}, 404")
                                return error_data, 404

                            # Find the first data point at or after the original start time
                            start_point = None
                            print(f"[get_monthly_data] Searching for start point >= {start_time.isoformat()} (in original timezone)")
                            for point in processed_history_data:
                                print(f"[get_monthly_data]   Comparing data point time {point['datetime_local'].isoformat()} with start time {start_time.isoformat()}")
                                if point['datetime_local'] >= start_time:
                                    start_point = point
                                    print(f"[get_monthly_data]   Found start point matching condition: {start_point['datetime_local'].isoformat()}")
                                    break

                            # Find the last data point at or before the original end time
                            end_point = None
                            if end_time:
                                print(f"[get_monthly_data] Searching for end point <= {end_time.isoformat()} (in original timezone)")
                                # Iterate in reverse to find the last point at or before end_time
                                for point in reversed(processed_history_data):
                                     print(f"[get_monthly_data]   Comparing data point time {point['datetime_local'].isoformat()} with end time {end_time.isoformat()}")
                                     if point['datetime_local'] <= end_time:
                                        end_point = point
                                        print(f"[get_monthly_data]   Found end point matching condition: {end_point['datetime_local'].isoformat()}")
                                        break
                            else:
                                # If no end time is provided, use the very last point in the retrieved data
                                end_point = processed_history_data[-1]
                                print(f"[get_monthly_data] No end time provided, using last retrieved point: {end_point['datetime_local'].isoformat()}")


                            # Fallback: If exact points not found within the range, use the first/last points
                            # of the *processed* data. This indicates the API might not have returned data precisely
                            # covering the requested range, even with the buffer.
                            if not start_point:
                                # Use the first processed point as fallback if no point found at or after start time
                                if processed_history_data:
                                    start_point = processed_history_data[0]
                                    print(f"[get_monthly_data] Warning: No point found exactly at or after start time within retrieved data. Using first retrieved point: {start_point['datetime_local'].isoformat()}")
                                else:
                                    # Should not happen if previous checks passed, but as safeguard
                                    print("[get_monthly_data] Error: No processed data points for fallback start point selection.")
                                    error_data = {"error": "Could not select a suitable start point."}
                                    print(f"[get_monthly_data] Returning: {error_data}, 500")
                                    return error_data, 500

                            if not end_point:
                                # Use the last processed point as fallback if no point found at or before end time
                                if processed_history_data:
                                    end_point = processed_history_data[-1]
                                    print(f"[get_monthly_data] Warning: No point found exactly at or before end time within retrieved data. Using last retrieved point: {end_point['datetime_local'].isoformat()}")
                                else:
                                    # Should not happen if previous checks passed, but as safeguard
                                    print("[get_monthly_data] Error: No processed data points for fallback end point selection.")
                                    error_data = {"error": "Could not select a suitable end point."}
                                    print(f"[get_monthly_data] Returning: {error_data}, 500")
                                    return error_data, 500


                            # Ensure start_point is chronologically before or at end_point, otherwise swap
                            if start_point['datetime_local'] > end_point['datetime_local']:
                                print("[get_monthly_data] Warning: Start point found is after end point. Swapping points.")
                                start_point, end_point = end_point, start_point


                            print(f"[get_monthly_data] Final selected start point (Original Timezone): {start_point['datetime_local'].isoformat()} with value {start_point.get('state')}")
                            print(f"[get_monthly_data] Final selected end point (Original Timezone): {end_point['datetime_local'].isoformat()} with value {end_point.get('state')}")

                            try:
                                # Get state values, handling potential None or non-numeric states
                                start_value_str = start_point.get('state')
                                end_value_str = end_point.get('state')

                                start_value = float(start_value_str) if start_value_str not in ['unknown', 'unavailable', None] else 0.0
                                end_value = float(end_value_str) if end_value_str not in ['unknown', 'unavailable', None] else 0.0

                                state = end_value - start_value
                                print(f"[get_monthly_data] Calculated values - Start: {start_value}, End: {end_value}, Difference: {state}")

                                # Create a summary response using the timezone-converted datetimes
                                summary = {
                                    "start_time": start_point['datetime_local'].isoformat(),
                                    "start_value": start_value,
                                    "end_time": end_point['datetime_local'].isoformat(),
                                    "end_value": end_value,
                                    "state": state,
                                    "unit": start_point.get('attributes', {}).get('unit_of_measurement', 'unknown') # Safely get unit
                                }
                                print(f"[get_monthly_data] Returning summary data: {summary}")
                                return summary, 200 # Return data and status code

                            except (ValueError, TypeError) as e:
                                print(f"[get_monthly_data] Error converting state values to float: {e}")
                                print(f"[get_monthly_data] Start point data: {start_point}")
                                print(f"[get_monthly_data] End point data: {end_point}")
                                # Return raw point data and error message in case of conversion error.
                                error_response_data = {
                                    "error": "Could not convert state values to float",
                                    "details": str(e),
                                    "start_point_data": start_point,
                                    "end_point_data": end_point
                                }
                                print(f"[get_monthly_data] Returning error data: {error_response_data}")
                                return error_response_data, 500 # Return data and status code

                        else:
                            print("[get_monthly_data] No data or unexpected data format received from API.")
                            error_data = {"error": "No data or unexpected data format received from API."}
                            print(f"[get_monthly_data] Returning: {error_data}, 404")
                            return error_data, 404 # Return data and status code
                    else:
                        error_text = await response.text()
                        print(f"[get_monthly_data] Error response from server: {response.status} - {error_text}")
                         # Return the error response from the API with its status code
                        error_response_data = {"error": f"Failed to fetch history data from API: {response.status} - {error_text}"}
                        print(f"[get_monthly_data] Returning API error data: {error_response_data}")
                        return error_response_data, response.status

        except Exception as e:
            print(f"[get_monthly_data] An unexpected error occurred: {str(e)}")
            if hasattr(e, '__cause__') and e.__cause__:
                print(f"[get_monthly_data] Caused by: {str(e.__cause__)}")
            # Return a generic internal server error message
            internal_error_response_data = {"error": f"An internal server error occurred: {str(e)}"}
            print(f"[get_monthly_data] Returning internal error data: {internal_error_response_data}")
            return internal_error_response_data, 500 # Return data and status code

async def get_entity_state_async(url, token, entity_id):
    """Helper function to fetch entity state asynchronously"""
    client = HomeAssistantClient(url, token)
    state = None
    try:
        await client.connect()
        state = await client.get_entity_state(entity_id)
    finally:
        # Ensure the connection is closed even if getting state failed
        await client.close()
    return state

async def get_entity_history_async(url, token, entity_id, start_time, end_time):
    """Helper function to fetch entity history asynchronously"""
    client = HomeAssistantClient(url, token)
    try:
        history = await client.get_entity_history(entity_id, start_time, end_time)
        return history
    finally:
        await client.close()

async def get_monthly_data_async(url, token, entity_id, start_time, end_time):
    """Helper function to fetch monthly data asynchronously and return data + status"""
    client = HomeAssistantClient(url, token)
    try:
        print("[get_monthly_data_async] Calling client.get_monthly_data")
        # HomeAssistantClient.get_monthly_data now returns (data_dict, status_code)
        data_dict, status_code = await client.get_monthly_data(entity_id, start_time, end_time)
        print(f"[get_monthly_data_async] client.get_monthly_data returned data_dict type: {type(data_dict)}, status_code: {status_code}")
        return data_dict, status_code
    finally:
        # Ensure the connection is closed
        print("[get_monthly_data_async] Closing client connection")
        await client.close()

app = Flask(__name__)

@app.route('/get_state', methods=['GET'])
def get_state():
    url = request.args.get('url')
    token = request.args.get('token')
    entity_id = request.args.get('entity_id')

    if not url or not token or not entity_id:
        return jsonify({"error": "Missing url, token, or entity_id parameter"}), 400

    # Validate URL format
    if not url.startswith(('ws://', 'wss://')):
        return jsonify({"error": "URL must start with ws:// or wss://"}), 400

    try:
        # Run the async function within a synchronous Flask route
        # Use a new event loop for each request to avoid conflicts
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        state = loop.run_until_complete(get_entity_state_async(url, token, entity_id))
        loop.close()
        asyncio.set_event_loop(None) # Reset event loop

        if state is not None:
            # Return the raw state object directly
            return jsonify(state), 200
        else:
            return jsonify({"error": f"Entity '{entity_id}' not found"}), 404

    except Exception as e:
        print(f"An error occurred in Flask route: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/get_history', methods=['GET'])
def get_history():
    url = request.args.get('url')
    token = request.args.get('token')
    entity_id = request.args.get('entity_id')
    start_time = request.args.get('start_time')
    end_time = request.args.get('end_time')

    if not all([url, token, entity_id, start_time]):
        return jsonify({"error": "Missing required parameters"}), 400

    try:
        # Parse datetime strings and ensure they're in UTC
        # Use the parse_datetime helper function to handle different formats
        def parse_datetime(dt_str):
            # Handle different timezone formats
            if ' ' in dt_str:
                # Split the string at the space and rejoin with proper format
                date_part, tz_part = dt_str.split(' ')
                dt_str = f"{date_part}+{tz_part}"
            elif 'Z' in dt_str:
                dt_str = dt_str.replace('Z', '+00:00')
            return datetime.fromisoformat(dt_str)

        start_dt = parse_datetime(start_time)
        end_dt = parse_datetime(end_time) if end_time else None

        print(f"Parsed start time: {start_dt}")  # Debug log
        print(f"Parsed end time: {end_dt}")  # Debug log

        # Run the async function within a synchronous Flask route
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        history = loop.run_until_complete(get_entity_history_async(url, token, entity_id, start_dt, end_dt))
        loop.close()
        asyncio.set_event_loop(None)

        if history:
            # Convert timestamps in the history data to the timezone of the start_time
            target_tzinfo = start_dt.tzinfo
            processed_history = []
            for entity_data in history:
                processed_entity_data = []
                for point in entity_data:
                    # Ensure timestamp is treated as UTC before converting
                    point_time_utc = datetime.fromisoformat(point['last_changed'].replace('Z', '+00:00')).replace(tzinfo=timezone.utc)
                    point_time_local = point_time_utc.astimezone(target_tzinfo)
                    point['last_changed'] = point_time_local.isoformat()
                    processed_entity_data.append(point)
                processed_history.append(processed_entity_data)

            return jsonify(processed_history), 200
        else:
            return jsonify({"error": f"No history found for entity '{entity_id}'"}), 404

    except Exception as e:
        print(f"An error occurred in Flask route: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/get_monthly_data', methods=['GET'])
def get_monthly_data():
    url = request.args.get('url')
    token = request.args.get('token')
    entity_id = request.args.get('entity_id')
    start_time = request.args.get('start_time')
    end_time = request.args.get('end_time')

    if not all([url, token, entity_id, start_time]):
        print("[route /get_monthly_data] Missing required parameters")
        return jsonify({"error": "Missing required parameters"}), 400

    try:
        # Parse datetime strings with timezone handling
        def parse_datetime(dt_str):
            # Handle different timezone formats
            if ' ' in dt_str:
                # Split the string at the space and rejoin with proper format
                date_part, tz_part = dt_str.split(' ')
                dt_str = f"{date_part}+{tz_part}"
            elif 'Z' in dt_str:
                dt_str = dt_str.replace('Z', '+00:00')
            
            # Attempt to parse with fromisoformat, which handles various ISO 8601 formats including timezone offsets
            try:
                return datetime.fromisoformat(dt_str)
            except ValueError as e:
                print(f"[route /get_monthly_data] Error parsing datetime string {dt_str}: {e}")
                raise ValueError(f"Invalid datetime format: {dt_str}") from e

        start_dt = parse_datetime(start_time)
        end_dt = parse_datetime(end_time) if end_time else None

        print(f"[route /get_monthly_data] Parsed start time: {start_dt.isoformat()}")
        print(f"[route /get_monthly_data] Parsed end time: {end_dt.isoformat() if end_dt else 'None'}")

        # Run the async function within a synchronous Flask route
        # Use a new event loop for each request to avoid conflicts
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            print("[route /get_monthly_data] Running async function")
            # The async function get_monthly_data_async now returns (data_dict, status_code)
            data_dict, status_code = loop.run_until_complete(get_monthly_data_async(url, token, entity_id, start_dt, end_dt))
            print(f"[route /get_monthly_data] Async function returned data_dict type: {type(data_dict)}, status_code: {status_code}")
            # Call jsonify on the data dictionary here
            response = jsonify(data_dict)
            print(f"[route /get_monthly_data] Returning jsonify response with status {status_code}")
            return response, status_code
        finally:
            loop.close()
            asyncio.set_event_loop(None) # Reset event loop

    except ValueError as e:
        print(f"[route /get_monthly_data] Datetime parsing error in Flask route: {e}")
        return jsonify({"error": str(e)}), 400 # Return 400 for bad request due to invalid time format
    except Exception as e:
        print(f"[route /get_monthly_data] An error occurred in Flask route calling async function: {e}")
        # This is a fallback for unexpected errors during async execution setup/completion.
        # Errors within the async function that are caught and returned as data+status
        # will be handled by the primary return path.
        return jsonify({"error": f"An unexpected error occurred processing your request: {str(e)}"}), 500

async def run_cli_mode():
    parser = argparse.ArgumentParser(description='Home Assistant WebSocket Client (CLI Mode)')
    parser.add_argument('url', help='Home Assistant WebSocket URL (e.g., ws://localhost:8123/api/websocket)')
    parser.add_argument('token', help='Long-lived access token')
    parser.add_argument('entity_id', help='Entity ID to fetch (e.g., sensor.temperature)')
    
    args = parser.parse_args(sys.argv[1:]) # Parse arguments excluding the script name
    
    # Validate URL format
    if not args.url.startswith(('ws://', 'wss://')):
        print("Error: URL must start with ws:// or wss://")
        sys.exit(1)
    
    print(f"Connecting to Home Assistant at: {args.url}")
    client = HomeAssistantClient(args.url, args.token)
    try:
        state = await client.get_entity_state(args.entity_id)
        if state:
            print(json.dumps(state, indent=2))
        else:
             sys.exit(1) # Exit with error code if entity not found
    finally:
        # Ensure the connection is closed even if getting state failed
        await client.close()

if __name__ == "__main__":
    if '--server' in sys.argv:
        # Remove --server from sys.argv so Flask's parser doesn't complain
        sys.argv.remove('--server')
        
        parser = argparse.ArgumentParser(description='Home Assistant WebSocket Client (Server Mode)')
        parser.add_argument('--host', default='127.0.0.1', help='Host for Flask server (default: 127.0.0.1)')
        parser.add_argument('--port', type=int, default=5000, help='Port for Flask server (default: 5000)')
        
        args = parser.parse_args()

        print(f"Starting Flask server on http://{args.host}:{args.port}/")
        # Use app.run for development server. For production, use a WSGI server like Gunicorn.
        # debug=True should only be used during development
        app.run(debug=True, host=args.host, port=args.port)
    else:
        # Default to CLI mode
        asyncio.run(run_cli_mode()) 