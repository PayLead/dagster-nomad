import io

import httpx
import pandas as pd
from dagster import asset, Output, define_asset_job


EURO_FX_REF_CSV_FILE_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"


@asset
def demo_euro_fx_ref_csv_file() -> Output[bytes]:
    res = httpx.get(EURO_FX_REF_CSV_FILE_URL)
    res.raise_for_status()
    return Output(res.content)


@asset
def demo_currencies_rates(demo_euro_fx_ref_csv_file: bytes) -> Output[pd.DataFrame]:
    df = pd.read_csv(io.BytesIO(demo_euro_fx_ref_csv_file), sep=",", compression="zip")
    df.drop(columns=[c for c in df.columns if c.startswith("Unnamed")], inplace=True)
    return Output(df)


sample_job = define_asset_job(
    "demo_sample_job", selection=[demo_euro_fx_ref_csv_file, demo_currencies_rates], tags={"executor": "sample"}
)
