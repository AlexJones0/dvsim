# Copyright lowRISC contributors (OpenTitan project).
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""DVSim scheduler instrumentation timeline (bar chart) visualizations."""

import heapq
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import plotly.colors as pc
import plotly.graph_objects as go
from plotly.graph_objs import Figure
from typing_extensions import Self

from dvsim.instrumentation import InstrumentationResults
from dvsim.instrumentation.records import ConcreteJobTimingMetrics
from dvsim.instrumentation.report.base import (
    DEFAULT_PNG_THRESHOLD,
    DEFAULT_VISUALIZATION_HEIGHT_PX,
    PLOTLY_TIMING_AXIS_CONFIG,
    InstrumentationVisualizer,
    RenderProfile,
    make_job_metadata_hover,
    make_repeating_color_map,
    render_large_figure,
)
from dvsim.logging import log
from dvsim.utils import format_time_as_hms as format_time
from dvsim.utils import format_time_metric

# Default timeline (bar chart) rendering size & positioning properties
DEFAULT_MIN_BAR_PX: int = 4
DEFAULT_MAX_BAR_PX: int = 50


@dataclass(frozen=True)
class TimelineMeta:
    """Metadata about a run's timeline computed from instrumentation information."""

    num_jobs: int
    num_indices: int
    run_duration: float


@dataclass(frozen=True)
class TimelineResult:
    """A plotly Figure & some accompanying run metadata computed from instrumentation info."""

    fig: Figure
    meta: TimelineMeta


class TimelineBarChart(InstrumentationVisualizer):
    """Renders plotly bar chart figures showing scheduler job timeline information."""

    def __init__(
        self,
        *,
        squashed: bool = False,
        apply_bar_scaling: bool = True,
        bar_px_range: tuple[int, int] = (DEFAULT_MIN_BAR_PX, DEFAULT_MAX_BAR_PX),
        png_threshold: int | None = DEFAULT_PNG_THRESHOLD,
    ) -> None:
        """Construct a TimelineBarChart.

        Args:
            squashed: If true, squash bars down into the minimum number of indices/slots required
              to render all bars without overlaps (as in interval partitioning).
            apply_bar_scaling: Enable the ability to automatically increase the bar thickness. This
              will make bars more visible on larger graphs, but will cause bars to overlap.
            bar_px_range: tuple of (min, max) range of pixels that each bar is allowed to occupy.
            png_threshold: If more than this many bars are provided, the graph will be rendered as
              a PNG for space/performance optimization. If `None`, this will never happen.

        """
        self.squashed: bool = squashed
        self.apply_bar_scaling: bool = apply_bar_scaling
        self.min_bar_px: int = bar_px_range[0]
        self.max_bar_px: int = bar_px_range[1]
        self.png_threshold = png_threshold

        # Margins to render the bar chart with
        self.margins: dict[str, int] = {"t": 80, "b": 40, "l": 50, "r": 20}

    def _assign_parallel_indices(
        self, jobs_by_start_time: list[tuple[str, ConcreteJobTimingMetrics]]
    ) -> tuple[dict[str, int], int]:
        """Squash the bar chart by assigning each job to the first unoccupied parallel index.

        Args:
            jobs_by_start_time: a list of (job_id, job timing) items pre-ordered by start time.

        Returns:
            A tuple of (mapping of job_id -> assigned index, number of indices).

        """
        heap: list[tuple[float, int]] = []  # heap of (end time, slot ID)
        assignments: dict[str, int] = {}  # assignments of (job ID -> slot ID)
        next_slot_id: int = 0

        # Greedy assignment, take the last known free slot.
        for job_id, timing in jobs_by_start_time:
            if timing.start_time is None or timing.end_time is None:
                continue
            if heap and heap[0][0] <= timing.start_time:
                _, slot = heapq.heappop(heap)
            else:
                slot = next_slot_id
                next_slot_id += 1
            heapq.heappush(heap, (timing.end_time, slot))
            assignments[job_id] = slot

        return assignments, next_slot_id

    def _compute_chart_height(self, num_indices: int) -> int:
        """Compute the height that should be used for the bar chart figure."""
        vertical_margins = self.margins.get("t", 0) + self.margins.get("b", 0)
        height = num_indices * self.max_bar_px + vertical_margins
        return min(height, DEFAULT_VISUALIZATION_HEIGHT_PX)

    def _compute_bar_thickness(self, num_indices: int) -> float:
        """Compute the bar thickness (in visual units, not px) to use for this chart.

        Below a configured threshold (`DEFAULT_PNG_THRESHOLD`) we always render at a minimum width.
        After this 'knee', we linearly scale the width to ensure visibility for large amounts.

        """
        if not self.apply_bar_scaling or num_indices <= DEFAULT_PNG_THRESHOLD:
            return 1.0
        scaled = num_indices / DEFAULT_PNG_THRESHOLD * self.min_bar_px
        return max(1.0, scaled)

    def _get_marker_info(self, num_indices: int, bar_color: str) -> dict[str, Any]:
        """Get the bar marker information to use for this chart.

        If squashed, we always render without outlines. Otherwise, below a configured threshold
        (DEFAULT_PNG_THRESHOLD), we render as normal. When the number of jobs exceeds this
        threshold, we make bar outlines less distinctive to avoid small bars overlapping and
        combining to blot out parts of the graph.

        """
        if self.squashed:
            return {"color": bar_color, "line": {"width": 0}}
        if num_indices <= DEFAULT_PNG_THRESHOLD:
            return {"color": bar_color}
        return {"color": bar_color, "line": {"width": 0.2, "color": "rgba(0,0,0,0.025)"}}

    def _build(self, results: InstrumentationResults) -> TimelineResult | None:
        """Build the plotly bar chart figure (& compute the metadata) for the given results."""
        # Get the job & scheduler timing info, and check enough data exists to build a graph.
        job_timings = results.job_timings()
        if not job_timings:
            return None
        jobs_by_start_time = sorted(job_timings.items(), key=lambda kv: kv[1].start_time)
        run_start_time, run_end_time = results.get_run_time_info()

        # Determine the index (slot) of each bar by start time
        if self.squashed:
            job_indices, num_indices = self._assign_parallel_indices(jobs_by_start_time)
        else:
            job_indices = {job_id: i for i, (job_id, _) in enumerate(jobs_by_start_time)}
            num_indices = len(jobs_by_start_time)

        # If any relevant job metadata exists, split bars into subsets keyed by the target.
        categories: dict[str, list[str]] = defaultdict(list)
        for job_id in job_indices:
            metadata = results.jobs[job_id].meta
            key = "all" if metadata is None else metadata.target
            categories[key].append(job_id)
        categories = dict(sorted(categories.items()))
        color_map = make_repeating_color_map(categories, pc.qualitative.Plotly)

        # Determine scaling factors so the bars remain visible for large numbers of jobs.
        clamped_height = self._compute_chart_height(num_indices)
        bar_width = self._compute_bar_thickness(num_indices)

        # Render the chart itself
        fig = go.Figure()
        for key, jobs in categories.items():
            bar_color = color_map[key]
            marker_info = self._get_marker_info(num_indices, bar_color)

            durations, start_times, hovers, indices = [], [], [], []
            for job_id in jobs:
                timings = job_timings[job_id]
                metadata = results.jobs[job_id].meta
                index = job_indices.get(job_id, 0)

                start_time_offset = timings.start_time - run_start_time
                end_time_offset = timings.end_time - run_start_time
                extra_timing_info = {
                    "duration": format_time_metric(timings.duration),
                    "start_time": format_time_metric(start_time_offset),
                    "end time": format_time_metric(end_time_offset),
                }
                if self.squashed:
                    extra_timing_info["parallel slot"] = str(index)
                hover_data = make_job_metadata_hover(job_id, extra_timing_info, metadata)

                durations.append(timings.duration)
                start_times.append(timings.start_time - run_start_time)
                hovers.append(hover_data)
                indices.append(index)

            fig.add_bar(
                x=durations,
                y=indices,
                base=start_times,
                name=key,
                orientation="h",
                width=bar_width,
                marker=marker_info,
                customdata=hovers,
                hovertemplate="%{customdata}<extra></extra>",
            )

        # Extra layout / formatting settings
        fig.update_layout(
            template="plotly_white",
            margin=self.margins,
            height=clamped_height,
        )
        fig.update_legends(title="Job Target")
        fig.update_yaxes(title="Job", autorange="reversed")
        fig.update_xaxes(showgrid=True, **PLOTLY_TIMING_AXIS_CONFIG)

        # For squashed charts, use 'overlay' mode so different targets/subsets
        # in the same index are rendered with the same vertical offset
        if self.squashed:
            fig.update_layout(barmode="overlay")

        # Enforce linear integer tick scaling for small numbers of indices to prevent automatic
        # interpolation of non-integer ticks.
        linear_tick_threshold = 10
        if num_indices <= linear_tick_threshold:
            fig.update_yaxes(tickmode="linear", tick0=1, dtick=1)
        else:
            fig.update_yaxes(tickmode="auto", tickformat=",")

        return TimelineResult(
            fig=fig,
            meta=TimelineMeta(
                num_jobs=len(jobs_by_start_time),
                num_indices=num_indices,
                run_duration=(run_end_time - run_start_time),
            ),
        )

    def render(self, results: InstrumentationResults) -> str | None:
        """Render a bar chart visualization from the instrumentation results as a HTML fragment.

        If the required job timing information is not available (or there are no jobs), just
        returns `None` instead.

        """
        build_output = self._build(results)
        if build_output is None:
            return None

        fig = build_output.fig
        fig.update_layout(
            title_text=(
                f"<b>Gantt chart of {build_output.meta.num_jobs:,} scheduled jobs "
                f"({format_time(build_output.meta.run_duration, omit_zero=True)} run length)</b>"
            ),
            title_x=0.5,
        )

        return render_large_figure(
            fig,
            num_points=build_output.meta.num_jobs,
            interactivity_limit=self.png_threshold,
            # If rendering as PNG, use a 2:1 aspect ratio.
            png_width=DEFAULT_VISUALIZATION_HEIGHT_PX * 2,
            png_height=DEFAULT_VISUALIZATION_HEIGHT_PX,
        )

    @classmethod
    def for_profile(cls, profile: RenderProfile) -> Self:
        """Create a visualizer instance configured for a given rendering profile."""
        if profile == RenderProfile.HIGH:
            png_threshold = DEFAULT_PNG_THRESHOLD * 10
            log.debug(
                "Using render profile '%s' for '%s' visualization. Setting PNG threshold to %d.",
                profile.name,
                cls.title,
                png_threshold,
            )
            return cls(png_threshold=png_threshold)
        if profile == RenderProfile.FULL:
            log.debug(
                "Using render profile '%s' for '%s' visualization. Disabling PNG threshold.",
                profile.name,
                cls.title,
            )
            return cls(png_threshold=None)
        return cls()


class GanttChart(TimelineBarChart):
    """Gantt chart showing the progression of jobs that are scheduled over the run's lifetime."""

    title = "Job Timeline"

    def __init__(self, png_threshold: int | None = DEFAULT_PNG_THRESHOLD) -> None:
        """Construct a GanttChart.

        Args:
            png_threshold: If more than this many bars are provided, the graph will be rendered as
              a PNG for space/performance optimization. If `None`, this will never happen.

        """
        super().__init__(squashed=False, apply_bar_scaling=True, png_threshold=png_threshold)

    def render(self, results: InstrumentationResults) -> str | None:
        """Render a Gantt chart visualization from the instrumentation results as a HTML fragment.

        If the required job timing information is not available (or there are no jobs), just
        returns `None` instead.

        """
        build_output = self._build(results)
        if build_output is None:
            return None

        fig = build_output.fig
        fig.update_layout(
            title_text=(
                f"<b>Gantt chart of {build_output.meta.num_jobs:,} scheduled jobs "
                f"({format_time(build_output.meta.run_duration, omit_zero=True)} run length)</b>"
            ),
            title_x=0.5,
        )

        return render_large_figure(
            fig,
            num_points=build_output.meta.num_jobs,
            # If rendering as PNG, use a 2:1 aspect ratio.
            png_width=DEFAULT_VISUALIZATION_HEIGHT_PX * 2,
            png_height=DEFAULT_VISUALIZATION_HEIGHT_PX,
        )


class ParallelismChart(TimelineBarChart):
    """A squashed timeline that shows the (simulated) parallelism in scheduling a run's jobs."""

    title = "Job Parallelism"

    def __init__(self, png_threshold: int | None = DEFAULT_PNG_THRESHOLD) -> None:
        """Construct a class ParallelismChart(TimelineBarChart):.

        Args:
            png_threshold: If more than this many bars are provided, the graph will be rendered as
              a PNG for space/performance optimization. If `None`, this will never happen.

        """
        super().__init__(squashed=True, apply_bar_scaling=True, png_threshold=png_threshold)

    def render(self, results: InstrumentationResults) -> str | None:
        """Render a parallelism visualization from the instrumentation results as a HTML fragment.

        Also computes some additional metrics and appends them as simple paragraphs at the end
        of the fragment. If the required job timing information is not available (or there are no
        jobs), just returns `None` instead.

        """
        build_output = self._build(results)
        if build_output is None:
            return None

        fig = build_output.fig
        fig.update_layout(
            title_text=(
                f"<b>Job Parallelism Visualization "
                f"({format_time(build_output.meta.run_duration, omit_zero=True)} run length)</b>"
            ),
        )
        fig.update_yaxes(title="Parallel slot")

        rendered_fig = render_large_figure(
            fig,
            num_points=build_output.meta.num_jobs,
            # If rendering as PNG, use a 2:1 aspect ratio.
            png_width=DEFAULT_VISUALIZATION_HEIGHT_PX * 2,
            png_height=DEFAULT_VISUALIZATION_HEIGHT_PX,
        )

        # Add some additional metrics (as text) describing the scheduling efficiency
        available_compute_time = build_output.meta.num_indices * build_output.meta.run_duration
        if available_compute_time == 0:
            return rendered_fig
        useful_work_time = sum(
            job.timing.duration
            for job in results.jobs.values()
            if job.timing is not None and job.timing.duration is not None
        )
        utilization = useful_work_time / available_compute_time
        metrics = {
            "Degree of parallelism": str(build_output.meta.num_indices),
            "Wallclock time": format_time_metric(build_output.meta.run_duration, omit_zero=True),
            "Available compute time": format_time_metric(available_compute_time, omit_zero=True),
            "Time running jobs": format_time_metric(useful_work_time, omit_zero=True),
            "Parallel utilization": f"{utilization:.3%}",
        }
        rendered_fig += (
            "<p>" + "<br>".join(f"<b>{key}</b>: {value}" for key, value in metrics.items()) + "</p>"
        )
        return rendered_fig
