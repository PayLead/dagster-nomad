from __future__ import annotations

from dagster_aws.s3 import s3_pickle_io_manager, s3_resource

from dagster import Definitions, load_assets_from_modules

from . import assets
from .assets import sample_job

S3_IO_MANAGER_CONFIG = {
    "s3_bucket": {"env": "DAGSTER_S3_BUCKET"},
    "s3_prefix": "io_manager",
}
S3_SESSION_CONFIG = {
    "endpoint_url": {"env": "DAGSTER_S3_ENDPOINT_URL"},
}

currency_assets = load_assets_from_modules([assets])

defs = Definitions(
    assets=currency_assets,
    jobs=[sample_job],
    resources={
        "io_manager": s3_pickle_io_manager.configured(S3_IO_MANAGER_CONFIG),
        "s3": s3_resource.configured(S3_SESSION_CONFIG),
    },
)
