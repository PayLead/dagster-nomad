from contextlib import contextmanager
from typing import Callable, ContextManager, Iterator
from unittest.mock import MagicMock

import httpx2
import pytest
from dagster import _check as check
from dagster._core.instance import DagsterInstance
from dagster._core.launcher.base import WorkerStatus
from dagster._core.remote_origin import IN_PROCESS_NAME
from dagster._core.storage.dagster_run import DagsterRun
from dagster._core.test_utils import instance_for_test

from dagster_nomad.run_launcher import NomadClient, NomadRunLauncher


@pytest.fixture
def mock_nomad_client():
    nomad_client = MagicMock(spec=NomadClient)
    nomad_client.dispatch_job.return_value = "job_id_dispatched"
    nomad_client.get_job_status.return_value = ("alloc_id", "running", False)
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
    instance_cm: Callable[..., ContextManager[DagsterInstance]], mock_nomad_client, request
) -> Iterator[DagsterInstance]:
    config = getattr(request, "param", {})
    with instance_cm(
        {
            "job_id": "test_job",
            "url": "http://nomad.example.com",
            **config,
        }
    ) as dagster_instance:
        yield dagster_instance


class TestNomadRunLauncher:
    def test_launch_run(self, instance, workspace, run, mock_nomad_client):
        instance.run_launcher.nomad_client = mock_nomad_client
        instance.launch_run(run.run_id, workspace)

        assert mock_nomad_client.dispatch_job.call_count == 1
        assert mock_nomad_client.dispatch_job.call_args_list[0][0][0] == "test_job"

    @pytest.mark.parametrize(
        "instance",
        [{"job_id_mapping": {IN_PROCESS_NAME: "mapped_job"}}],
        indirect=True,
    )
    def test_launch_run_with_job_id_mapping(self, instance, workspace, run, mock_nomad_client):
        instance.run_launcher.nomad_client = mock_nomad_client
        instance.launch_run(run.run_id, workspace)

        assert mock_nomad_client.dispatch_job.call_count == 1
        assert mock_nomad_client.dispatch_job.call_args_list[0][0][0] == "mapped_job"

    @pytest.mark.parametrize(
        "instance",
        [{"job_id_mapping": {"some_other_location": "mapped_job"}}],
        indirect=True,
    )
    def test_launch_run_with_job_id_mapping_no_match(self, instance, workspace, run, mock_nomad_client):
        instance.run_launcher.nomad_client = mock_nomad_client
        instance.launch_run(run.run_id, workspace)

        assert mock_nomad_client.dispatch_job.call_count == 1
        assert mock_nomad_client.dispatch_job.call_args_list[0][0][0] == "test_job"

    def test_terminate(self, instance, workspace, run, mock_nomad_client):
        instance.run_launcher.nomad_client = mock_nomad_client
        instance.launch_run(run.run_id, workspace)

        assert instance.run_launcher.terminate(run.run_id) is True
        assert mock_nomad_client.stop_job.call_args_list[0][0][0] == "job_id_dispatched"

    def test_terminate_without_dispatched_job_id(self, instance, run, mock_nomad_client):
        """The run was never dispatched, there is nothing to stop."""
        instance.run_launcher.nomad_client = mock_nomad_client

        assert instance.run_launcher.terminate(run.run_id) is False
        assert mock_nomad_client.stop_job.call_count == 0


class TestNomadRunLauncherCheckRunWorkerHealth:
    @pytest.fixture
    def launched_run(self, instance, workspace, run, mock_nomad_client) -> DagsterRun:
        instance.run_launcher.nomad_client = mock_nomad_client
        instance.launch_run(run.run_id, workspace)
        return check.not_none(instance.get_run_by_id(run.run_id))

    @pytest.fixture
    def launcher(self, instance) -> NomadRunLauncher:
        return instance.run_launcher

    @pytest.mark.parametrize("state", ["pending", "running"])
    def test_running(self, launcher, launched_run, mock_nomad_client, state):
        mock_nomad_client.get_job_status.return_value = ("alloc_id", state, False)

        assert launcher.check_run_worker_health(launched_run).status == WorkerStatus.RUNNING

    def test_failed_task(self, launcher, launched_run, mock_nomad_client):
        mock_nomad_client.get_job_status.return_value = ("alloc_id", "dead", True)

        assert launcher.check_run_worker_health(launched_run).status == WorkerStatus.FAILED

    def test_task_exited_without_completing_the_run(self, launcher, launched_run, mock_nomad_client):
        """The task exited with a zero status code while the run is still in progress."""
        mock_nomad_client.get_job_status.return_value = ("alloc_id", "dead", False)

        result = launcher.check_run_worker_health(launched_run)

        assert result.status == WorkerStatus.FAILED
        assert "never reported its completion" in check.not_none(result.msg)

    def test_garbage_collected_job(self, launcher, launched_run, mock_nomad_client):
        mock_nomad_client.get_job_status.return_value = None

        assert launcher.check_run_worker_health(launched_run).status == WorkerStatus.NOT_FOUND

    def test_missing_dispatched_job_id(self, launcher, run):
        """The run was never dispatched, so no worker can be alive."""
        assert launcher.check_run_worker_health(run).status == WorkerStatus.NOT_FOUND

    def test_nomad_api_error_is_transient(self, launcher, launched_run, mock_nomad_client):
        """A failing API call must not fail a healthy run, the daemon has no retry of its own."""
        mock_nomad_client.get_job_status.side_effect = httpx2.ConnectError("connection refused")

        result = launcher.check_run_worker_health(launched_run)

        assert result.status == WorkerStatus.RUNNING
        assert result.transient is True

    def test_unexpected_error_is_raised(self, launcher, launched_run, mock_nomad_client):
        mock_nomad_client.get_job_status.side_effect = ValueError("'complete' is not a valid NomadTaskState")

        with pytest.raises(ValueError):
            launcher.check_run_worker_health(launched_run)
