from contextlib import contextmanager
from typing import Callable, ContextManager, Iterator
from unittest.mock import MagicMock

import pytest
from dagster._core.instance import DagsterInstance
from dagster._core.test_utils import instance_for_test

from dagster_nomad.run_launcher import NomadClient


@pytest.fixture
def mock_nomad_client():
    nomad_client = MagicMock(spec=NomadClient)
    nomad_client.dispatch_job.return_value = "job_id_dispatched"
    nomad_client.get_job_status.return_value = ("running", False)
    return nomad_client


@pytest.fixture
def instance_cm() -> Callable[..., ContextManager[DagsterInstance]]:
    @contextmanager
    def cm(config=None):
        overrides = {
            "run_launcher": {
                "module": "dagster_nomad",
                "class": "NomadRunLauncher",
                "config": {**(config or {})},
            }
        }
        with instance_for_test(overrides) as dagster_instance:
            yield dagster_instance

    return cm


@pytest.fixture
def instance(
    instance_cm: Callable[..., ContextManager[DagsterInstance]], mock_nomad_client
) -> Iterator[DagsterInstance]:
    with instance_cm(
        {
            "job_id": "test_job",
            "url": "http://nomad.example.com",
        }
    ) as dagster_instance:
        yield dagster_instance


class TestNomadRunLauncher:
    def test_launch_run(self, instance, workspace, run, mock_nomad_client):
        instance.run_launcher.nomad_client = mock_nomad_client
        instance.launch_run(run.run_id, workspace)

        assert mock_nomad_client.dispatch_job.call_count == 1
        assert mock_nomad_client.dispatch_job.call_args_list[0][0][0] == "test_job"
