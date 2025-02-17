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

INSITU_API_ENDPOINT = 'https://doms.jpl.nasa.gov/insitu/1.0/query_data_doms_custom_pagination'

INSITU_PROVIDER_MAP = [
    {
        'name': 'NCAR',
        'projects': [
            {
                'name': 'ICOADS Release 3.0',
                'platforms': ['0', '16', '17', '30', '41', '42']
            }
        ]
    },
    {
        'name': 'Florida State University, COAPS',
        'projects': [
            {
                'name': 'SAMOS',
                'platforms': ['30']
            }
        ]
    },
    {
        'name': 'Saildrone',
        'projects': [
            {
                'name': '1021_atlantic',
                'platforms': ['3B']
            },
            {
                'name': 'antarctic_circumnavigation_2019',
                'platforms': ['3B']
            },
            {
                'name': 'atlantic_to_med_2019_to_2020',
                'platforms': ['3B']
            },
            {
                'name': 'shark-2018',
                'platforms': ['3B']
            }
        ]
    }
]

ENDPOINTS = [
    {
        "name": "samos",
        "url": "https://doms.coaps.fsu.edu/ws/search/samos_cdms",
        "fetchParallel": True,
        "fetchThreads": 8,
        "itemsPerPage": 1000,
        "metadataUrl": "http://doms.jpl.nasa.gov/ws/metadata/dataset?shortName=SAMOS&format=umm-json"
    },
    {
        "name": "spurs",
        "url": "https://doms.jpl.nasa.gov/ws/search/spurs",
        "fetchParallel": True,
        "fetchThreads": 8,
        "itemsPerPage": 25000,
        "metadataUrl": "http://doms.jpl.nasa.gov/ws/metadata/dataset?shortName=SPURS-1&format=umm-json"
    },
    {
        "name": "icoads",
        "url": "http://rda-work.ucar.edu:8890/ws/search/icoads",
        "fetchParallel": True,
        "fetchThreads": 8,
        "itemsPerPage": 1000,
        "metadataUrl": "http://doms.jpl.nasa.gov/ws/metadata/dataset?shortName=ICOADS&format=umm-json"
    },
    {
        "name": "spurs2",
        "url": "https://doms.jpl.nasa.gov/ws/search/spurs2",
        "fetchParallel": True,
        "fetchThreads": 8,
        "itemsPerPage": 25000,
        "metadataUrl": "http://doms.jpl.nasa.gov/ws/metadata/dataset?shortName=SPURS-2&format=umm-json"
    }
]

METADATA_LINKS = {
    "samos": "http://samos.coaps.fsu.edu/html/nav.php?s=2",
    "icoads": "https://rda.ucar.edu/datasets/ds548.0/",
    "spurs": "https://podaac.jpl.nasa.gov/spurs",
    "spurs2": "https://podaac.jpl.nasa.gov/spurs?tab=spurs2-campaign",
}

import os

try:
    env = os.environ['ENV']
    if env == 'dev':
        ENDPOINTS = [
            {
                "name": "samos",
                "url": "https://doms.coaps.fsu.edu/ws/search/samos_cdms",
                "fetchParallel": True,
                "fetchThreads": 8,
                "itemsPerPage": 1000,
                "metadataUrl": "http://doms.jpl.nasa.gov/ws/metadata/dataset?shortName=SAMOS&format=umm-json"
            },
            {
                "name": "spurs",
                "url": "http://127.0.0.1:8890/ws/search/spurs",
                "fetchParallel": True,
                "fetchThreads": 8,
                "itemsPerPage": 25000,
                "metadataUrl": "http://doms.jpl.nasa.gov/ws/metadata/dataset?shortName=SPURS-1&format=umm-json"
            },
            {
                "name": "icoads",
                "url": "http://rda-work.ucar.edu:8890/ws/search/icoads",
                "fetchParallel": True,
                "fetchThreads": 8,
                "itemsPerPage": 1000,
                "metadataUrl": "http://doms.jpl.nasa.gov/ws/metadata/dataset?shortName=ICOADS&format=umm-json"
            },
            {
                "name": "spurs2",
                "url": "https://doms.jpl.nasa.gov/ws/search/spurs2",
                "fetchParallel": True,
                "fetchThreads": 8,
                "itemsPerPage": 25000,
                "metadataUrl": "http://doms.jpl.nasa.gov/ws/metadata/dataset?shortName=SPURS-2&format=umm-json"
            }
        ]
        METADATA_LINKS = {
            "samos": "http://samos.coaps.fsu.edu/html/nav.php?s=2",
            "icoads": "https://rda.ucar.edu/datasets/ds548.1/",
            "spurs": "https://podaac.jpl.nasa.gov/spurs"
        }
except KeyError:
    pass


def getEndpoint():
    return INSITU_API_ENDPOINT


def getEndpointByName(name):
    for endpoint in ENDPOINTS:
        if endpoint["name"].upper() == name.upper():
            return endpoint
    return None

def validate_insitu_params(provider_name, project_name, platform_name):
    """
    Validate the provided params. The project should be within the
    given provider and the platform should be appropriate for the
    given project.
    """
    provider = next((provider for provider in INSITU_PROVIDER_MAP
                     if provider['name'] == provider_name), None)

    if provider is None:
        return False

    project = next((project for project in provider['projects']
                    if project_name == project['name']), None)

    if project is None:
        return False

    return platform_name in project['platforms']


def get_provider_name(project_name):
    provider = next((provider for provider in INSITU_PROVIDER_MAP
                     if project_name in map(lambda project: project['name'], provider['projects'])), None)

    if provider is not None:
        return provider['name']

    # Check DOMS endpoints as well. Eventually we should remove this so
    # only CDMS insitu endpoints are used.
    provider = next((provider for provider in ENDPOINTS
                     if provider['name'] == project_name), None)
    if provider is not None:
        return provider['name']

