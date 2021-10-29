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
def inverter_log_point(measurement=None, reportdate=None, serial=None, devtype=None, value=None):
    return {
        "measurement": measurement,
        # TODO: Timezone needs to be UTC regardless of server settings
        #"time": datetime.datetime.fromtimestamp(reportdate).utcnow().strftime ("%Y-%m-%d %H:%M:%S"),
        "tags": {
            "serialNumber": serial,
            "devType": devtype
        },
        "fields": {
            "panel_metric": value
        }
    }


# Data structure for InfluxDB, used for 'EIM' type CT data
def eim_log_point(measurement=None, reading_time=None, measurement_type=None, value=None):
    return {
        "measurement": measurement,
        # TODO: Timezone needs to be UTC regardless of server settings
        #"time": datetime.datetime.fromtimestamp(reading_time).strftime ("%Y-%m-%d %H:%M:%S"),
        "tags": {
            "type": "eim",
            "measurementType": measurement_type
        },
        "fields": {
            "ct_metric": float(value)
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


def main():
    # Some basic logging
    logging.info("Enphase Metrics Collector Started...")
    logging.debug(config)

    # Main gathering loop
    while True:
        # Cleanup variables for new run
        points = []
        inverter_output = None

        logging.debug("Sleeping for 30 seconds")
        time.sleep(30)

        # Pull inverter data, output is a list of dictionaries
        inverter_output = inverters(config.enphase_host, config.enphase_user, config.enphase_password)

        # If we have a value, process data for influxDB, 'lastReportWatts' and 'maxReportWatts' metrics
        if inverter_output is not None:
            for panel in inverter_output:
                points.append(inverter_log_point("lastReportWatts", panel['lastReportDate'], panel['serialNumber'], panel['devType'], panel['lastReportWatts']))
                points.append(inverter_log_point("maxReportWatts", panel['lastReportDate'], panel['serialNumber'], panel['devType'], panel['maxReportWatts']))

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
                #logging.debug(k)
                for v in ct_output[k]:
                    #logging.debug(v)
                    # Format metrics to 'eim' type
                    if v['type'] == "eim":
                        points.append(eim_log_point("activeCount", v['readingTime'], v['measurementType'], v['activeCount']))
                        points.append(eim_log_point("wNow", v['readingTime'], v['measurementType'], v['wNow']))
                        points.append(eim_log_point("whLifetime", v['readingTime'], v['measurementType'], v['whLifetime']))
                        points.append(eim_log_point("varhLeadLifetime", v['readingTime'], v['measurementType'], v['varhLeadLifetime']))
                        points.append(eim_log_point("varhLagLifetime", v['readingTime'], v['measurementType'], v['varhLagLifetime']))
                        points.append(eim_log_point("vahLifetime", v['readingTime'], v['measurementType'], v['vahLifetime']))
                        points.append(eim_log_point("rmsCurrent", v['readingTime'], v['measurementType'], v['rmsCurrent']))
                        points.append(eim_log_point("rmsVoltage", v['readingTime'], v['measurementType'], v['rmsVoltage']))
                        points.append(eim_log_point("reactPwr", v['readingTime'], v['measurementType'], v['reactPwr']))
                        points.append(eim_log_point("apprntPwr", v['readingTime'], v['measurementType'], v['apprntPwr']))
                        points.append(eim_log_point("pwrFactor", v['readingTime'], v['measurementType'], v['pwrFactor']))
                        points.append(eim_log_point("whToday", v['readingTime'], v['measurementType'], v['whToday']))
                        points.append(eim_log_point("whLastSevenDays", v['readingTime'], v['measurementType'], v['whLastSevenDays']))
                        points.append(eim_log_point("vahToday", v['readingTime'], v['measurementType'], v['vahToday']))
                        points.append(eim_log_point("varhLeadToday", v['readingTime'], v['measurementType'], v['varhLeadToday']))
                        points.append(eim_log_point("varhLagToday", v['readingTime'], v['measurementType'], v['varhLagToday']))  

                        # Log that metrics were collected
                        logging.info("Collected metrics from EIM: '%s'" % v['measurementType'])

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

