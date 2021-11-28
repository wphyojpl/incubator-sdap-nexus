import logging
import pytest
import os

from pyspark import HiveContext
from pyspark import SparkConf
from pyspark import SparkContext
from pyspark.streaming import StreamingContext

def quiet_py4j():
    logger = logging.getLogger('py4j')
    logger.setLevel(logging.WARN)

@pytest.fixture(scope="session")
def spark_context(request):
    conf = (SparkConf().setMaster("local[2]").setAppName("pytest-pyspark-local-testing"))
    request.addfinalizer(lambda: sc.stop())

    sc = SparkContext(conf=conf)
    quiet_py4j()
    return sc

@pytest.fixture(scope="session")
def hive_context(spark_context):
    return HiveContext(spark_context)

@pytest.fixture(scope="session")
def streaming_context(spark_context):
    return StreamingContext(spark_context, 1)

@pytest.fixture(scope="session")
def setup_pyspark_env():
    os.environ['PYSPARK_DRIVER_PYTHON'] = '/usr/local/anaconda3/envs/nexus-messages3/bin/python'
    os.environ['PYSPARK_PYTHON'] = '/usr/local/anaconda3/envs/nexus-messages3/bin/python'