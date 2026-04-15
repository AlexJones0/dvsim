# Copyright lowRISC contributors (OpenTitan project).
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""EDA tool plugin providing Z01X support to DVSim."""

from typing import TYPE_CHECKING

from dvsim.job.data import ResourceMapping
from dvsim.sim.tool.base import SimStage
from dvsim.sim.tool.vcs import VCS

if TYPE_CHECKING:
    from dvsim.job.deploy import Deploy

__all__ = ("Z01X",)


class Z01X(VCS):
    """Implement Z01X tool support."""

    @staticmethod
    def get_job_resources(stage: SimStage) -> ResourceMapping | None:
        """Get the resources (licenses) that are used for a given sim job stage.

        Args:
            stage: the simulation flow job stage to get resources for.
            mode: the mode of operation being used to run the job.

        Returns:
            a Mapping of (resource_name -> resource_count) used for a job in this configuration,
            or None if no mapping is defined for this stage.

        """
        resources = VCS.get_job_resources(stage)
        if resources is not None:
            resources["Z01X"] = 1
        return resources

    @staticmethod
    def set_additional_attrs(deploy: "Deploy") -> None:
        """Define any additional tool-specific attrs on the deploy object.

        Args:
            deploy: the deploy object to mutate.

        """
        # TODO: when circular import issues are resolved, this can be a check of
        # `isinstance(deploy, RunTest)` and we don't need the type ignores here.
        if deploy.target == "run":
            sim_run_opts = " ".join(opt.strip() for opt in deploy.run_opts)  # type: ignore[reportAttributeAccessIssue]
            deploy.exports.append({"sim_run_opts": sim_run_opts})
            deploy.run_opts = list(getattr(deploy.sim_cfg, "run_opts_fi_sim", ()))  # type: ignore[reportAttributeAccessIssue]
