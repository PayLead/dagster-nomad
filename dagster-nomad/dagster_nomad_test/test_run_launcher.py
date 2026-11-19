from contextlib import contextmanager
from typing import Callable, ContextManager, Iterator
from unittest.mock import MagicMock

import pytest
from dagster._core.instance import DagsterInstance
from dagster._core.remote_origin import IN_PROCESS_NAME
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


SECOND_CLUSTER = {"url": "http://nomad-gpu.example.com", "job_id": "gpu_job"}


def _mock_clients(instance, **extra):
    """Replace every Nomad client of the launcher with a dedicated mock."""
    mocks = {}
    for name in instance.run_launcher.nomad_clients:
        client = MagicMock(spec=NomadClient)
        client.dispatch_job.return_value = f"dispatched_on_{name}"
        client.get_job_status.return_value = ("alloc", "running", False)
        mocks[name] = client
    instance.run_launcher.nomad_clients = mocks
    return mocks


class TestNomadRunLauncherMultiCluster:
    def test_no_rule_uses_default_cluster(self, instance, workspace, run):
        clients = _mock_clients(instance)
        instance.launch_run(run.run_id, workspace)

        assert clients["default"].dispatch_job.call_args_list[0][0][0] == "test_job"
        assert instance.get_run_by_id(run.run_id).tags["nomad_cluster"] == "default"

    @pytest.mark.parametrize(
        "instance",
        [
            {"clusters": {"gpu": SECOND_CLUSTER}, "cluster_rules": [{"cluster": "gpu", "location": "*"}]},
            {"clusters": {"gpu": SECOND_CLUSTER}, "cluster_rules": [{"cluster": "gpu", "job": "job_*"}]},
            {
                "clusters": {"gpu": SECOND_CLUSTER},
                "cluster_rules": [{"cluster": "gpu", "location": IN_PROCESS_NAME, "job": "job_def"}],
            },
        ],
        indirect=True,
    )
    def test_matching_rule_selects_cluster(self, instance, workspace, run):
        clients = _mock_clients(instance)
        instance.launch_run(run.run_id, workspace)

        clients["default"].dispatch_job.assert_not_called()
        assert clients["gpu"].dispatch_job.call_args_list[0][0][0] == "gpu_job"

        run = instance.get_run_by_id(run.run_id)
        assert run.tags["nomad_cluster"] == "gpu"
        assert run.tags["nomad_dispatched_job_id"] == "dispatched_on_gpu"

    @pytest.mark.parametrize(
        "instance",
        [
            {"clusters": {"gpu": SECOND_CLUSTER}, "cluster_rules": [{"cluster": "gpu", "location": "other_*"}]},
            {
                "clusters": {"gpu": SECOND_CLUSTER},
                "cluster_rules": [{"cluster": "gpu", "location": IN_PROCESS_NAME, "job": "other_job"}],
            },
        ],
        indirect=True,
    )
    def test_non_matching_rule_falls_back_to_default(self, instance, workspace, run):
        clients = _mock_clients(instance)
        instance.launch_run(run.run_id, workspace)

        clients["gpu"].dispatch_job.assert_not_called()
        assert clients["default"].dispatch_job.call_count == 1

    @pytest.mark.parametrize(
        "instance",
        [
            {
                "clusters": {"gpu": SECOND_CLUSTER, "cpu": {"url": "http://nomad-cpu.example.com"}},
                "cluster_rules": [
                    {"cluster": "cpu", "job": "job_def"},
                    {"cluster": "gpu", "job": "*"},
                ],
            }
        ],
        indirect=True,
    )
    def test_first_matching_rule_wins_and_job_id_is_inherited(self, instance, workspace, run):
        clients = _mock_clients(instance)
        instance.launch_run(run.run_id, workspace)

        clients["gpu"].dispatch_job.assert_not_called()
        # `cpu` does not define job_id: it inherits the top-level one
        assert clients["cpu"].dispatch_job.call_args_list[0][0][0] == "test_job"

    @pytest.mark.parametrize(
        "instance",
        [
            {
                "clusters": {"gpu": {**SECOND_CLUSTER, "job_id_mapping": {IN_PROCESS_NAME: "gpu_mapped_job"}}},
                "cluster_rules": [{"cluster": "gpu", "location": "*"}],
            }
        ],
        indirect=True,
    )
    def test_cluster_job_id_mapping(self, instance, workspace, run):
        clients = _mock_clients(instance)
        instance.launch_run(run.run_id, workspace)

        assert clients["gpu"].dispatch_job.call_args_list[0][0][0] == "gpu_mapped_job"

    @pytest.mark.parametrize(
        "instance",
        [{"clusters": {"gpu": SECOND_CLUSTER}, "cluster_rules": [{"cluster": "gpu", "location": "*"}]}],
        indirect=True,
    )
    def test_health_check_and_terminate_use_run_cluster(self, instance, workspace, run):
        clients = _mock_clients(instance)
        instance.launch_run(run.run_id, workspace)
        run = instance.get_run_by_id(run.run_id)

        instance.run_launcher.check_run_worker_health(run)
        clients["gpu"].get_job_status.assert_called_once_with("dispatched_on_gpu")
        clients["default"].get_job_status.assert_not_called()

        instance.run_launcher.terminate(run.run_id)
        clients["gpu"].stop_job.assert_called_once_with("dispatched_on_gpu")
        clients["default"].stop_job.assert_not_called()

    def test_run_without_cluster_tag_uses_default(self, instance, run):
        clients = _mock_clients(instance)
        instance.add_run_tags(run.run_id, {"nomad_dispatched_job_id": "legacy"})
        run = instance.get_run_by_id(run.run_id)

        instance.run_launcher.check_run_worker_health(run)
        clients["default"].get_job_status.assert_called_once_with("legacy")

    def test_run_with_unknown_cluster_tag(self, instance, run):
        from dagster._core.launcher.base import WorkerStatus

        _mock_clients(instance)
        instance.add_run_tags(run.run_id, {"nomad_dispatched_job_id": "x", "nomad_cluster": "removed"})
        run = instance.get_run_by_id(run.run_id)

        assert instance.run_launcher.check_run_worker_health(run).status == WorkerStatus.NOT_FOUND

    @pytest.mark.parametrize(
        "config, error",
        [
            ({"cluster_rules": [{"cluster": "missing", "location": "*"}]}, "unknown cluster"),
            ({"clusters": {"gpu": SECOND_CLUSTER}, "cluster_rules": [{"cluster": "gpu"}]}, "at least"),
            ({"clusters": {"default": SECOND_CLUSTER}}, "reserved"),
        ],
    )
    def test_invalid_config(self, instance_cm, config, error):
        with pytest.raises(ValueError, match=error):
            with instance_cm({"job_id": "test_job", "url": "http://nomad.example.com", **config}) as instance:
                instance.run_launcher  # noqa: B018


class TestNomadRunLauncherClusterTag:
    @pytest.mark.parametrize(
        "instance",
        # the rule would send the run to `cpu`, the job tag must win
        [
            {
                "clusters": {"gpu": SECOND_CLUSTER, "cpu": {"url": "http://nomad-cpu.example.com"}},
                "cluster_rules": [{"cluster": "cpu", "location": "*"}],
            }
        ],
        indirect=True,
    )
    def test_job_tag_overrides_rules(self, instance, workspace, gpu_run):
        assert gpu_run.tags["dagster-nomad/cluster"] == "gpu"
        clients = _mock_clients(instance)
        instance.launch_run(gpu_run.run_id, workspace)

        clients["cpu"].dispatch_job.assert_not_called()
        assert clients["gpu"].dispatch_job.call_args_list[0][0][0] == "gpu_job"
        assert instance.get_run_by_id(gpu_run.run_id).tags["nomad_cluster"] == "gpu"

    @pytest.mark.parametrize("instance", [{"clusters": {"gpu": SECOND_CLUSTER}}], indirect=True)
    def test_run_tag_selects_cluster(self, instance, workspace, job, external_job):
        run = instance.create_run_for_job(
            job,
            remote_job_origin=external_job.get_remote_origin(),
            job_code_origin=external_job.get_python_origin(),
            tags={"dagster-nomad/cluster": "gpu"},
        )
        clients = _mock_clients(instance)
        instance.launch_run(run.run_id, workspace)

        clients["default"].dispatch_job.assert_not_called()
        assert clients["gpu"].dispatch_job.call_count == 1

    def test_unknown_cluster_tag_fails_launch(self, instance, workspace, gpu_run):
        clients = _mock_clients(instance)
        with pytest.raises(Exception, match="unknown Nomad cluster `gpu`"):
            instance.launch_run(gpu_run.run_id, workspace)
        clients["default"].dispatch_job.assert_not_called()
