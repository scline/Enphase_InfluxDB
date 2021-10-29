import os, sys, time, logging, datetime, requests, configargparse
from requests.auth import HTTPDigestAuth
from influxdb import InfluxDBClient

# Load configuration file and settings
p = configargparse.ArgParser()
p.add('--enphase-host', required=True, help='Hostname or IP address of Enphase monitor', env_var='ENPHASE_HOST')
p.add('--enphase-user', required=False, help='Enphase API local user, default is "envoy"', env_var='ENPHASE_USER')
p.add('--enphase-password', required=True, help='LocalAPI Password, normally last 6 of serial number', env_var='ENPHASE_PASS')
p.add('--influxdb-host', required=True, help='InfluxDB Hostname', env_var='INFLUXDB_HOST')
p.add('--influxdb-port', required=False, type=int, help='InfluxDB Port', env_var='INFLUXDB_PORT')
p.add('--influxdb-name', required=False, help='InfluxDB Name, defaults to "enphase"', env_var='INFLUXDB_NAME')
p.add('-v', '--verbose', help='verbose logging', action='store_true', env_var='DEBUG')

config = p.parse_args()

# Set defaults
if not config.enphase_user:
    config.enphase_user = "envoy"
if not config.influxdb_port:
    config.influxdb_port = 8086
if not config.influxdb_name:
    config.influxdb_name = "enphase"

# Set logging levels, if anything is assigned to env variable 'DEBUG' then this is true.
if config.verbose:
    logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", encoding='utf-8', level=logging.DEBUG)
else:
    logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", encoding='utf-8', level=logging.INFO)


# API call to gather CT Clamp readouts
def ct(api_host):
    # Create URL with hostname provided
    url = "http://%s/production.json" % api_host

    # HTTP API Request
    try:
        r = requests.get(url, timeout=30)
    # Uh oh, something went wrong
    except Exception as e:
        logging.error("Unable to GET from '%s'" % url)
        logging.error(e)

        # We will handle None as an error within main()
        return None
    r.close()

    # Basic error checking on return code
    if r.status_code != 200:
        logging.error("'%s' returned unknown error, status_code: '%s'" % (url, r.status_code))
        logging.debug("%s'" % r.text())

        # We will handle None as an error within main()
        return None
    # Status code 200
    else:
        logging.debug(r.json())
        return r.json()


# API Call to gather per-panel metrics 
def inverters(api_host, api_user, api_pass):
    # Create API URL with hostname provided
    url = "http://%s/api/v1/production/inverters" % api_host

    # HTTP API Request, long timeout since it appears Envoy system throttles data requists
    try:
        r = requests.get(url, auth=HTTPDigestAuth(api_user, api_pass), timeout=30)

    # Uh oh, something went wrong
    except Exception as e:
        logging.error("Unable to GET from '%s'" % url)
        logging.error(e)

        # We will handle None as an error within main()
        return None
    r.close()

    # Log 401 errors
    if r.status_code == 401:
        logging.error("'%s' reports incorrect password was given for user 'envoy'" % (url))
        logging.debug("%s'" % r.json())

        # We will handle None as an error within main()
        return None

    # If status is anything but 200
    elif r.status_code != 200:
        logging.error("'%s' returned unknown error, status_code: '%s'" % (url, r.status_code))
        logging.debug("%s'" % r.text())

        # We will handle None as an error within main()
        return None

    # If status is 200(OK!)
    else:
        # Return the results to main, should be a list of dictionaries via JSON
        """
        [
            {
                "serialNumber": "122102008624",
                "lastReportDate": 1635382224,
                "devType": 1,
                "lastReportWatts": 0,
                "maxReportWatts": 348
            }
        ]
        """
        logging.debug(r.json())
        return r.json()


# Data structure for InfluxDB, used for 'lastReportWatts' and 'maxReportWatts'
def inverter_log_point(metric):
    return {
        "measurement": "inverter",
        "tags": {
            "serialNumber": metric['serialNumber'],
            "devType": metric['devType']
        },
        "fields": {
            "lastReportWatts": metric['lastReportWatts'],
            "maxReportWatts": metric['maxReportWatts']
        }
    }


# Data structure for InfluxDB, used for 'inverters' type CT data
def ct_inverter_log_point(metric):
    return {
        "measurement": "ct",
        "tags": {
            "type": metric['type']
        },
        "fields": {
            "activeCount": metric['activeCount'],
            "wNow": metric['wNow'],
            "whLifetime": metric['whLifetime']
        }
    }


# Data structure for InfluxDB, used for 'EIM' type CT data
def eim_log_point(metric):
    return {
        "measurement": "ct",
        "tags": {
            "type": metric['type'],
            "measurementType": metric['measurementType']
        },
        "fields": {
            "activeCount": metric['activeCount'],
            "wNow": float(metric['wNow']),
            "whLifetime": float(metric['whLifetime']),
            "varhLeadLifetime": float(metric['varhLeadLifetime']),
            "varhLagLifetime": float(metric['varhLagLifetime']),
            "vahLifetime": float(metric['vahLifetime']),
            "rmsCurrent": float(metric['rmsCurrent']),
            "rmsVoltage": float(metric['rmsVoltage']),
            "reactPwr": float(metric['reactPwr']),
            "apprntPwr": float(metric['apprntPwr']),
            "whToday": float(metric['whToday']),
            "whLastSevenDays": float(metric['whLastSevenDays']),
            "vahToday": float(metric['vahToday']),
            "varhLeadToday": float(metric['varhLeadToday']),
            "varhLagToday": float(metric['varhLagToday'])
        }
    }


# Write influxDB Data
def write_influx(config, points):
    # Setup InfluxDB client
    client = InfluxDBClient(host=config.influxdb_host, port=config.influxdb_port, database=config.influxdb_name, verify_ssl=False)

    # Attempt to write metrics to InfluxDB
    try:
        client.write_points(points)

    # Catch errors and log
    except Exception as e:
        logging.error("Error writing to InfluxDB")
        logging.error("Host: %s, Port: %s, Database: %s" % (config.influxdb_host, config.influxdb_port, config.influxdb_name))
        logging.error(e)

        # Handle in main if errored
        return False
    
    # Log how mant metrics we wrote
    logging.info("Wrote %i metrics to influxDB" % len(points))

    # Return true if we thing everything worked
    return True


def setup_influx(config):
    # Setup InfluxDB client
    client = InfluxDBClient(host=config.influxdb_host, port=config.influxdb_port, database=config.influxdb_name, verify_ssl=False)

    # Create database if it does not exsist
    try:
        client.create_database(config.influxdb_name)
    except Exception as e:
        logging.error("Error creating InfluxDB Database, please verify one exsists")
        logging.error(e)

    return


def main():
    # Some basic logging
    logging.info("Enphase Metrics Collector Started...")
    logging.debug(config)

    # Basic non-looped setup
    setup_influx(config)

    # Main gathering loop
    while True:
        # Cleanup variables for new run
        points = []
        inverter_output = None
        metric = None

        logging.debug("Sleeping for 30 seconds")
        time.sleep(30)

        # Pull inverter data, output is a list of dictionaries
        inverter_output = inverters(config.enphase_host, config.enphase_user, config.enphase_password)

        # If we have a value, process data for influxDB, 'lastReportWatts' and 'maxReportWatts' metrics
        if inverter_output is not None:
            for metric in inverter_output:
                points.append(inverter_log_point(metric))

            # Log the number of panels we got information on
            logging.info("Collected stats from %i solar panels" % len(inverter_output))

        # If None then something went wrong during data collection. Log error and move on.
        else:
            logging.warning("Was not able to collect per panel metrics this cycle")

        logging.debug("Sleeping for 30 seconds")
        time.sleep(30)

        # Pull CT metrics data, output is a bunch of JSON that needs processing
        ct_output = ct(config.enphase_host)

        # If we have a value, process data for influxDB
        if ct_output is not None:
            for k in ct_output:
                for metric in ct_output[k]:
                    # Format metrics to 'eim' type
                    if metric['type'] == "eim":
                        points.append(eim_log_point(metric))
                        # Log that metrics were collected
                        logging.info("Collected metrics from CT-EIM: '%s'" % metric['measurementType'])
                    if metric['type'] == 'inverters':
                        points.append(ct_inverter_log_point(metric))
                        # Log that metrics were collected
                        logging.info("Collected metrics from CT-Inverters: '%s'" % metric['type'])      

        # If None then something went wrong during data collection. Log error and move on.
        else:
            logging.warning("Was not able to collect CT metrics this cycle")
    
        # If there are any metrics, write them to Influx
        if len(points) > 0:
            logging.debug(points)
            # Write metrics to InfluxDB
            write_influx(config, points)
        else:
            logging.error("No metrics to write this cycle, are we pulling data?")


# Start Program
if __name__ == "__main__":
    main()
