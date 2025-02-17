# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.



import json
import logging
import threading
from shapely.geometry import Polygon
from datetime import datetime
from itertools import chain
from math import cos, radians
from dataclasses import dataclass

import numpy as np
import pyproj
import requests
from pytz import timezone, UTC
from scipy import spatial
from shapely import wkt
from shapely.geometry import Point
from shapely.geometry import box
from shapely.geos import WKTReadingError

from webservice.NexusHandler import nexus_handler
from webservice.algorithms_spark.NexusCalcSparkHandler import NexusCalcSparkHandler
from webservice.algorithms.doms import config as edge_endpoints
from webservice.algorithms.doms import values as doms_values
from webservice.algorithms.doms.BaseDomsHandler import DomsQueryResults
from webservice.algorithms.doms.ResultsStorage import ResultsStorage
from webservice.webmodel import NexusProcessingException

EPOCH = timezone('UTC').localize(datetime(1970, 1, 1))
ISO_8601 = '%Y-%m-%dT%H:%M:%S%z'


def iso_time_to_epoch(str_time):
    return (datetime.strptime(str_time, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=UTC) - EPOCH).total_seconds()


@nexus_handler
class Matchup(NexusCalcSparkHandler):
    name = "Matchup"
    path = "/match_spark"
    description = "Match measurements between two or more datasets"

    params = {
        "primary": {
            "name": "Primary Dataset",
            "type": "string",
            "description": "The Primary dataset used to find matches for. Required"
        },
        "secondary": {
            "name": "Match-Up Datasets",
            "type": "comma-delimited string",
            "description": "The Dataset(s) being searched for measurements that match the Primary. Required"
        },
        "parameter": {
            "name": "Match-Up Parameter",
            "type": "string",
            "description": "The parameter of interest used for the match up. One of 'sst', 'sss', 'wind'. Optional"
        },
        "startTime": {
            "name": "Start Time",
            "type": "string",
            "description": "Starting time in format YYYY-MM-DDTHH:mm:ssZ or seconds since EPOCH. Required"
        },
        "endTime": {
            "name": "End Time",
            "type": "string",
            "description": "Ending time in format YYYY-MM-DDTHH:mm:ssZ or seconds since EPOCH. Required"
        },
        "b": {
            "name": "Bounding box",
            "type": "comma-delimited float",
            "description": "Minimum (Western) Longitude, Minimum (Southern) Latitude, "
                           "Maximum (Eastern) Longitude, Maximum (Northern) Latitude. Required"
        },
        "depthMin": {
            "name": "Minimum Depth",
            "type": "float",
            "description": "Minimum depth of measurements. Must be less than depthMax. Optional. Default: no limit"
        },
        "depthMax": {
            "name": "Maximum Depth",
            "type": "float",
            "description": "Maximum depth of measurements. Must be greater than depthMin. Optional. Default: no limit"
        },
        "tt": {
            "name": "Time Tolerance",
            "type": "long",
            "description": "Tolerance in time (seconds) when comparing two measurements. Optional. Default: 86400"
        },
        "rt": {
            "name": "Radius Tolerance",
            "type": "float",
            "description": "Tolerance in radius (meters) when comparing two measurements. Optional. Default: 1000"
        },
        "platforms": {
            "name": "Platforms",
            "type": "comma-delimited integer",
            "description": "Platforms to include for matchup consideration. Required"
        },
        "matchOnce": {
            "name": "Match Once",
            "type": "boolean",
            "description": "Optional True/False flag used to determine if more than one match per primary point is returned. "
                           + "If true, only the nearest point will be returned for each primary point. "
                           + "If false, all points within the tolerances will be returned for each primary point. Default: False"
        },
        "resultSizeLimit": {
            "name": "Result Size Limit",
            "type": "int",
            "description": "Optional integer value that limits the number of results returned from the matchup. "
                           "If the number of primary matches is greater than this limit, the service will respond with "
                           "(HTTP 202: Accepted) and an empty response body. A value of 0 means return all results. "
                           "Default: 500"
        }
    }
    singleton = True

    def __init__(self, algorithm_config=None, sc=None, tile_service_factory=None, config=None):
        NexusCalcSparkHandler.__init__(self, algorithm_config=algorithm_config, sc=sc, tile_service_factory=tile_service_factory)
        self.log = logging.getLogger(__name__)
        self.tile_service_factory = tile_service_factory
        self.config = config

    def parse_arguments(self, request):
        # Parse input arguments
        self.log.debug("Parsing arguments")
        try:
            bounding_polygon = request.get_bounding_polygon()
        except:
            raise NexusProcessingException(
                reason="'b' argument is required. Must be comma-delimited float formatted as Minimum (Western) Longitude, Minimum (Southern) Latitude, Maximum (Eastern) Longitude, Maximum (Northern) Latitude",
                code=400)
        primary_ds_name = request.get_argument('primary', None)
        if primary_ds_name is None:
            raise NexusProcessingException(reason="'primary' argument is required", code=400)
        secondary_ds_names = request.get_argument('secondary', None)
        if secondary_ds_names is None:
            raise NexusProcessingException(reason="'secondary' argument is required", code=400)

        parameter_s = request.get_argument('parameter')
        if parameter_s and parameter_s not in ['sst', 'sss', 'wind']:
            raise NexusProcessingException(
                reason="Parameter %s not supported. Must be one of 'sst', 'sss', 'wind'." % parameter_s, code=400)

        try:
            start_time = request.get_start_datetime()
        except:
            raise NexusProcessingException(
                reason="'startTime' argument is required. Can be int value seconds from epoch or string format YYYY-MM-DDTHH:mm:ssZ",
                code=400)
        try:
            end_time = request.get_end_datetime()
        except:
            raise NexusProcessingException(
                reason="'endTime' argument is required. Can be int value seconds from epoch or string format YYYY-MM-DDTHH:mm:ssZ",
                code=400)

        if start_time > end_time:
            raise NexusProcessingException(
                reason="The starting time must be before the ending time. Received startTime: %s, endTime: %s" % (
                    request.get_start_datetime().strftime(ISO_8601), request.get_end_datetime().strftime(ISO_8601)),
                code=400)

        depth_min = request.get_decimal_arg('depthMin', default=None)
        depth_max = request.get_decimal_arg('depthMax', default=None)

        if depth_min is not None and depth_max is not None and depth_min >= depth_max:
            raise NexusProcessingException(
                reason="Depth Min should be less than Depth Max", code=400)

        time_tolerance = request.get_int_arg('tt', default=86400)
        radius_tolerance = request.get_decimal_arg('rt', default=1000.0)
        platforms = request.get_argument('platforms', None)
        if platforms is None:
            raise NexusProcessingException(reason="'platforms' argument is required", code=400)

        match_once = request.get_boolean_arg("matchOnce", default=False)

        result_size_limit = request.get_int_arg("resultSizeLimit", default=500)

        start_seconds_from_epoch = int((start_time - EPOCH).total_seconds())
        end_seconds_from_epoch = int((end_time - EPOCH).total_seconds())

        return bounding_polygon, primary_ds_name, secondary_ds_names, parameter_s, \
               start_time, start_seconds_from_epoch, end_time, end_seconds_from_epoch, \
               depth_min, depth_max, time_tolerance, radius_tolerance, \
               platforms, match_once, result_size_limit

    def calc(self, request, **args):
        start = datetime.utcnow()
        # TODO Assuming Satellite primary
        bounding_polygon, primary_ds_name, secondary_ds_names, parameter_s, \
        start_time, start_seconds_from_epoch, end_time, end_seconds_from_epoch, \
        depth_min, depth_max, time_tolerance, radius_tolerance, \
        platforms, match_once, result_size_limit = self.parse_arguments(request)

        with ResultsStorage(self.config) as resultsStorage:

            execution_id = str(resultsStorage.insertExecution(None, start, None, None))

        self.log.debug("Querying for tiles in search domain")
        # Get tile ids in box
        tile_ids = [tile.tile_id for tile in
                    self._get_tile_service().find_tiles_in_polygon(bounding_polygon, primary_ds_name,
                                                             start_seconds_from_epoch, end_seconds_from_epoch,
                                                             fetch_data=False, fl='id',
                                                             sort=['tile_min_time_dt asc', 'tile_min_lon asc',
                                                                   'tile_min_lat asc'], rows=5000)]

        self.log.info('Found %s tile_ids', len(tile_ids))
        # Call spark_matchup
        self.log.debug("Calling Spark Driver")
        try:
            spark_result = spark_matchup_driver(tile_ids, wkt.dumps(bounding_polygon), primary_ds_name,
                                                secondary_ds_names, parameter_s, depth_min, depth_max, time_tolerance,
                                                radius_tolerance, platforms, match_once, self.tile_service_factory, sc=self._sc)
        except Exception as e:
            self.log.exception(e)
            raise NexusProcessingException(reason="An unknown error occurred while computing matches", code=500)

        end = datetime.utcnow()

        self.log.debug("Building and saving results")
        args = {
            "primary": primary_ds_name,
            "matchup": secondary_ds_names,
            "startTime": start_time,
            "endTime": end_time,
            "bbox": request.get_argument('b'),
            "timeTolerance": time_tolerance,
            "radiusTolerance": float(radius_tolerance),
            "platforms": platforms,
            "parameter": parameter_s
        }

        if depth_min is not None:
            args["depthMin"] = float(depth_min)

        if depth_max is not None:
            args["depthMax"] = float(depth_max)

        total_keys = len(list(spark_result.keys()))
        total_values = sum(len(v) for v in spark_result.values())
        details = {
            "timeToComplete": int((end - start).total_seconds()),
            "numInSituRecords": 0,
            "numInSituMatched": total_values,
            "numGriddedChecked": 0,
            "numGriddedMatched": total_keys
        }

        matches = Matchup.convert_to_matches(spark_result)

        def do_result_insert():
            with ResultsStorage(self.config) as storage:
                storage.insertResults(results=matches, params=args, stats=details,
                                      startTime=start, completeTime=end, userEmail="",
                                      execution_id=execution_id)

        threading.Thread(target=do_result_insert).start()

        if 0 < result_size_limit < len(matches):
            result = DomsQueryResults(results=None, args=args, details=details, bounds=None, count=None,
                                      computeOptions=None, executionId=execution_id, status_code=202)
        else:
            result = DomsQueryResults(results=matches, args=args, details=details, bounds=None, count=None,
                                      computeOptions=None, executionId=execution_id)

        return result

    @classmethod
    def convert_to_matches(cls, spark_result):
        matches = []
        for primary_domspoint, matched_domspoints in spark_result.items():
            p_matched = [cls.domspoint_to_dict(p_match, 'secondary') for p_match in matched_domspoints]

            primary = cls.domspoint_to_dict(primary_domspoint, 'primary')
            primary['matches'] = list(p_matched)
            matches.append(primary)
        return matches

    @staticmethod
    def domspoint_to_dict(domspoint, data_key_name='data'):
        doms_dict = {
            "platform": doms_values.getPlatformById(domspoint.platform),
            "device": doms_values.getDeviceById(domspoint.device),
            "lon": str(domspoint.longitude),
            "lat": str(domspoint.latitude),
            "point": "Point(%s %s)" % (domspoint.longitude, domspoint.latitude),
            "time": datetime.strptime(domspoint.time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC),
            "fileurl": domspoint.file_url,
            "id": domspoint.data_id,
            "source": domspoint.source,
            data_key_name: [data_point.__dict__ for data_point in domspoint.data]
        }
        return doms_dict


@dataclass
class DataPoint:
    """
    Represents a single point of data. This is used to construct the
    output of the matchup algorithm.

    :attribute variable_name: The name of the NetCDF variable.
    :attribute cf_variable_name: The CF standard_name of the
    NetCDF variable. This will be None if the standard_name does not
    exist in the source data file.
    :attribute variable_value: value at some point for the given
        variable.
    """
    variable_name: str = None
    cf_variable_name: str = None
    variable_value: float = None


class DomsPoint(object):
    def __init__(self, longitude=None, latitude=None, time=None, depth=None, data_id=None):

        self.time = time
        self.longitude = longitude
        self.latitude = latitude
        self.depth = depth
        self.data_id = data_id

        self.data = None

        self.source = None
        self.depth = None
        self.platform = None
        self.device = None
        self.file_url = None

    def __repr__(self):
        return str(self.__dict__)

    @staticmethod
    def _variables_to_device(variables):
        """
        Given a list of science variables, attempt to determine what
        the correct device is. This method will only be used for
        satellite measurements, so the only options are 'scatterometers'
        or 'radiometers'

        :param variables: List of variable names
        :return: device id integer
        """
        for variable in variables:
            if 'wind' in variable.variable_name.lower():
                # scatterometers
                return 6
        # Assume radiometers
        return 5

    @staticmethod
    def from_nexus_point(nexus_point, tile=None):
        point = DomsPoint()

        point.data_id = "%s[%s]" % (tile.tile_id, nexus_point.index)

        if tile.is_multi:
            data_vals = nexus_point.data_vals
        else:
            data_vals = [nexus_point.data_vals]

        data = []
        for data_val, variable in zip(data_vals, tile.variables):
            if data_val:
                data.append(DataPoint(
                    variable_name=variable.variable_name,
                    variable_value=data_val,
                    cf_variable_name=variable.standard_name
                ))
        point.data = data

        try:
            point.wind_v = tile.meta_data['wind_v'][tuple(nexus_point.index)].item()
        except (KeyError, IndexError):
            pass
        try:
            point.wind_direction = tile.meta_data['wind_dir'][tuple(nexus_point.index)].item()
        except (KeyError, IndexError):
            pass
        try:
            point.wind_speed = tile.meta_data['wind_speed'][tuple(nexus_point.index)].item()
        except (KeyError, IndexError):
            pass

        point.longitude = nexus_point.longitude.item()
        point.latitude = nexus_point.latitude.item()

        point.time = datetime.utcfromtimestamp(nexus_point.time).strftime('%Y-%m-%dT%H:%M:%SZ')

        try:
            point.depth = nexus_point.depth
        except KeyError:
            # No depth associated with this measurement
            pass

        point.sst_depth = 0
        point.source = tile.dataset
        point.file_url = tile.granule

        point.platform = 9
        point.device = DomsPoint._variables_to_device(tile.variables)
        return point

    @staticmethod
    def from_edge_point(edge_point):
        point = DomsPoint()
        x, y = edge_point['longitude'], edge_point['latitude']

        point.longitude = x
        point.latitude = y

        point.time = edge_point['time']

        point.source = edge_point.get('source')
        point.platform = edge_point.get('platform')
        point.device = edge_point.get('device')
        point.file_url = edge_point.get('fileurl')

        if 'code' in point.platform:
            point.platform = edge_point.get('platform')['code']

        data_fields = [
            'air_pressure',
            'air_pressure_quality',
            'air_temperature',
            'air_temperature_quality',
            'dew_point_temperature',
            'dew_point_temperature_quality',
            'downwelling_longwave_flux_in_air',
            'downwelling_longwave_flux_in_air_quality',
            'downwelling_longwave_radiance_in_air',
            'downwelling_longwave_radiance_in_air_quality',
            'downwelling_shortwave_flux_in_air',
            'downwelling_shortwave_flux_in_air_quality',
            'mass_concentration_of_chlorophyll_in_sea_water',
            'mass_concentration_of_chlorophyll_in_sea_water_quality',
            'rainfall_rate',
            'rainfall_rate_quality',
            'relative_humidity',
            'relative_humidity_quality',
            'sea_surface_salinity',
            'sea_surface_salinity_quality',
            'sea_surface_skin_temperature',
            'sea_surface_skin_temperature_quality',
            'sea_surface_subskin_temperature',
            'sea_surface_subskin_temperature_quality',
            'sea_surface_temperature',
            'sea_surface_temperature_quality',
            'sea_water_density',
            'sea_water_density_quality',
            'sea_water_electrical_conductivity',
            'sea_water_electrical_conductivity_quality',
            'sea_water_practical_salinity',
            'sea_water_practical_salinity_quality',
            'sea_water_salinity',
            'sea_water_salinity_quality',
            'sea_water_temperature',
            'sea_water_temperature_quality',
            'surface_downwelling_photosynthetic_photon_flux_in_air',
            'surface_downwelling_photosynthetic_photon_flux_in_air_quality',
            'wet_bulb_temperature',
            'wet_bulb_temperature_quality',
            'wind_speed',
            'wind_speed_quality',
            'wind_from_direction',
            'wind_from_direction_quality',
            'wind_to_direction',
            'wind_to_direction_quality',
            'eastward_wind',
            'northward_wind',
            'wind_component_quality'
        ]
        data = []
        # This is for in-situ secondary points
        for name in data_fields:
            val = edge_point.get(name)
            if val:
                data.append(DataPoint(
                    variable_name=name,
                    variable_value=val
                ))


        # This is for satellite secondary points
        if 'variables' in edge_point:

            data.extend([DataPoint(
                variable_name=variable.variable_name,
                variable_value=var_value,
                cf_variable_name=variable.standard_name
            ) for var_value, variable in zip(
                edge_point['var_values'],
                edge_point['variables']
            ) if var_value])
        point.data = data

        try:
            point.data_id = str(edge_point['id'])
        except KeyError:
            point.data_id = "%s:%s:%s" % (point.time, point.longitude, point.latitude)

        return point


from threading import Lock

DRIVER_LOCK = Lock()


def spark_matchup_driver(tile_ids, bounding_wkt, primary_ds_name, secondary_ds_names, parameter, depth_min, depth_max,
                         time_tolerance, radius_tolerance, platforms, match_once, tile_service_factory, sc=None):
    from functools import partial

    with DRIVER_LOCK:
        # Broadcast parameters
        primary_b = sc.broadcast(primary_ds_name)
        secondary_b = sc.broadcast(secondary_ds_names)
        depth_min_b = sc.broadcast(float(depth_min) if depth_min is not None else None)
        depth_max_b = sc.broadcast(float(depth_max) if depth_max is not None else None)
        tt_b = sc.broadcast(time_tolerance)
        rt_b = sc.broadcast(float(radius_tolerance))
        platforms_b = sc.broadcast(platforms)
        bounding_wkt_b = sc.broadcast(bounding_wkt)
        parameter_b = sc.broadcast(parameter)

        # Parallelize list of tile ids
        rdd = sc.parallelize(tile_ids, determine_parllelism(len(tile_ids)))

    # Map Partitions ( list(tile_id) )
    rdd_filtered = rdd.mapPartitions(
        partial(
            match_satellite_to_insitu,
            primary_b=primary_b,
            secondary_b=secondary_b,
            parameter_b=parameter_b,
            tt_b=tt_b,
            rt_b=rt_b,
            platforms_b=platforms_b,
            bounding_wkt_b=bounding_wkt_b,
            depth_min_b=depth_min_b,
            depth_max_b=depth_max_b,
            tile_service_factory=tile_service_factory
        ),
        preservesPartitioning=True
    ).filter(
        lambda p_m_tuple: abs(
            iso_time_to_epoch(p_m_tuple[0].time) - iso_time_to_epoch(p_m_tuple[1].time)
        ) <= time_tolerance
    )

    if match_once:
        # Only the 'nearest' point for each primary should be returned. Add an extra map/reduce which calculates
        # the distance and finds the minimum

        # Method used for calculating the distance between 2 DomsPoints
        from pyproj import Geod

        def dist(primary, matchup):
            wgs84_geod = Geod(ellps='WGS84')
            lat1, lon1 = (primary.latitude, primary.longitude)
            lat2, lon2 = (matchup.latitude, matchup.longitude)
            az12, az21, distance = wgs84_geod.inv(lon1, lat1, lon2, lat2)
            return distance

        rdd_filtered = rdd_filtered \
            .map(lambda primary_matchup: tuple([primary_matchup[0], tuple([primary_matchup[1], dist(primary_matchup[0], primary_matchup[1])])])) \
            .reduceByKey(lambda match_1, match_2: match_1 if match_1[1] < match_2[1] else match_2) \
            .mapValues(lambda x: [x[0]])
    else:
        rdd_filtered = rdd_filtered \
            .combineByKey(lambda value: [value],  # Create 1 element list
                          lambda value_list, value: value_list + [value],  # Add 1 element to list
                          lambda value_list_a, value_list_b: value_list_a + value_list_b)  # Add two lists together

    result_as_map = rdd_filtered.collectAsMap()

    return result_as_map


def determine_parllelism(num_tiles):
    """
    Try to stay at a maximum of 140 tiles per partition; But don't go over 128 partitions.
    Also, don't go below the default of 8
    """
    num_partitions = max(min(num_tiles / 140, 128), 8)
    return num_partitions


def add_meters_to_lon_lat(lon, lat, meters):
    """
    Uses a simple approximation of
    1 degree latitude = 111,111 meters
    1 degree longitude = 111,111 meters * cosine(latitude)
    :param lon: longitude to add meters to
    :param lat: latitude to add meters to
    :param meters: meters to add to the longitude and latitude values
    :return: (longitude, latitude) increased by given meters
    """
    longitude = lon + ((meters / 111111) * cos(radians(lat)))
    latitude = lat + (meters / 111111)

    return longitude, latitude


def tile_to_edge_points(tile):
    indices = tile.get_indices()
    edge_points = []

    for idx in indices:
        if tile.is_multi:
            data = [var_data[tuple(idx)] for var_data in tile.data]
        else:
            data = [tile.data[tuple(idx)]]

        edge_point = {
            'point': f'Point({tile.longitudes[idx[2]]} {tile.latitudes[idx[1]]})',
            'time': datetime.utcfromtimestamp(tile.times[idx[0]]).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'source': tile.dataset,
            'platform': None,
            'device': None,
            'fileurl': tile.granule,
            'variables': tile.variables,
            'var_values': data
        }
        edge_points.append(edge_point)
    return edge_points


def match_satellite_to_insitu(tile_ids, primary_b, secondary_b, parameter_b, tt_b, rt_b, platforms_b,
                              bounding_wkt_b, depth_min_b, depth_max_b, tile_service_factory):
    the_time = datetime.now()
    tile_ids = list(tile_ids)
    if len(tile_ids) == 0:
        return []

    tile_service = tile_service_factory()

    # Determine the spatial temporal extents of this partition of tiles
    tiles_bbox = tile_service.get_bounding_box(tile_ids)
    tiles_min_time = tile_service.get_min_time(tile_ids)
    tiles_max_time = tile_service.get_max_time(tile_ids)

    # Increase spatial extents by the radius tolerance
    matchup_min_lon, matchup_min_lat = add_meters_to_lon_lat(tiles_bbox.bounds[0], tiles_bbox.bounds[1],
                                                             -1 * rt_b.value)
    matchup_max_lon, matchup_max_lat = add_meters_to_lon_lat(tiles_bbox.bounds[2], tiles_bbox.bounds[3], rt_b.value)

    # Don't go outside of the search domain
    search_min_x, search_min_y, search_max_x, search_max_y = wkt.loads(bounding_wkt_b.value).bounds
    matchup_min_lon = max(matchup_min_lon, search_min_x)
    matchup_min_lat = max(matchup_min_lat, search_min_y)
    matchup_max_lon = min(matchup_max_lon, search_max_x)
    matchup_max_lat = min(matchup_max_lat, search_max_y)

    # Find the centroid of the matchup bounding box and initialize the projections
    matchup_center = box(matchup_min_lon, matchup_min_lat, matchup_max_lon, matchup_max_lat).centroid.coords[0]
    aeqd_proj = pyproj.Proj(proj='aeqd', lon_0=matchup_center[0], lat_0=matchup_center[1])

    # Increase temporal extents by the time tolerance
    matchup_min_time = tiles_min_time - tt_b.value
    matchup_max_time = tiles_max_time + tt_b.value
    print("%s Time to determine spatial-temporal extents for partition %s to %s" % (
        str(datetime.now() - the_time), tile_ids[0], tile_ids[-1]))

    # Query edge for all points within the spatial-temporal extents of this partition
    is_insitu_dataset = edge_endpoints.get_provider_name(secondary_b.value) is not None

    if is_insitu_dataset:
        the_time = datetime.now()
        edge_session = requests.Session()
        edge_results = []
        with edge_session:
            for insitudata_name in secondary_b.value.split(','):
                bbox = ','.join(
                    [str(matchup_min_lon), str(matchup_min_lat), str(matchup_max_lon), str(matchup_max_lat)])
                edge_response = query_edge(insitudata_name, parameter_b.value, matchup_min_time, matchup_max_time, bbox,
                                           platforms_b.value, depth_min_b.value, depth_max_b.value, session=edge_session)
                if edge_response['total'] == 0:
                    continue
                r = edge_response['results']
                for p in r:
                    p['source'] = insitudata_name
                edge_results.extend(r)
        print("%s Time to call edge for partition %s to %s" % (str(datetime.now() - the_time), tile_ids[0], tile_ids[-1]))
        if len(edge_results) == 0:
            return []

        # Convert edge points to utm
        the_time = datetime.now()
        matchup_points = np.ndarray((len(edge_results), 2), dtype=np.float32)
        for n, edge_point in enumerate(edge_results):
            x, y = edge_point['longitude'], edge_point['latitude']
            matchup_points[n][0], matchup_points[n][1] = aeqd_proj(x, y)
    else:
        # Query nexus (cassandra? solr?) to find matching points.
        bbox = ','.join(
            [str(matchup_min_lon), str(matchup_min_lat), str(matchup_max_lon),
             str(matchup_max_lat)])
        west, south, east, north = [float(b) for b in bbox.split(",")]
        polygon = Polygon(
            [(west, south), (east, south), (east, north), (west, north), (west, south)])

        matchup_tiles = tile_service.find_tiles_in_polygon(
            bounding_polygon=polygon,
            ds=secondary_b.value,
            start_time=matchup_min_time,
            end_time=matchup_max_time,
            fetch_data=True,
            sort=['tile_min_time_dt asc', 'tile_min_lon asc', 'tile_min_lat asc'],
            rows=5000
        )

        # Convert Tile IDS to tiles and convert to UTM lat/lon projection.
        matchup_points = []
        for tile in matchup_tiles:
            valid_indices = tile.get_indices()
            primary_points = np.array([aeqd_proj(
                tile.longitudes[aslice[2]],
                tile.latitudes[aslice[1]]
            ) for aslice in valid_indices])
            matchup_points.extend(primary_points)

        # Convert tiles to 'edge points' which match the format of in-situ edge points.
        edge_results = []
        for matchup_tile in matchup_tiles:
            edge_results.extend(tile_to_edge_points(matchup_tile))

        matchup_points = np.array(matchup_points)

    print("%s Time to convert match points for partition %s to %s" % (
        str(datetime.now() - the_time), tile_ids[0], tile_ids[-1]))

    # Build kdtree from matchup points
    the_time = datetime.now()
    m_tree = spatial.cKDTree(matchup_points, leafsize=30)
    print("%s Time to build matchup tree" % (str(datetime.now() - the_time)))

    # The actual matching happens in the generator. This is so that we only load 1 tile into memory at a time
    match_generators = [match_tile_to_point_generator(tile_service, tile_id, m_tree, edge_results, bounding_wkt_b.value,
                                                      parameter_b.value, rt_b.value, aeqd_proj) for tile_id
                        in tile_ids]

    return chain(*match_generators)


def match_tile_to_point_generator(tile_service, tile_id, m_tree, edge_results, search_domain_bounding_wkt,
                                  search_parameter, radius_tolerance, aeqd_proj):
    from nexustiles.model.nexusmodel import NexusPoint
    from webservice.algorithms_spark.Matchup import DomsPoint  # Must import DomsPoint or Spark complains

    # Load tile
    try:
        the_time = datetime.now()
        tile = tile_service.mask_tiles_to_polygon(wkt.loads(search_domain_bounding_wkt),
                                                  tile_service.find_tile_by_id(tile_id))[0]
        print("%s Time to load tile %s" % (str(datetime.now() - the_time), tile_id))
    except IndexError:
        # This should only happen if all measurements in a tile become masked after applying the bounding polygon
        print('Tile is empty after masking spatially. Skipping this tile.')
        return

    # Convert valid tile lat,lon tuples to UTM tuples
    the_time = datetime.now()
    # Get list of indices of valid values
    valid_indices = tile.get_indices()
    primary_points = np.array(
        [aeqd_proj(tile.longitudes[aslice[2]], tile.latitudes[aslice[1]]) for
         aslice in valid_indices])

    print("%s Time to convert primary points for tile %s" % (str(datetime.now() - the_time), tile_id))

    a_time = datetime.now()
    p_tree = spatial.cKDTree(primary_points, leafsize=30)
    print("%s Time to build primary tree" % (str(datetime.now() - a_time)))

    a_time = datetime.now()
    matched_indexes = p_tree.query_ball_tree(m_tree, radius_tolerance)
    print("%s Time to query primary tree for tile %s" % (str(datetime.now() - a_time), tile_id))
    for i, point_matches in enumerate(matched_indexes):
        if len(point_matches) > 0:
            if tile.is_multi:
                data_vals = [tile_data[tuple(valid_indices[i])] for tile_data in tile.data]
            else:
                data_vals = tile.data[tuple(valid_indices[i])]
            p_nexus_point = NexusPoint(
                latitude=tile.latitudes[valid_indices[i][1]],
                longitude=tile.longitudes[valid_indices[i][2]],
                depth=None,
                time=tile.times[valid_indices[i][0]],
                index=valid_indices[i],
                data_vals=data_vals
            )

            p_doms_point = DomsPoint.from_nexus_point(p_nexus_point, tile=tile)
            for m_point_index in point_matches:
                m_doms_point = DomsPoint.from_edge_point(edge_results[m_point_index])
                yield p_doms_point, m_doms_point


def query_edge(dataset, variable, startTime, endTime, bbox, platform, depth_min, depth_max, itemsPerPage=1000,
               startIndex=0, stats=True, session=None):
    try:
        startTime = datetime.utcfromtimestamp(startTime).strftime('%Y-%m-%dT%H:%M:%SZ')
    except TypeError:
        # Assume we were passed a properly formatted string
        pass

    try:
        endTime = datetime.utcfromtimestamp(endTime).strftime('%Y-%m-%dT%H:%M:%SZ')
    except TypeError:
        # Assume we were passed a properly formatted string
        pass

    provider = edge_endpoints.get_provider_name(dataset)

    params = {
        "itemsPerPage": itemsPerPage,
        "startTime": startTime,
        "endTime": endTime,
        "bbox": bbox,
        "minDepth": depth_min,
        "maxDepth": depth_max,
        "provider": provider,
        "project": dataset,
        "platform": platform,
    }

    if variable is not None:
        params["variable"] = variable

    edge_response = {}

    # Get all edge results
    next_page_url = edge_endpoints.getEndpoint()
    while next_page_url is not None and next_page_url != 'NA':
        logging.debug(f'Edge request {next_page_url}')
        if session is not None:
            edge_page_request = session.get(next_page_url, params=params)
        else:
            edge_page_request = requests.get(next_page_url, params=params)

        edge_page_request.raise_for_status()

        edge_page_response = json.loads(edge_page_request.text)

        if not edge_response:
            edge_response = edge_page_response
        else:
            edge_response['results'].extend(edge_page_response['results'])

        next_page_url = edge_page_response.get('next', None)
        params = {}  # Remove params, they are already included in above URL

    return edge_response
