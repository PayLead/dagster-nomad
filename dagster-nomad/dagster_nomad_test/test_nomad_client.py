import httpx
import pytest

from dagster_nomad.run_launcher import NomadClient

ALLOCATIONS_RESPONSE = [
    {
        "ClientDescription": "All tasks have completed",
        "ClientStatus": "complete",
        "CreateIndex": 19055340,
        "CreateTime": 1784617507710696154,
        "DeploymentStatus": None,
        "DesiredDescription": "",
        "DesiredStatus": "run",
        "DesiredTransition": {
            "ForceReschedule": None,
            "Migrate": None,
            "MigrateDisablePlacement": None,
            "NoShutdownDelay": None,
            "Reschedule": None,
        },
        "EvalID": "71b9dc12-9d5b-2908-0dc1-2f54a94d49e9",
        "FollowupEvalID": "",
        "ID": "c7fda1f4-e05d-e113-cb7e-0f7956fab617",
        "JobID": "dagster-executor-ml/dispatch-1784617507-97b7695f",
        "JobType": "batch",
        "JobVersion": 20,
        "ModifyIndex": 19055347,
        "ModifyTime": 1784617541654898178,
        "Name": "dagster-executor-ml/dispatch-1784617507-97b7695f.dagster-executor-ml[0]",
        "Namespace": "acceptance",
        "NextAllocation": "",
        "NodeID": "3be0515c-122f-4bc0-8c03-07d1607caf84",
        "NodeName": "nomad-scw-03",
        "PreemptedAllocations": None,
        "PreemptedByAllocation": "",
        "RescheduleTracker": None,
        "TaskGroup": "dagster-executor-ml",
        "TaskStates": {
            "knowledge": {
                "Events": [
                    {
                        "Details": {},
                        "DiskLimit": 0,
                        "DisplayMessage": "Task received by client",
                        "DownloadError": "",
                        "DriverError": "",
                        "DriverMessage": "",
                        "ExitCode": 0,
                        "FailedSibling": "",
                        "FailsTask": False,
                        "GenericSource": "",
                        "KillError": "",
                        "KillReason": "",
                        "KillTimeout": 0,
                        "Message": "",
                        "RestartReason": "",
                        "SetupError": "",
                        "Signal": 0,
                        "StartDelay": 0,
                        "TaskSignal": "",
                        "TaskSignalReason": "",
                        "Time": 1784617507754612810,
                        "Type": "Received",
                        "ValidationError": "",
                        "VaultError": "",
                    },
                    {
                        "Details": {"message": "Building Task Directory"},
                        "DiskLimit": 0,
                        "DisplayMessage": "Building Task Directory",
                        "DownloadError": "",
                        "DriverError": "",
                        "DriverMessage": "",
                        "ExitCode": 0,
                        "FailedSibling": "",
                        "FailsTask": False,
                        "GenericSource": "",
                        "KillError": "",
                        "KillReason": "",
                        "KillTimeout": 0,
                        "Message": "Building Task Directory",
                        "RestartReason": "",
                        "SetupError": "",
                        "Signal": 0,
                        "StartDelay": 0,
                        "TaskSignal": "",
                        "TaskSignalReason": "",
                        "Time": 1784617508831539043,
                        "Type": "Task Setup",
                        "ValidationError": "",
                        "VaultError": "",
                    },
                    {
                        "Details": {},
                        "DiskLimit": 0,
                        "DisplayMessage": "Task started by client",
                        "DownloadError": "",
                        "DriverError": "",
                        "DriverMessage": "",
                        "ExitCode": 0,
                        "FailedSibling": "",
                        "FailsTask": False,
                        "GenericSource": "",
                        "KillError": "",
                        "KillReason": "",
                        "KillTimeout": 0,
                        "Message": "",
                        "RestartReason": "",
                        "SetupError": "",
                        "Signal": 0,
                        "StartDelay": 0,
                        "TaskSignal": "",
                        "TaskSignalReason": "",
                        "Time": 1784617510503282729,
                        "Type": "Started",
                        "ValidationError": "",
                        "VaultError": "",
                    },
                    {
                        "Details": {"signal": "0", "oom_killed": "false", "exit_code": "0"},
                        "DiskLimit": 0,
                        "DisplayMessage": "Exit Code: 0",
                        "DownloadError": "",
                        "DriverError": "",
                        "DriverMessage": "",
                        "ExitCode": 0,
                        "FailedSibling": "",
                        "FailsTask": False,
                        "GenericSource": "",
                        "KillError": "",
                        "KillReason": "",
                        "KillTimeout": 0,
                        "Message": "",
                        "RestartReason": "",
                        "SetupError": "",
                        "Signal": 0,
                        "StartDelay": 0,
                        "TaskSignal": "",
                        "TaskSignalReason": "",
                        "Time": 1784617541186143602,
                        "Type": "Terminated",
                        "ValidationError": "",
                        "VaultError": "",
                    },
                ],
                "Failed": False,
                "FinishedAt": "2026-07-21T07:05:41.454884283Z",
                "LastRestart": None,
                "Paused": "",
                "Restarts": 0,
                "StartedAt": "2026-07-21T07:05:10.503341869Z",
                "State": "dead",
            }
        },
    }
]


@pytest.fixture
def allocations_response():
    return ALLOCATIONS_RESPONSE


class TestNomadClientGetJobStatus:
    def test_get_job_status(self, allocations_response):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/job/test_job/allocations"
            return httpx.Response(200, json=allocations_response)

        client = NomadClient(url="http://nomad.example.com", transport=httpx.MockTransport(handler))

        state, failed = client.get_job_status("test_job")

        assert state == "dead"
        assert failed is False
