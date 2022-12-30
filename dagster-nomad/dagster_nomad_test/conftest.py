"""
Pytest fixtures and helpers for testing Dagster Nomad integration

Most of the code is adapted from dagster-aws
See: dagster-aws/dagster_aws_tests/ecs_tests/launcher_tests/conftest.py
"""
from typing import Iterator

import pytest
from dagster import job, op, repository
from dagster._core.definitions.job_definition import JobDefinition
from dagster._core.host_representation.external import ExternalJob
from dagster._core.instance import DagsterInstance
from dagster._core.storage.dagster_run import DagsterRun
from dagster._core.test_utils import in_process_test_workspace
from dagster._core.types.loadable_target_origin import LoadableTargetOrigin
from dagster._core.workspace.context import WorkspaceRequestContext


@op
def node(_):
    pass


@job
def job_def():
    node()


@repository
def repository():
    return {"jobs": {"job": job_def}}


@pytest.fixture
def job() -> JobDefinition:
    return job_def


@pytest.fixture
def run(instance: DagsterInstance, job: JobDefinition, external_job: ExternalJob) -> DagsterRun:
    return instance.create_run_for_job(
        job_def,
        external_job_origin=external_job.get_external_origin(),
        job_code_origin=external_job.get_python_origin(),
    )


@pytest.fixture
def external_job(workspace: WorkspaceRequestContext) -> ExternalJob:
    location = workspace.get_code_location(workspace.code_location_names[0])
    return location.get_repository(repository.name).get_full_external_job(job_def.name)


@pytest.fixture
def image() -> str:
    return "dagster:first"


@pytest.fixture
def workspace(instance: DagsterInstance, image: str) -> Iterator[WorkspaceRequestContext]:
    with in_process_test_workspace(
        instance,
        loadable_target_origin=LoadableTargetOrigin(
            python_file=__file__,
            attribute=repository.name,
        ),
        container_image=image,
    ) as workspace:
        yield workspace
