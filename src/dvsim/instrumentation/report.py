# Copyright lowRISC contributors (OpenTitan project).
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""DVSim scheduler instrumentation reporting & visualizations."""

import base64
import colorsys
import heapq
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import plotly.colors as pc
import plotly.graph_objects as go
from plotly.graph_objs import Figure
from plotly.subplots import make_subplots

from dvsim.instrumentation import (
    InstrumentationResults,
    JobInstrumentationMetadata,
    JobTimingMetrics,
    SchedulerTimingMetrics,
)
from dvsim.instrumentation.records import JobInstrumentationResults
from dvsim.logging import log
from dvsim.report.artifacts import ReportArtifacts, render_static_content
from dvsim.templates.render import render_template

__all__ = (
    "InstrumentationVisualizer",
    "get_visualization_registry",
    "make_job_metadata_hover",
    "register_instrumentation_visualizer",
    "render_figure",
    "render_html_report",
)


# The default figure height in pixels that visualizations should target, if possible
MAX_VISUALIZATION_HEIGHT_PX: int = 1000

# The number of jobs above which graphs should be rendered as encoded PNGs, instead of dynamic HTML
GRAPH_PNG_THRESHOLD: int = 1000

# The rendering configuration to use when rendering a graph as a PNG
PNG_SCALE_SQRT_DIVIDER: int = 1000
PNG_SCALE_FACTOR: float = 2.0


class InstrumentationVisualizer(Protocol):
    """TODO"""

    title: str

    def render(self, results: InstrumentationResults) -> str | None:
        """TODO"""
        ...


_vis_registry: list[InstrumentationVisualizer] = []


def register_instrumentation_visualizer(vis: InstrumentationVisualizer) -> None:
    """TODO"""
    _vis_registry.append(vis)


def get_visualization_registry() -> list[InstrumentationVisualizer]:
    """TODO"""
    return list(_vis_registry)


def render_html_report(
    results: InstrumentationResults,
    *,
    visualizations: Sequence[InstrumentationVisualizer] | None = None,
    outdir: Path | None = None,
) -> ReportArtifacts:
    """TODO"""
    # TODO: any nice way to check the plotly version matches the minified JS. Simple comparison?
    log.info("Rendering instrumentation HTML report...")

    if visualizations is None:
        visualizations = get_visualization_registry()

    renders: list[tuple[InstrumentationVisualizer, str]] = []
    for i, vis in enumerate(visualizations, start=1):
        log.debug(
            "Attempting to render instrumentation visualization: %s [%d/%d]",
            vis.title,
            i,
            len(visualizations),
        )
        render = vis.render(results)
        if render is not None:
            log.debug("Rendered instrumentation visualization: %s", vis.title)
            renders.append((vis, render))

    if outdir is not None:
        outdir.mkdir(parents=True, exist_ok=True)

    artifacts = {}

    # Render the visualizations to a single metrics.html file
    artifacts["metrics.html"] = render_template(
        path="reports/instrumentation_report.html", data={"renders": renders}
    )
    if outdir is not None:
        (outdir / "metrics.html").write_text(artifacts["metrics.html"])

    # Render static content needed for the report
    artifacts.update(
        render_static_content(
            static_files=[
                "css/style.css",
                "css/bootstrap.min.css",
                "js/bootstrap.bundle.min.js",
                "js/htmx.min.js",
            ]
            + (["js/plotly.min.js"] if renders else []),
            outdir=outdir,
        )
    )

    return artifacts


def render_large_figure(
    fig: Figure,
    *,
    num_points: int | None = None,
    interactivity_limit: int = GRAPH_PNG_THRESHOLD,
    png_width: int | None = None,
    png_height: int | None = None,
) -> str:
    """TODO"""
    if num_points is None or num_points <= interactivity_limit:
        return fig.to_html(full_html=False, include_plotlyjs=False)
    log.debug(
        "Plotly figure with %d points is larger than threshold %d.", num_points, interactivity_limit
    )

    width = fig.layout.width if png_width is None else png_width
    width = MAX_VISUALIZATION_HEIGHT_PX if width is None else width
    height = fig.layout.height if png_height is None else png_height
    height = MAX_VISUALIZATION_HEIGHT_PX if height is None else height

    log.debug(
        "Rendering the figure as a PNG with dimensions (%dx%d) with scale %g...",
        width,
        height,
        PNG_SCALE_FACTOR,
    )

    png = fig.to_image(format="png", width=width, height=height, scale=PNG_SCALE_FACTOR)
    b64 = base64.b64encode(png).decode("ascii")
    return (
        f'<img src="data:image/png;base64,{b64}" '
        f'     onclick="window.open(this.src)" '
        f'     style="max-height:{MAX_VISUALIZATION_HEIGHT_PX}px; height: auto; '
        f'            width: 100%; cursor: zoom-in;" />'
        f'<div style="font-size: 0.9em;">'
        f"  Click to open the full-resolution image"
        f"</div>"
    )


# TODO: maybe move these to some common utils


def _format_time(seconds: int, *, omit_zero: bool = False) -> str:
    """Format some runtime like '12h 34m 56.79s'."""
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if omit_zero and hours == 0 and minutes == 0:
        return f"{secs:.2f}s"
    elif omit_zero and hours == 0:
        return f"{int(minutes)}m {secs:.2f}s"
    return f"{int(hours)}h {int(minutes)}m {secs:.2f}s"


def _ordinal_suffix(n: int) -> str:
    """Suffix for some ordinal (positive int), e.g. 'st' for 1st, 'th' for 11th, 'rd' for 33rd."""
    if n in (11, 12, 13):
        return "th"

    suffixes = ("st", "nd", "rd")
    return suffixes[n % 10 - 1] if n % 10 in (1, 2, 3) else "th"


def make_job_metadata_hover(
    job_id: str,
    extra: Iterable[str] | Mapping[str, str] | None,
    metadata: JobInstrumentationMetadata | None,
    *,
    omit_status: bool = False,
) -> str:
    """TODO"""
    lines = [f"<b>{job_id}</b>"]

    if extra is not None:
        if isinstance(extra, Mapping):
            for key, value in extra.items():
                lines.append(f"{key.capitalize()}: {value}")
        else:
            lines += list(extra)

    if metadata is not None:
        block_info = metadata.block
        if metadata.block_variant:
            block_info += f" ({metadata.block_variant})"
        lines += [
            "------------------",
            f"Name: {metadata.name}",
            f"Tool: {metadata.tool}",
            f"Block: {block_info}",
            f"Target: {metadata.target}",
        ]
        if not omit_status:
            lines.append(f"Status: {metadata.status}")
        if metadata.backend is not None:
            lines.append(f"Backend: {metadata.backend}")

    return "<br>".join(lines)


def make_repeating_color_map(data: Iterable[str], colors: Iterable[str]) -> dict[str, str]:
    """TODO"""
    data = list(data)
    colors = list(colors)
    while len(colors) < len(data):
        colors += colors.copy()
    return {item: color for item, color in zip(data, colors, strict=False)}


def get_run_time_info(
    scheduler_timing: SchedulerTimingMetrics, job_timings: dict[str, JobTimingMetrics]
) -> tuple[float, float]:
    """TODO"""
    if scheduler_timing is None or scheduler_timing.start_time is None:
        return min(timing.start_time for timing in job_timings.values()), max(
            timing.end_time for timing in job_timings.values()
        )
    else:
        return scheduler_timing.start_time, scheduler_timing.end_time


@dataclass(frozen=True)
class JobBarMeta:
    """TODO"""

    num_jobs: int
    num_indices: int
    run_duration: float


@dataclass(frozen=True)
class JobBarResult:
    """TODO"""

    fig: Figure
    meta: JobBarMeta


class JobBarVisualization:
    """TODO"""

    def __init__(
        self,
        squashed: bool,
        apply_bar_scaling: bool,
        bar_px_range: tuple[int, int],
        margins: dict[str, int],
    ) -> None:
        """TODO

        apply_bar_scaling: scale bar thickness for graphs with too many bars
        bar_px_range: (min, max)
        margins: (t, b, l, r)
        """
        self.squashed = squashed
        self.apply_bar_scaling = apply_bar_scaling
        self.min_bar_px = bar_px_range[0]
        self.max_bar_px = bar_px_range[1]
        self.margins = margins

    def _assign_parallel_slots(
        self, jobs_by_start_time: list[tuple[str, JobTimingMetrics]]
    ) -> tuple[dict[str, int], int]:
        """TODO. Interval partitioning problem, sort of."""
        heap: list[tuple[float, int]] = []  # heap of (end time, slot ID)
        assignments: dict[str, int] = {}  # assignments of (job ID -> slot ID)
        next_slot_id = 0

        # Greedy assignment (same approach as interval partitioning problem)
        for job_id, timing in jobs_by_start_time:
            if heap and heap[0][0] <= timing.start_time:
                _, slot = heapq.heappop(heap)
            else:
                slot = next_slot_id
                next_slot_id += 1
            heapq.heappush(heap, (timing.end_time, slot))
            assignments[job_id] = slot

        return assignments, next_slot_id

    def _compute_bar_thickness(self, num_indices: int) -> int:
        """Compute the bar thickness (in visual units) to use for this chart.

        Below a configured threshold (GRAPH_PNG_THRESHOLD) we always render at a minimum width.
        After this 'knee', we linearly scale the width to ensure visibility for large amounts.

        """
        if not self.apply_bar_scaling or num_indices <= GRAPH_PNG_THRESHOLD:
            return 1.0
        scaled = num_indices / MAX_VISUALIZATION_HEIGHT_PX * self.min_bar_px
        return max(1.0, scaled)

    def _get_marker_info(self, num_indices: int, bar_color: str) -> dict[str, Any]:
        """Get the bar marker information to use for this chart.

        Below a configured threshold (GRAPH_PNG_THRESHOLD), we render as normal. When the number of
        jobs/indices exceeds this threshold, we make bar outlines less distinctive to avoid small
        bars combining to blot out parts of the graph.

        TODO update this comment to match the new implementation

        """
        if self.squashed:
            return dict(color=bar_color, line=dict(width=0))
        if num_indices <= GRAPH_PNG_THRESHOLD:
            return dict(color=bar_color)
        return dict(color=bar_color, line=dict(width=0.2, color="rgba(0,0,0,0.025)"))

    def build(self, results: InstrumentationResults) -> JobBarResult | None:
        """TODO"""
        # Get job & scheduler runtime info, and check enough times exist to render a graph (>= 1)
        job_timings = {
            job_id: job.timing for job_id, job in results.jobs.items() if job.timing is not None
        }
        if not job_timings:
            return None

        jobs_by_start_time = sorted(job_timings.items(), key=lambda kv: kv[1].start_time)
        run_start_time, run_end_time = get_run_time_info(results.scheduler.timing, job_timings)

        # Determine the index/slot of each bar by start time
        if self.squashed:
            job_indices, num_indices = self._assign_parallel_slots(jobs_by_start_time)
        else:
            job_indices = {job_id: i for i, (job_id, _) in enumerate(jobs_by_start_time)}
            num_indices = len(jobs_by_start_time)

        # If any relevant job metadata exists, split bars into subsets keyed by the target.
        subsets: dict[str, list[str]] = defaultdict(lambda: [])
        for job_id in job_indices:
            metadata = results.jobs[job_id].meta
            key = "all" if metadata is None else metadata.target
            subsets[key].append(job_id)
        color_map = make_repeating_color_map(sorted(subsets), pc.qualitative.Plotly)

        # Determine scaling factors so the bars remain visible for large numbers of jobs.
        tb_margins = self.margins.get("t", 0) + self.margins.get("b", 0)
        height = num_indices * self.max_bar_px + tb_margins
        clamped_height = min(MAX_VISUALIZATION_HEIGHT_PX, height)
        bar_width = self._compute_bar_thickness(num_indices)

        # Render the chart itself
        fig = go.Figure()
        for key, jobs in subsets.items():
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
                    "duration": f"{_format_time(timings.duration)} ({timings.duration:.2f}s)",
                    "start time": f"{_format_time(start_time_offset)} ({timings.start_time:.2f})",
                    "end time": f"{_format_time(end_time_offset)} ({timings.end_time:.2f})",
                }
                if self.squashed:
                    extra_timing_info["parallel slot"] = index
                hover_data = make_job_metadata_hover(job_id, extra_timing_info, metadata)

                durations.append(timings.duration)
                start_times.append(timings.start_time - run_start_time)
                hovers.append(hover_data)
                indices.append(index)

            fig.add_bar(
                x=durations,
                y=indices,
                base=start_times,
                orientation="h",
                width=bar_width,
                name=key,
                marker=marker_info,
                customdata=hovers,
                hovertemplate="%{customdata}<extra></extra>",
            )

        # Extra layout / formatting settings
        fig.update_layout(
            template="plotly_white",
            margin=self.margins,
            title_x=0.5,
            height=clamped_height,
        )
        fig.update_legends(title="Job Target")
        fig.update_yaxes(autorange="reversed", title="Job")
        fig.update_xaxes(
            title="Time (s)",
            tickformat=",",
            ticks="outside",
            tickwidth=1,
            tickcolor="black",
            ticklen=4,
            showgrid=True,
        )

        # For squashed/parallel charts, use overlay mode so different targets/subsets
        # in the same index are rendered with the same vertical offset
        if self.squashed:
            fig.update_layout(barmode="overlay")

        # Prevent interpolating non-integer ticks for small `num_indices`
        linear_tick_threshold = 10
        if num_indices <= linear_tick_threshold:
            fig.update_yaxes(tickmode="linear", tick0=1, dtick=1)
        else:
            fig.update_yaxes(tickmode="auto", tickformat=",")

        return JobBarResult(
            fig=fig,
            meta=JobBarMeta(
                num_jobs=len(jobs_by_start_time),
                num_indices=num_indices,
                run_duration=(run_end_time - run_start_time),
            ),
        )


class TimingGanttVisualization:
    """TODO"""

    title = "Job Timeline"

    # TODO (also, should this inherit somehow to pick up these defaults? Or should they live elsewhere?)
    MIN_BAR_PX: int = 4
    MAX_BAR_PX: int = 50
    MARGINS: dict[str, int] = dict(t=80, b=40, l=50, r=20)

    def __init__(self) -> None:
        """TODO"""
        # TODO: remove the builder pattern, just inherit from it...
        self.builder = JobBarVisualization(
            squashed=False,
            apply_bar_scaling=True,
            bar_px_range=(self.MIN_BAR_PX, self.MAX_BAR_PX),
            margins=self.MARGINS,
        )

    def render(self, results: InstrumentationResults) -> str | None:
        """TODO"""
        build_output = self.builder.build(results)
        if build_output is None:
            return None

        fig = build_output.fig
        fig.update_layout(
            title_text=(
                f"<b>Gantt chart of {build_output.meta.num_jobs:,} scheduled jobs "
                f"({_format_time(build_output.meta.run_duration, omit_zero=True)} run length)</b>"
            ),
        )

        return render_large_figure(
            fig,
            num_points=build_output.meta.num_jobs,
            png_width=MAX_VISUALIZATION_HEIGHT_PX * 2,
            png_height=MAX_VISUALIZATION_HEIGHT_PX,
        )


class ParallelismVisualization:
    """TODO"""

    title = "Job Parallelism"

    # TODO (also, should this inherit somehow to pick up these defaults? Or should they live elsewhere?)
    MIN_BAR_PX: int = 4
    MAX_BAR_PX: int = 50
    MARGINS: dict[str, int] = dict(t=80, b=40, l=50, r=20)

    def __init__(self) -> None:
        """TODO"""
        self.builder = JobBarVisualization(
            squashed=True,
            apply_bar_scaling=True,
            bar_px_range=(self.MIN_BAR_PX, self.MAX_BAR_PX),
            margins=self.MARGINS,
        )

    def render(self, results: InstrumentationResults) -> str | None:
        """TODO"""
        build_output = self.builder.build(results)
        if build_output is None:
            return None

        fig = build_output.fig
        fig.update_layout(
            title_text=(
                f"<b>Job Parallelism Visualization "
                f"({_format_time(build_output.meta.run_duration, omit_zero=True)} run length)</b>"
            ),
        )
        fig.update_yaxes(title="Parallel slot")

        rendered_fig = render_large_figure(
            fig,
            num_points=build_output.meta.num_jobs,
            png_width=MAX_VISUALIZATION_HEIGHT_PX * 2,
            png_height=MAX_VISUALIZATION_HEIGHT_PX,
        )

        # Add some additional metrics describing the scheduling efficiency
        available_compute_time = build_output.meta.num_indices * build_output.meta.run_duration
        if available_compute_time == 0:
            return rendered_fig
        useful_work_time = sum(
            job.timing.duration
            for job in results.jobs.values()
            if job.timing is not None and job.timing.duration is not None
        )
        utilization = useful_work_time / available_compute_time
        rendered_fig += (
            f"<p><b>Degree of parallelism</b>: {build_output.meta.num_indices}<br>"
            f"<b>Wallclock runtime</b>: {_format_time(build_output.meta.run_duration, omit_zero=True)}"
            f" ({build_output.meta.run_duration:,.2f}s)<br>"
            f"<b>Available compute time</b>: {_format_time(available_compute_time, omit_zero=True)}"
            f" ({available_compute_time:,.2f}s)<br>"
            f"<b>Time running jobs</b>: {_format_time(useful_work_time, omit_zero=True)}"
            f" ({useful_work_time:,.2f}s)<br>"
            f"<b>Parallel utilization</b>: {utilization * 100:.3f}%</p>"
        )

        return rendered_fig


class LongestJobsVisualization:
    """TODO"""

    title = "Longest Jobs"

    MARGINS: dict[str, int] = {"t": 100, "b": 40, "l": 160, "r": 40}
    MAX_NAME_CHARS: int = 40

    def __init__(
        self,
        *,
        max_bars: int | None = None,
        max_jobs_per_bar: int | None = None,
        category_fn: Callable[[JobInstrumentationResults], str | None] | None = None,
        test_group_fn: Callable[[JobInstrumentationResults], str | None] | None = None,
        color_fn: Callable[[str], Any] | None = None,
        allow_missing_meta: bool = False,
    ) -> None:
        """TODO"""
        if test_group_fn is None and max_jobs_per_bar:
            log.warning("Setting max_jobs_per_bar is meaningless without grouping jobs into tests")

        self.max_bars = max_bars
        self.max_jobs_per_bar = max_jobs_per_bar
        self.category_fn = category_fn
        self.test_group_fn = test_group_fn
        self.color_fn = color_fn
        self.allow_missing_meta = allow_missing_meta

    def _trace_visibility(self, num_traces: int, trace_idx: int) -> list[bool]:
        """TODO"""
        return [i == trace_idx for i in range(num_traces)]

    def render(self, results: InstrumentationResults) -> str | None:
        """TODO"""
        job_timings = {
            job_id: job.timing for job_id, job in results.jobs.items() if job.timing is not None
        }
        if not job_timings:
            return None
        if not self.allow_missing_meta and all(job.meta is None for job in results.jobs.values()):
            return None

        # TODO: repetition, refactor
        jobs_by_longest_duration = sorted(
            job_timings.items(), key=lambda kv: kv[1].duration, reverse=True
        )

        # Group items
        jobs_per_group: dict[str, list[str]] = defaultdict(lambda: [])
        group_durations: dict[str, float] = defaultdict(float)
        for job_id, timing in jobs_by_longest_duration:
            if self.test_group_fn is not None:
                group = self.test_group_fn(results.jobs[job_id])
                jobs_per_group[group].append(job_id)
                group_durations[group] += timing.duration
            else:
                jobs_per_group[job_id].append(job_id)
                group_durations[job_id] = timing.duration

        # TODO: reorganize some of this logic to make it cleaner
        num_jobs = len(job_timings)
        num_groups = len(group_durations)
        item_type = "jobs" if self.test_group_fn is None else "tests"
        if num_groups <= self.max_bars:
            num_visible_items = num_groups
            y_range = None
            title_job_desc = f"{num_groups:,} {item_type}"
        else:
            num_visible_items = self.max_bars
            y_range = [num_visible_items - 0.5, -0.5]  # TODO, is this needed still
            title_job_desc = f"top {num_visible_items} of {num_groups} {item_type}"

        # TODO: repetition, refactor
        groups_by_longest_duration = sorted(
            group_durations.items(), key=lambda kv: kv[1], reverse=True
        )
        group_ordinals = {
            group: i for i, (group, _) in enumerate(groups_by_longest_duration, start=1)
        }

        # TODO comment this better
        top_by_category: dict[str, list[str]] = defaultdict(lambda: [])
        top_by_category["All categories"] = [
            group for (group, _) in groups_by_longest_duration[:num_visible_items]
        ]
        for group, _ in groups_by_longest_duration:
            if self.category_fn is not None:
                jobs = jobs_per_group[group]
                keys = {job_id: self.category_fn(results.jobs[job_id]) for job_id in jobs}
                unique_keys = set(keys.values())
                key = next(iter(unique_keys))
                if len(unique_keys) != 1:
                    # TODO: could combine keys into flaky for status?
                    log.error(
                        "Got multiple different categories for jobs in the same group: %s\n",
                        "Using the first key %s as a fallback.",
                        keys,
                        key,
                    )
                # TODO: handle none key better?
                if key is not None and len(top_by_category[key]) < num_visible_items:
                    top_by_category[key].append(group)
        categories: list[str] = sorted(top_by_category.keys())
        categories.remove("All categories")
        categories.insert(0, "All categories")
        num_categories = len(categories)
        if self.color_fn is None:
            color_map = make_repeating_color_map(sorted(categories), pc.qualitative.Plotly)
        else:
            color_map = {key: self.color_fn(key) for key in categories}

        traces: list[go.Bar] = []
        menu_buttons: list[dict[str, Any]] = []
        for i, key in enumerate(categories):
            groups = top_by_category[key]
            max_ordinal = max(group_ordinals[group] for group in groups)
            ordinal_len = len(str(max_ordinal))

            durations, hovers, job_names, marker_colors = [], [], [], []
            for group in groups:
                group_duration = group_durations[group]
                group_ordinal = group_ordinals[group]
                jobs = jobs_per_group[group]

                # TODO explain: unfortunately still need to be unique due to a limitation in plotly presentation.
                # Does not let us have auto tick scaling (which we need for the size of our data) with tickmode="array"
                # which we need to give custom names because job IDs are too long. So define shortened names that
                # are guaranteed to be unique by prefixing them with their ordinal position.
                id_prefix = (
                    group
                    if len(group) < self.MAX_NAME_CHARS
                    else group[: self.MAX_NAME_CHARS - 3] + "..."
                )
                display_name = f"{group_ordinal:0{ordinal_len}d}: {id_prefix} "

                # Display jobs within groups shortest to longest (shortest bars first)
                jobs = list(reversed(jobs))

                # It's too slow & too much data to display all the bars. Combine the smallest ones.
                max_jobs = self.max_jobs_per_bar or len(jobs)
                num_combined_jobs = len(jobs) - max_jobs + 1
                if num_combined_jobs <= 1:
                    num_combined_jobs = 0
                if num_combined_jobs > 1:
                    combined_jobs = jobs[:num_combined_jobs]
                    job_durations = [job_timings[job_id].duration for job_id in combined_jobs]
                    total_duration = sum(job_durations)
                    max_duration = max(job_durations)
                    min_duration = min(job_durations)
                    avg_duration = total_duration / num_combined_jobs
                    extra_timing_info = [
                        f"{num_combined_jobs} other jobs (combined)",
                        f"Number of seeds: {len(jobs)}",
                        f"Combined Duration: {_format_time(total_duration)} ({total_duration:.2f}s)",
                        f"Mean Duration: {_format_time(avg_duration)} ({avg_duration:.2f}s)",
                        f"Maximum Duration: {_format_time(max_duration)} ({max_duration:.2f}s)",
                        f"Minimum Duration: {_format_time(min_duration)} ({min_duration:.2f}s)",
                    ]
                    first_job = results.jobs[combined_jobs[0]]
                    hover = make_job_metadata_hover(group, extra_timing_info, first_job.meta)
                    # TODO: modularize with the logic to do this for groups from before?
                    # TODO: assumes first job is correct key for now, could also maybe precompute these somewhere
                    group_key = self.category_fn(first_job) if key == "All categories" else key

                    durations.append(total_duration)
                    hovers.append(hover)
                    job_names.append(display_name)
                    marker_colors.append(color_map.get(group_key))

                # Render the remainder of the jobs in full detail
                for job_id in jobs[num_combined_jobs:]:
                    timings = job_timings[job_id]

                    # TODO: probably can be dict now, not list?
                    extra_timing_info = []
                    if self.test_group_fn is not None:
                        extra_timing_info += [
                            f"Number of seeds: {len(jobs)}",
                            f"Test Duration (all seeds combined): {_format_time(group_duration)} ({group_duration:.2f}s)",
                            f"Test Ranking (all seeds combined): {group_ordinal}{_ordinal_suffix(group_ordinal)} longest",
                        ]
                    extra_timing_info += [
                        f"Duration: {_format_time(timings.duration)} ({timings.duration:.2f}s)",
                    ]
                    hover = make_job_metadata_hover(
                        job_id, extra_timing_info, results.jobs[job_id].meta
                    )
                    job_key = (
                        self.category_fn(results.jobs[job_id]) if key == "All categories" else key
                    )

                    durations.append(timings.duration)
                    hovers.append(hover)
                    job_names.append(display_name)
                    marker_colors.append(color_map.get(job_key))

            traces.append(
                go.Bar(
                    orientation="h",
                    x=durations,
                    y=job_names,
                    customdata=hovers,
                    marker_color=marker_colors,
                    hovertemplate="%{customdata}<extra></extra>",
                    visible=False,
                )
            )

            menu_buttons.append(
                dict(
                    label=key,
                    method="update",
                    args=[dict(visible=self._trace_visibility(num_categories, i))],
                )
            )

        traces[0].visible = True

        if num_categories <= 8:
            updatemenu_type = "buttons"
            updatemenu_direction = "right"
        else:
            updatemenu_type = "dropdown"
            updatemenu_direction = "down"

        fig = go.Figure(data=traces)
        fig.update_layout(
            template="plotly_white",
            title_text=f"<b>{item_type.capitalize()} by longest duration ({title_job_desc})</b>",
            title_y=0.97,
            title_x=0.5,
            title_xanchor="center",
            height=MAX_VISUALIZATION_HEIGHT_PX,  # TODO: modularize the bar chart scaling code and re-use it here
            margin=self.MARGINS,
            bargap=0.05,
            showlegend=False,
            # barmode="stack",  # TODO use and explain with grouped bars
            updatemenus=[
                dict(
                    type=updatemenu_type,
                    direction=updatemenu_direction,
                    bgcolor="rgba(230,230,230,0.9)",
                    bordercolor="rgba(0,0,0,0)",
                    borderwidth=0,
                    pad=dict(r=8, t=0, b=0),
                    font=dict(size=13),
                    showactive=True,
                    x=0.0,
                    xanchor="left",
                    y=1.055,  # TODO: magic constants
                    buttons=menu_buttons,
                )
            ],
        )
        fig.update_yaxes(autorange="reversed")
        fig.update_xaxes(title="Duration (s)", showgrid=True)
        if y_range:  # TODO: is still needed?
            fig.update_yaxes(range=y_range)

        # Assume figure is never large enough to render to PNG because we control the limit here.
        return fig.to_html(full_html=False, include_plotlyjs=False)


class LongestTestsVisualization(LongestJobsVisualization):
    """TODO"""

    def __init__(
        self,
        *,
        max_bars: int | None = None,
        max_jobs_per_bar: int | None = None,
        category_fn: Callable[[JobInstrumentationResults], str | None] | None = None,
        color_fn: Callable[[str], Any] | None = None,
        group_tests: bool = False,
        allow_missing_meta: bool = False,
    ) -> None:
        """TODO"""
        if allow_missing_meta and group_tests:
            log.warning("Metadata is required to group jobs into tests; requiring it anyway.")
            allow_missing_meta = False
        group_fn = self.get_job_test if group_tests else None
        super().__init__(
            max_bars=max_bars,
            max_jobs_per_bar=max_jobs_per_bar,
            category_fn=category_fn,
            test_group_fn=group_fn,
            color_fn=color_fn,
            allow_missing_meta=allow_missing_meta,
        )

    def get_job_test(self, job: JobInstrumentationResults) -> str | None:
        """TODO"""
        if job.meta is None:
            return
        variant_name = job.meta.block
        if job.meta.block_variant:
            variant_name += f"_{job.meta.block_variant}"
        return f"{variant_name}:{job.meta.name}"


class LongestJobsByStatusVisualization(LongestTestsVisualization):
    """TODO"""

    def __init__(self, *, max_bars: int | None = None) -> None:
        """TODO"""
        super().__init__(
            max_bars=max_bars,
            category_fn=self._get_job_status,
            color_fn=self._get_status_color,
            allow_missing_meta=True,
        )

    def _get_job_status(self, job: JobInstrumentationResults) -> str:
        """TODO"""
        if job.meta is None:
            return None
        return job.meta.status

    def _get_status_color(self, status: str) -> str:
        """TODO"""
        # TODO: can I get the status enum working in the Pydantic models so I can use it directly?
        color_mapping = {
            "Passed": "#04B34F",
            "Failed": "#BF1B00",
            "Killed": "#12436D",
        }
        return color_mapping.get(status, "#808080")


class LongestJobsByToolVisualization(LongestTestsVisualization):
    """TODO"""

    title = "Longest Jobs by Tool"

    def __init__(
        self,
        *,
        max_bars: int | None = None,
        max_jobs_per_bar: int | None = None,
        group_tests: bool = False,
    ) -> None:
        """TODO"""
        if group_tests:
            self.title = "Longest Tests by Tool"
        super().__init__(
            max_bars=max_bars,
            max_jobs_per_bar=max_jobs_per_bar,
            category_fn=self._get_job_tool,
            group_tests=group_tests,
        )

    def _get_job_tool(self, job: JobInstrumentationResults) -> str:
        """TODO"""
        if job.meta is None:
            return "Unknown"
        return job.meta.tool


class LongestJobsByBlockVisualization(LongestTestsVisualization):
    """TODO"""

    title = "Longest Jobs by Block"

    def __init__(
        self,
        *,
        max_bars: int | None = None,
        max_jobs_per_bar: int | None = None,
        group_tests: bool = False,
    ) -> None:
        """TODO"""
        if group_tests:
            self.title = "Longest Tests by Block"
        super().__init__(
            max_bars=max_bars,
            max_jobs_per_bar=max_jobs_per_bar,
            category_fn=self._get_job_block,
            group_tests=group_tests,
        )

    def _get_job_block(self, job: JobInstrumentationResults) -> str:
        """TODO"""
        if job.meta is None:
            return "Unknown"
        return job.meta.block


class ConcurrencyVisualization:
    """TODO"""

    title = "Job Concurrency"

    def __init__(
        self, *, group_fn: Callable[[JobInstrumentationResults], str | None] | None = None
    ) -> None:
        """TODO"""
        self.group_fn = group_fn

    def concurrency_events(
        self, job_timings: dict[str, JobTimingMetrics]
    ) -> list[tuple[float, int]]:
        """TODO"""
        start_times = [(timing.start_time, 1) for timing in job_timings.values()]
        end_times = [(timing.end_time, -1) for timing in job_timings.values()]
        job_events = sorted(start_times + end_times)

        concurrency_events = []
        concurrency = 0
        for event_time, concurrency_diff in job_events:
            concurrency += concurrency_diff  # cumulative sum so far
            concurrency_events.append((event_time, concurrency))

        return concurrency_events

    def _build(self, results: InstrumentationResults) -> Figure | None:
        """TODO"""
        job_timings = {
            job_id: job.timing for job_id, job in results.jobs.items() if job.timing is not None
        }
        if not job_timings:
            return None

        run_start_time, run_end_time = get_run_time_info(results.scheduler.timing, job_timings)

        # Group items into subsets
        subsets: dict[str, list[str]] = defaultdict(lambda: [])
        for job_id in job_timings:
            if self.group_fn is not None:
                # TODO: handle None better
                key = self.group_fn(results.jobs[job_id]) or "Unknown"
                subsets[key].append(job_id)
            else:
                subsets["Concurrent Jobs"].append(job_id)
        color_map = make_repeating_color_map(sorted(subsets), pc.qualitative.Plotly)

        # Get concurrency events and plot them on the graph
        # For each group, get concurrency events and plot them as a trace.
        fig = go.Figure()

        for subset, jobs in subsets.items():
            subset_timings = {job_id: job_timings[job_id] for job_id in jobs}
            concurrency_events = self.concurrency_events(subset_timings)

            fig.add_scatter(
                x=[event[0] - run_start_time for event in concurrency_events],
                y=[event[1] for event in concurrency_events],
                mode="lines",
                name=subset,
                marker=dict(color=color_map[subset]),
            )

        # Extra layout / formatting settings
        fig.update_layout(
            template="plotly_white",
            title_text="<b>Job Concurrency over Time</b>",
            title_x=0.5,
            margin=dict(t=40),
            height=min(800, MAX_VISUALIZATION_HEIGHT_PX),
            hovermode="x unified",
        )
        fig.update_yaxes(title="Number of Concurrent Jobs", tickformat=",", showgrid=True)
        fig.update_xaxes(
            title="Time (s)",
            tickformat=",",
            ticks="outside",
            tickwidth=1,
            tickcolor="black",
            ticklen=4,
            showgrid=True,
        )

        return fig

    def render(self, results: InstrumentationResults) -> str | None:
        """TODO"""
        fig = self._build(results)
        if fig is None:
            return None

        return fig.to_html(full_html=False, include_plotlyjs=False)


class ToolConcurrencyVisualization(ConcurrencyVisualization):
    """TODO"""

    title = "Tool Concurrency"

    def __init__(self) -> None:
        """TODO"""
        super().__init__(group_fn=self._get_job_tool)

    def _get_job_tool(self, job: JobInstrumentationResults) -> str:
        """TODO"""
        if job.meta is None:
            return "Unknown"

        return job.meta.tool

    def render(self, results: InstrumentationResults) -> str | None:
        """TODO"""
        if all(job.meta is None for job in results.jobs.values()):
            return None

        fig = self._build(results)
        if fig is None:
            return None

        fig.update_legends(title="Tool")

        return fig.to_html(full_html=False, include_plotlyjs=False)


class PieBreakdownVisualization:
    """TODO"""

    title = "Job Breakdown"

    MAX_BAR_PX: int = 50
    MARGINS: dict[str, int] = dict(t=80, b=40, l=50, r=20)

    def __init__(
        self, *, group_type: str, group_fn: Callable[[JobInstrumentationResults], str]
    ) -> None:
        """TODO"""
        self.group_type = group_type
        self.group_fn = group_fn

    def _build(self, results: InstrumentationResults) -> Figure | None:
        """TODO"""
        # TODO: need to add proper handling for missing timing data everywhere...
        # Should probably also make a nice function to handle this on the instrumentation report
        # (maybe even giving the stricter typing guarantees, something like with_data_present
        # - but maybe this is annoying in that I need to make a new model for it? I guess it'self
        # ironically easier not to pass around the models everywhere).
        job_timings = {
            job_id: job.timing for job_id, job in results.jobs.items() if job.timing is not None
        }
        if not job_timings:
            return None

        # Group items into subsets
        subsets: dict[str, list[str]] = defaultdict(lambda: [])
        subset_durations: dict[str, float] = defaultdict(float)
        for job_id in job_timings:
            key = self.group_fn(results.jobs[job_id])
            subsets[key].append(job_id)
            subset_durations[key] += job_timings[job_id].duration
        total_duration = sum(subset_durations.values())

        # Assign colors to categories; use larger palettes for variety if needed
        palette = pc.qualitative.Plotly
        if len(subsets) > len(palette):
            palette = pc.qualitative.Bold + pc.qualitative.Safe + pc.qualitative.Vivid
        if len(subsets) > len(palette):
            palette += pc.qualitative.D3 + pc.qualitative.Set1 + pc.qualitative.Set2
        color_map = make_repeating_color_map(sorted(subsets), pc.qualitative.Plotly)

        # Chart dimensions
        pie_chart_height = min(600, MAX_VISUALIZATION_HEIGHT_PX)
        tb_margins = self.MARGINS.get("t", 0) + self.MARGINS.get("b", 0)
        height = len(subsets) * self.MAX_BAR_PX + tb_margins
        bar_chart_height = min(
            MAX_VISUALIZATION_HEIGHT_PX * 2 - pie_chart_height, height
        )  # TODO: name of this
        total_height = pie_chart_height + bar_chart_height
        row_heights = [pie_chart_height / total_height, bar_chart_height / total_height]

        # Draw 2 subplots of the same data - a pie chart on the left & a bar chart on the right
        fig = make_subplots(
            rows=2, cols=1, row_heights=row_heights, specs=[[{"type": "pie"}], [{"type": "bar"}]]
        )

        subsets_by_duration = sorted(subset_durations.items(), key=lambda kv: kv[1], reverse=True)
        keys = [kv[0] for kv in subsets_by_duration]
        durations = [kv[1] for kv in subsets_by_duration]
        percentages = [duration / total_duration for duration in durations]
        colors = [color_map[key] for key in keys]
        pulls = [0.03 for _ in keys]
        display_text = [
            f"{key}<br>{pct:.2%}" if pct > 0.01 else ""
            for key, pct in zip(keys, percentages, strict=True)
        ]
        hovers = [
            (
                f"{self.group_type.capitalize()}: {key}<br>"
                f"Number of Jobs: {len(subsets[key])}<br>"
                f"Total Duration: {_format_time(duration, omit_zero=True)} ({duration:.2f}s)<br>"
                f"Percentage: {pct:.2%}"
            )
            for key, duration, pct in zip(keys, durations, percentages, strict=True)
        ]

        fig.add_trace(
            go.Pie(
                labels=keys,
                values=durations,
                text=display_text,
                hole=0.6,
                textinfo="text",
                textposition="outside",
                pull=pulls,
                marker=dict(colors=colors),
                customdata=hovers,
                hovertemplate="%{customdata}<extra></extra>",
                showlegend=False,
            ),
            row=1,
            col=1,
        )

        texts = [_format_time(duration, omit_zero=True) for duration in durations]
        for key, duration, text, color, hover in zip(
            keys, durations, texts, colors, hovers, strict=True
        ):
            fig.add_trace(
                go.Bar(
                    y=[key],
                    x=[duration],
                    name=key,
                    orientation="h",
                    text=[text],
                    textposition="outside",  # TODO
                    cliponaxis=False,
                    textangle=0,
                    marker=dict(color=color),
                    customdata=[hover],
                    hovertemplate="%{customdata}<extra></extra>",
                    showlegend=True,
                ),
                row=2,
                col=1,
            )

        # Extra layout / formatting settings
        fig.update_layout(
            template="plotly_white",
            title_text=f"<b>Total job runtime per {self.group_type.lower()}</b>",
            title_x=0.5,
            margin=self.MARGINS,
            height=total_height,
            bargap=0.1,
        )
        fig.update_legends(
            title=self.group_type.capitalize(),
            x=1.02,
            y=0.3,
            xanchor="left",
            yanchor="middle",
        )
        fig.update_yaxes(autorange="reversed")
        fig.update_xaxes(
            title="Time (s)",
            ticks="outside",
            tickwidth=1,
            tickcolor="black",
            ticklen=4,
            showgrid=True,
        )

        return fig

    def render(self, results: InstrumentationResults) -> str | None:
        """TODO"""
        fig = self._build(results)
        if fig is None:
            return None

        return fig.to_html(full_html=False, include_plotlyjs=False)


class ToolPieBreakdown(PieBreakdownVisualization):
    """TODO"""

    title = "Tool Breakdown"

    def __init__(self) -> None:
        """TODO"""
        super().__init__(group_type="tool", group_fn=self._get_job_tool)

    def _get_job_tool(self, job: JobInstrumentationResults) -> str:
        """TODO"""
        if job.meta is None:
            return "Unknown"

        return job.meta.tool

    def render(self, results: InstrumentationResults) -> str | None:
        """TODO"""
        if all(job.meta is None for job in results.jobs.values()):
            return None

        fig = self._build(results)
        if fig is None:
            return None

        fig.update_layout(title_text="<b>Total job runtime per tool</b>", title_x=0.5)
        fig.update_legends(title="Tool")

        return fig.to_html(full_html=False, include_plotlyjs=False)


class BlockVariantPieBreakdown(PieBreakdownVisualization):
    """TODO"""

    title = "Block Breakdown"

    def __init__(self) -> None:
        """TODO"""
        super().__init__(group_type="block", group_fn=self._get_job_block_variant)

    def _get_job_block_variant(self, job: JobInstrumentationResults) -> str:
        """TODO"""
        if job.meta is None:
            return "Unknown"

        return job.meta.block + (f"_{job.meta.block_variant}" if job.meta.block_variant else "")


# Register default / built-in instrumentation visualizations
# TODO: maybe look at reworking protocol and just give name here, the class structure is kind of weird
register_instrumentation_visualizer(LongestJobsByStatusVisualization(max_bars=250))
register_instrumentation_visualizer(LongestJobsByToolVisualization(max_bars=250))
register_instrumentation_visualizer(
    LongestJobsByToolVisualization(max_bars=75, max_jobs_per_bar=6, group_tests=True)
)
register_instrumentation_visualizer(LongestJobsByBlockVisualization(max_bars=50))
register_instrumentation_visualizer(
    LongestJobsByBlockVisualization(max_bars=20, max_jobs_per_bar=6, group_tests=True)
)
register_instrumentation_visualizer(BlockVariantPieBreakdown())
register_instrumentation_visualizer(ToolPieBreakdown())
register_instrumentation_visualizer(ToolConcurrencyVisualization())
register_instrumentation_visualizer(TimingGanttVisualization())
register_instrumentation_visualizer(ParallelismVisualization())


# Local testing, TODO remove the below


def _make_fake_results_for_testing(
    num_jobs: int = 500, parallel: bool = True
) -> InstrumentationResults:
    import random
    import string
    from datetime import datetime, timedelta, timezone

    from dvsim.instrumentation import (
        JobInstrumentationMetadata,
        JobTimingMetrics,
        SchedulerTimingMetrics,
    )
    from dvsim.instrumentation.records import (
        JobInstrumentationResults,
        SchedulerInstrumentationResults,
    )

    rng = random.Random(42)
    now = datetime(2026, 4, 30, 9, 0, 0, tzinfo=timezone.utc)

    job_timings: dict[str, JobTimingMetrics] = {}
    t = end = now

    for i in range(num_jobs):
        if rng.random() < 0.96:
            duration = max(0.05, rng.gauss(10, 5))
        else:
            duration = max(0.05, rng.gauss(1200, 600))
        end = t + timedelta(seconds=duration)
        job_timings[f"Job {i}"] = JobTimingMetrics(
            start_time=t.timestamp(), end_time=end.timestamp()
        )
        if (parallel and rng.random() < 0.1) or not parallel:
            t = end
    t = end

    job_metadata: dict[str, JobInstrumentationMetadata] = {}

    for i in range(num_jobs):
        target = random.choice(["default", "run", "cov_merge", "cov_report"])
        deps = set()
        while len(deps) < i and rng.random() < 0.6:
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
            block_variant=("abc" if rng.random() < 0.08 else None),
            backend="local",
            dependencies=[f"Job {dep}" for dep in deps],
            status=random.choice(["Passed", "Failed", "Killed"]),
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

    if len(sys.argv) > 1:
        results_list = [Path(arg) for arg in sys.argv[1:]]
        for results_path in results_list:
            if not results_path.exists():
                print(f"Skipping results file {results_path} which does not exist.")
                continue
            report = InstrumentationResults.model_validate_json(results_path.read_text())
            print(f"Finished loading given instrumentation report: {results_path}")
            _outdir = Path("./real_metrics/generated") / results_path.name
            _artifacts = render_html_report(
                report, visualizations=get_visualization_registry(), outdir=_outdir
            )
            print(f"Historic instrumentation report data written under {_outdir}")
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
            print(f"Making fake report with {_num_fake_jobs} jobs...")
            _fake_report = _make_fake_results_for_testing(num_jobs=_num_fake_jobs, parallel=True)
            print("Finished making fake report. Rendering HTML visualizations...")
            _outdir = Path("./mock_metrics", str(_num_fake_jobs).rjust(6, "0"))
            _artifacts = render_html_report(
                _fake_report, visualizations=get_visualization_registry(), outdir=_outdir
            )
            print(f"Fake instrumentation report data written under {_outdir}")
