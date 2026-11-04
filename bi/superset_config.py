import os

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
SQLALCHEMY_DATABASE_URI = "sqlite:////app/superset_home/superset.db"
SUPERSET_WEBSERVER_TIMEOUT = 120
# Snapshots are replaced in place; no stale result for any prior filter slice.
DATA_CACHE_CONFIG = {"CACHE_TYPE": "NullCache"}
