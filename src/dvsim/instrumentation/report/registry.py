# Copyright lowRISC contributors (OpenTitan project).
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""DVSim scheduler instrumentation report visualization registry."""

from typing import ClassVar

from dvsim.instrumentation.report.base import InstrumentationVisualizer, RenderProfile
from dvsim.instrumentation.report.breakdown import BlockVariantBreakdown, ToolBreakdown
from dvsim.instrumentation.report.longest import (
    LongestByBlockChart,
    LongestByStatusChart,
    LongestByToolChart,
    LongestTestsByBlockChart,
    LongestTestsByToolChart,
)
from dvsim.instrumentation.report.timelines import GanttChart, ParallelismChart
from dvsim.instrumentation.report.usage import ToolUsageLineGraph

__all__ = ("ReportVisualizationRegistry",)


class ReportVisualizationRegistry:
    """Registry for scheduler instrumentation visualizer classes."""

    _registry: ClassVar[dict[str, type[InstrumentationVisualizer]]] = {}

    @classmethod
    def register(cls, vis_cls: type[InstrumentationVisualizer]) -> None:
        """Register a new instrumentation visualization type."""
        cls._registry[vis_cls.title] = vis_cls

    @classmethod
    def clear(cls) -> None:
        """Clear any registered instrumentation visualization types."""
        cls._registry.clear()

    @classmethod
    def registered(cls) -> dict[str, type[InstrumentationVisualizer]]:
        """Get the current state of the registered instrumentation types."""
        return cls._registry.copy()

    @classmethod
    def create(cls, profile: RenderProfile | None = None) -> list[InstrumentationVisualizer]:
        """Create instances of registered visualization types for a given (optional) profile.

        Args:
            profile: The rendering profile (level of detail) to target, if provided.

        Returns:
            A list of InstrumentationVisualizer implementations created for the given profile.

        """
        if profile is None:
            return [vis_cls() for vis_cls in cls._registry.values()]
        return [vis_cls.for_profile(profile) for vis_cls in cls._registry.values()]


# Register implemented / built-in instrumentation report visualizations
ReportVisualizationRegistry.register(LongestByStatusChart)
ReportVisualizationRegistry.register(LongestByBlockChart)
ReportVisualizationRegistry.register(LongestTestsByBlockChart)
ReportVisualizationRegistry.register(BlockVariantBreakdown)
ReportVisualizationRegistry.register(LongestByToolChart)
ReportVisualizationRegistry.register(LongestTestsByToolChart)
ReportVisualizationRegistry.register(ToolBreakdown)
ReportVisualizationRegistry.register(ToolUsageLineGraph)
ReportVisualizationRegistry.register(GanttChart)
ReportVisualizationRegistry.register(ParallelismChart)


# TODO[IMPORTANT]: The below is for local testing, make sure to remove it later!

from dvsim.instrumentation import InstrumentationResults  # noqa: E402


def _make_fake_results_for_testing(
    num_jobs: int = 500, *, parallel: bool = True
) -> InstrumentationResults:
    import random  # noqa: PLC0415
    import string  # noqa: PLC0415
    from datetime import datetime, timedelta, timezone  # noqa: PLC0415

    from dvsim.instrumentation.records import (  # noqa: PLC0415
        JobInstrumentationMetadata,
        JobInstrumentationResults,
        JobTimingMetrics,
        SchedulerInstrumentationResults,
        SchedulerTimingMetrics,
    )

    rng = random.Random(42)  # noqa: S311
    now = datetime(2026, 4, 30, 9, 0, 0, tzinfo=timezone.utc)

    job_timings: dict[str, JobTimingMetrics] = {}
    t = end = now

    for i in range(num_jobs):
        if rng.random() < 0.96:  # noqa: PLR2004
            duration = max(0.05, rng.gauss(10, 5))
        else:
            duration = max(0.05, rng.gauss(1200, 600))
        end = t + timedelta(seconds=duration)
        job_timings[f"Job {i}"] = JobTimingMetrics(
            start_time=t.timestamp(), end_time=end.timestamp()
        )
        if (parallel and rng.random() < 0.1) or not parallel:  # noqa: PLR2004
            t = end
    t = end

    job_metadata: dict[str, JobInstrumentationMetadata] = {}

    for i in range(num_jobs):
        target = random.choice(["default", "run", "cov_merge", "cov_report"])  # noqa: S311
        deps = set()
        while len(deps) < i and rng.random() < 0.6:  # noqa: PLR2004
            maybe_dep = None
            while maybe_dep is None or maybe_dep in deps:
                maybe_dep = rng.randint(0, i - 1)
            deps.add(maybe_dep)
        block = rng.choice(
            [
                "aes",
                "alert_handler",
                "chip",
                "csrng",
                "edn",
                "flash_ctrl",
                "edn",
                "hmac",
                "kmac",
                "otp_ctrl",
            ]
        )
        job_metadata[f"Job {i}"] = JobInstrumentationMetadata(
            name="".join(rng.choices(string.ascii_uppercase + string.digits, k=10)),
            job_type="".join(word.capitalize() for word in target.split("_")),
            target=target,
            tool=rng.choice(["vcs", "xcelium"]),
            block=block,
            block_variant=("abc" if rng.random() < 0.08 else None),  # noqa: PLR2004
            backend="local",
            dependencies=[f"Job {dep}" for dep in deps],
            status=random.choice(["Passed", "Failed", "Killed"]),  # noqa: S311
        )

    return InstrumentationResults(
        scheduler=SchedulerInstrumentationResults(
            timing=SchedulerTimingMetrics(start_time=now.timestamp(), end_time=t.timestamp()),
        ),
        jobs={
            job_id: JobInstrumentationResults(
                meta=job_metadata[job_id],
                timing=job_timings[job_id],
            )
            for job_id in job_timings
        },
    )


if __name__ == "__main__":
    import sys
    from pathlib import Path

    from dvsim.instrumentation.report.base import render_html_report

    args = sys.argv[1:]
    render_profile = None
    if args and args[0].lower() == "normal":
        print("Set render profile to NORMAL")  # noqa: T201
        render_profile = RenderProfile.NORMAL
        args = args[1:]
    elif args and args[0].lower() == "high":
        print("Set render profile to HIGH")  # noqa: T201
        render_profile = RenderProfile.HIGH
        args = args[1:]
    elif args and args[0].lower() == "full":
        print("Set render profile to FULL")  # noqa: T201
        render_profile = RenderProfile.FULL
        args = args[1:]

    if args:
        results_list = [Path(arg) for arg in args]
        for results_path in results_list:
            if not results_path.exists():
                print(f"Skipping results file {results_path} which does not exist.")  # noqa: T201
                continue
            print(f"Loading instrumentation report: {results_path}")  # noqa: T201
            report = InstrumentationResults.model_validate_json(results_path.read_text())
            print(f"Finished loading given instrumentation report: {results_path}")  # noqa: T201
            _outdir = Path("./real_metrics/generated") / results_path.name.removesuffix(".json")
            _visualizations = ReportVisualizationRegistry.create(profile=render_profile)
            _artifacts = render_html_report(report, visualizations=_visualizations, outdir=_outdir)
            print(f"Historic instrumentation report data written under {_outdir}")  # noqa: T201
    else:
        for _num_fake_jobs in [
            1,
            3,
            5,
            10,
            25,
            100,
            250,
            500,
            1000,
            2500,
            5000,
            10000,
            25000,
            50000,
            250000,
        ]:
            print(f"Making fake report with {_num_fake_jobs} jobs...")  # noqa: T201
            _fake_report = _make_fake_results_for_testing(num_jobs=_num_fake_jobs, parallel=True)
            print("Finished making fake report. Rendering HTML visualizations...")  # noqa: T201
            _outdir = Path("./mock_metrics", str(_num_fake_jobs).rjust(6, "0"))
            _visualizations = ReportVisualizationRegistry.create(profile=render_profile)
            _artifacts = render_html_report(
                _fake_report, visualizations=_visualizations, outdir=_outdir
            )
            print(f"Fake instrumentation report data written under {_outdir}")  # noqa: T201
