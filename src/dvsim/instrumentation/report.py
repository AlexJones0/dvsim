# Copyright lowRISC contributors (OpenTitan project).
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""DVSim scheduler instrumentation reporting & visualizations."""

import base64
import heapq
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import plotly.colors as pc
import plotly.graph_objects as go
import plotly.offline
from plotly.graph_objs import Figure
from plotly.subplots import make_subplots

from dvsim.instrumentation import (
    ConcreteJobTimingMetrics,
    InstrumentationResults,
    JobInstrumentationMetadata,
    JobTimingMetrics,
    SchedulerTimingMetrics,
)
from dvsim.instrumentation.records import JobInstrumentationResults
from dvsim.logging import log
from dvsim.report.artifacts import ReportArtifacts, render_static_content
from dvsim.templates.render import render_template
from dvsim.utils import format_time_as_hms as format_time
from dvsim.utils import format_time_metric, ordinal_suffix

__all__ = (
    "InstrumentationVisualizer",
    "get_visualization_registry",
    "make_job_metadata_hover",
    "register_instrumentation_visualizer",
    "render_figure",
    "render_html_report",
)


# The default figure height in pixels that visualizations should target, if possible
DEFAULT_VISUALIZATION_HEIGHT_PX: int = 1000

# The number of jobs above which graphs should be rendered as encoded PNGs, instead of dynamic HTML
DEFAULT_PNG_THRESHOLD: int = 1000

# The rendering configuration to use when rendering a graph as a PNG
PNG_SCALE_SQRT_DIVIDER: int = 1000
PNG_SCALE_FACTOR: float = 2.0

# Standard plotly kwargs for rendering a HTML figure as a instrumentation report fragment.
PLOTLY_HTML_FRAGMENT_CONFIG: dict[str, Any] = {
    "full_html": False,
    "include_plotlyjs": False,
}

# Standard plotly timing tick config options
PLOTLY_TIMING_AXIS_CONFIG: dict[str, Any] = {
    "title": "Time (s)",
    "tickformat": ",",
    "ticks": "outside",
    "tickwidth": 1,
    "ticklen": 4,
    "tickcolor": "black",
}


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
            ],
            outdir=outdir,
        )
    )

    # Render static plotly.js separately
    if renders:
        plotly_js_path = "js/plotly.min.js"
        artifacts[plotly_js_path] = plotly.offline.get_plotlyjs()
        if outdir is not None:
            (outdir / plotly_js_path).write_text(artifacts[plotly_js_path])

    return artifacts


def render_large_figure(
    fig: Figure,
    *,
    num_points: int | None = None,
    interactivity_limit: int = DEFAULT_PNG_THRESHOLD,
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
    width = DEFAULT_VISUALIZATION_HEIGHT_PX if width is None else width
    height = fig.layout.height if png_height is None else png_height
    height = DEFAULT_VISUALIZATION_HEIGHT_PX if height is None else height

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
        f'     style="max-height:{DEFAULT_VISUALIZATION_HEIGHT_PX}px; height: auto; '
        f'            width: 100%; cursor: zoom-in;" />'
        f'<div style="font-size: 0.9em;">'
        f"  Click to open the full-resolution image"
        f"</div>"
    )


# TODO: maybe move these to some common utils


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


class TimelineBarChart:
    """Renders plotly bar chart figures showing scheduler job timeline information."""

    def __init__(
        self,
        *,
        squashed: bool,
        apply_bar_scaling: bool,
        bar_px_range: tuple[int, int] = (DEFAULT_MIN_BAR_PX, DEFAULT_MAX_BAR_PX),
    ) -> None:
        """Construct a TimelineBarChart.

        Args:
            squashed: If true, squash bars down into the minimum number of indices/slots required
              to render all bars without overlaps (as in interval partitioning).
            apply_bar_scaling: Enable the ability to automatically increase the bar thickness. This
              will make bars more visible on larger graphs, but will cause bars to overlap.
            bar_px_range: tuple of (min, max) range of pixels that each bar is allowed to occupy.

        """
        self.squashed: bool = squashed
        self.apply_bar_scaling: bool = apply_bar_scaling
        self.min_bar_px: int = bar_px_range[0]
        self.max_bar_px: int = bar_px_range[1]

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

        Below a configured threshold (DEFAULT_PNG_THRESHOLD) we always render at a minimum width.
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
            # If rendering as PNG, use a 2:1 aspect ratio.
            png_width=DEFAULT_VISUALIZATION_HEIGHT_PX * 2,
            png_height=DEFAULT_VISUALIZATION_HEIGHT_PX,
        )


class GanttChart(TimelineBarChart):
    """Gantt chart showing the progression of jobs that are scheduled over the run's lifetime."""

    title = "Job Timeline"

    def __init__(self) -> None:
        """Construct a GanttChart."""
        super().__init__(squashed=False, apply_bar_scaling=True)

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

    def __init__(self) -> None:
        """Construct a ParallelismChart."""
        super().__init__(squashed=True, apply_bar_scaling=True)

    def render(self, results: InstrumentationResults) -> str | None:
        """Render a parallelism visualization from the instrumentation results as a HTML fragment.

        If the required job timing information is not available (or there are no jobs), just
        returns `None` instead.

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


@dataclass(frozen=True)
class TestAggregateInfo:
    """Computed aggregate information about a test in a scheduler run."""

    name: str  # name of the test
    jobs: list[str]  # list of job IDs for different test seeds, sorted by duration, descending
    duration: float  # combined test duration, in seconds
    ordinal: int  # position / ranking relative to all tests, by decreasing duration
    timings: dict[str, ConcreteJobTimingMetrics]  # mapping from test jobs to their timings


class LongestBarChart:
    """Renders plotly bar chart figures ranking the longest jobs/tests in the scheduler."""

    title = "Longest Jobs"

    # Standard layout & formatting configuration
    MAX_NAME_CHARS: int = 40
    DROPDDOWN_KEY_THRESHOLD: int = 5
    ALL_KEY: str = "All categories"

    def __init__(  # noqa: PLR0913
        self,
        *,
        category_fn: Callable[[JobInstrumentationResults], str] | None = None,
        color_fn: Callable[[str], Any] | None = None,
        group_tests: bool = False,
        max_bars: int | None = None,
        max_jobs_per_bar: int | None = None,
        allow_missing_meta: bool = False,
        allow_isomorphic: bool = False,
    ) -> None:
        """Construct a LongestBarChart.

        Args:
            category_fn: Optional function to split jobs into groups by name. The top N items will
              be collected & displayed for each category, with a button to show each trace. If not
              provided, only an "overall" trace is shown.
            color_fn: Optional function to provide color mapping information for specific groups.
            group_tests: Flag to group jobs of the same test by name. This allows timing results
              for the same tests to be combined in a stacked bar view.
            max_bars: The maximum number of bars to show in each group (& overall).
            max_jobs_per_bar: If group_tests=True, the maximum number of stacked bars to
              render per bar. If there are too many bars to display, the bottom
              (N - max_jobs_per_bar + 1) jobs will be combined into a single bar.
            allow_missing_meta: Whether the figure should still render even if metadata is missing.
            allow_isomorphic: Whether the figure should still render even if the mapped tests are
              isomorphic to the jobs (i.e. each job is 1-to-1 with a test; there is no extra info
              provided by grouping the tests despite trying).

        """
        if not group_tests and max_jobs_per_bar:
            log.warning("Setting max_jobs_per_bar is meaningless without grouping jobs into tests")
        if not group_tests and allow_isomorphic:
            log.warning("Setting allow_isomorphic is meaningless without grouping jobs into tests")

        self.category_fn = category_fn
        self.color_fn = color_fn
        self.group_tests = group_tests
        self.max_bars = max_bars
        self.max_jobs_per_bar = max_jobs_per_bar
        self.allow_missing_meta = allow_missing_meta
        self.allow_isomorphic = allow_isomorphic

        # Default margin layout information
        self.margins = {"t": 100, "b": 40, "l": 160, "r": 40}

    def get_job_test(self, job_id: str, job: JobInstrumentationResults) -> str | None:
        """Get the test name for a given job (should be the same for all seeds of a given test)."""
        if job.meta is None:
            return None

        # Do not group non-test jobs (building, coverage merging and reporting).
        # These can be identified by their lack of seeds at the end of their IDs.
        if not job_id.strip().split(".")[-1].isdigit():
            return job_id

        variant_name = job.meta.block
        if job.meta.block_variant:
            variant_name += f"_{job.meta.block_variant}"
        return f"{variant_name}:{job.meta.name}"

    def _get_job_color(
        self, color_map: dict[str, str], job: JobInstrumentationResults, key: str
    ) -> str:
        """Get the color to render a job bar."""
        if self.category_fn is not None:
            return color_map[self.category_fn(job)]
        return color_map[key]

    def _compute_test_aggregates(
        self, results: InstrumentationResults
    ) -> dict[str, TestAggregateInfo]:
        """Combine jobs into tests (if enabled) and compute aggregate information about tests.

        Args:
            results: The instrumentation results to compute test aggregates for.

        Returns:
            A mapping of (test name -> aggregate info), ordered by decreasing duration.

        """
        job_timings = results.job_timings()
        if not job_timings:
            return {}

        longest_jobs = sorted(job_timings.items(), key=lambda kv: kv[1].duration, reverse=True)

        # Group jobs into tests if configured to do so
        jobs_per_test: dict[str, list[str]] = defaultdict(list)
        test_durations: dict[str, float] = defaultdict(float)
        for job_id, timing in longest_jobs:
            if self.group_tests:
                test_group = self.get_job_test(job_id, results.jobs[job_id]) or job_id
                jobs_per_test[test_group].append(job_id)
                test_durations[test_group] += timing.duration
            else:
                # If no test grouping function is given, tests are isomorphic to jobs
                jobs_per_test[job_id].append(job_id)
                test_durations[job_id] = timing.duration

        # If we get no additional data from grouping, we optionally stop rendering here.
        if self.group_tests and not self.allow_isomorphic:
            if len(jobs_per_test) == len(job_timings):
                log.debug("Not emitting grouped graph '%s' due to test isomorphism.", self.title)
                return {}

        longest_tests = sorted(test_durations.items(), key=lambda kv: kv[1], reverse=True)
        test_ordinals = {test_group: i for i, (test_group, _) in enumerate(longest_tests, start=1)}

        # Aggregate info about each test together, returning in order of decreasing duration
        return {
            test: TestAggregateInfo(
                name=test,
                jobs=jobs_per_test[test],
                duration=test_durations[test],
                ordinal=ordinal,
                timings={job: job_timings[job] for job in jobs_per_test[test]},
            )
            for test, ordinal in test_ordinals.items()
        }

    def _get_top_tests_by_category(
        self, results: InstrumentationResults, tests: dict[str, TestAggregateInfo], num_tests: int
    ) -> dict[str, list[str]]:
        """Split test items into categories and get the top N tests of each category.

        Args:
            results: The instrumentation results.
            tests: The mapping of tests computed from the instrumentation results.
            num_tests: The max number of tests to be computed for each category.

        Returns:
            A mapping of (category -> list of tests), where the list of tests are sorted in
            descending ranking order. The first category is a special case equivalent to the
            top N tests in *all* categories combined.

        """
        # Split items into categories and get the top items (tests) of each category.
        top_by_category: dict[str, list[str]] = defaultdict(list)
        top_by_category[self.ALL_KEY] = [t.name for t in list(tests.values())[:num_tests]]

        for test_group, test_info in tests.items():
            if self.category_fn is None:
                continue

            # If we are grouping jobs into tests, check that they all report the same category.
            # If they for some reason do not, warn and just choose the first one.
            keys = {job_id: self.category_fn(results.jobs[job_id]) for job_id in test_info.jobs}
            unique_keys = set(keys.values())
            test_group_key = next(iter(unique_keys))
            if len(unique_keys) != 1:
                log.error(
                    "Got multiple different categories for jobs in the same group: %s\n"
                    "Using the first key '%s' as a fallback.",
                    keys,
                    test_group_key,
                )
            if len(top_by_category[test_group_key]) < num_tests:
                top_by_category[test_group_key].append(test_group)

        return top_by_category

    def _make_combined_bar_info(
        self,
        test: TestAggregateInfo,
        jobs: list[str],
        meta: JobInstrumentationMetadata | None,
    ) -> tuple[float, str]:
        """Get job information for a bar that combines a subset of a test's jobs/seeds.

        Args:
            test: The test to create a combined bar for.
            jobs: The jobs (subset of test.jobs) to create a combined for.
            meta: Metadata about the test/jobs, if any exists.

        Returns:
            A tuple (combined duration, hover tooltip) for the combined bar.

        """
        if not jobs:
            raise ValueError("Cannot make a combined bar from a subset of no jobs.")

        num_combined_jobs = len(jobs)
        job_durations = [test.timings[job_id].duration for job_id in jobs]

        total_duration = sum(job_durations)
        max_duration = max(job_durations)
        min_duration = min(job_durations)
        avg_duration = total_duration / num_combined_jobs
        rank_str = f"{test.ordinal}{ordinal_suffix(test.ordinal)} longest"
        extra_timing_info = [
            f"{num_combined_jobs} other jobs (combined)",
            f"Number of seeds: {len(jobs)}",
            f"Test duration (all seeds combined): {format_time_metric(test.duration)}",
            f"Test ranking (all seeds combined): {rank_str}",
            f"Combined Duration: {format_time_metric(total_duration)}",
            f"Mean Duration: {format_time_metric(avg_duration)}",
            f"Maximum Duration: {format_time_metric(max_duration)}",
            f"Minimum Duration: {format_time_metric(min_duration)}",
        ]
        hover = make_job_metadata_hover(test.name, extra_timing_info, meta)

        return total_duration, hover

    def _make_trace(
        self,
        results: InstrumentationResults,
        test_info: list[TestAggregateInfo],
        color_map: dict[str, str],
    ) -> go.Bar:
        """Get a plotly bar trace for a group of tests using some defined color mapping."""
        max_ordinal = max(test.ordinal for test in test_info)
        ordinal_len = len(str(max_ordinal))

        job_names, durations, hovers, marker_colors = [], [], [], []
        for test in test_info:
            # Display jobs within groups shortest to longest (shortest bars first)
            jobs = list(reversed(test.jobs))
            first_job = results.jobs[jobs[0]]
            marker_color = self._get_job_color(color_map, first_job, test.name)

            # Due to a limitation in plotly's presentation, we cannot have auto tick scaling
            # (which is needed for the size of our data) with tickmode="array", which we also
            # need to be able to provide custom names, because job IDs are too long.
            # To work around this, we define shortened names that are guaranteed to be unique,
            # by prefixing them with their ordinal position (ranking).
            id_prefix = (
                test.name
                if len(test.name) < self.MAX_NAME_CHARS
                else test.name[: self.MAX_NAME_CHARS - 3] + "..."
            )
            display_name = f"{test.ordinal:0{ordinal_len}d}: {id_prefix} "

            # It's too slow & too much data to display all the bars. Combine the smallest ones.
            max_jobs = self.max_jobs_per_bar or len(jobs)
            num_combined_jobs = len(jobs) - max_jobs + 1
            if num_combined_jobs <= 1:
                num_combined_jobs = 0

            if num_combined_jobs > 1:
                meta = results.jobs[jobs[0]].meta
                duration, hover = self._make_combined_bar_info(test, jobs[:num_combined_jobs], meta)
                job_names.append(display_name)
                durations.append(duration)
                hovers.append(hover)
                marker_colors.append(marker_color)

            # Render the remainder of the jobs in full detail
            for job_id in jobs[num_combined_jobs:]:
                timings = test.timings[job_id]

                extra_timing_info = {}
                if self.group_tests:
                    rank_str = f"{test.ordinal}{ordinal_suffix(test.ordinal)} longest"
                    extra_timing_info.update(
                        {
                            "number of seeds": str(len(jobs)),
                            "test duration (all seeds combined)": format_time_metric(test.duration),
                            "test ranking (all seeds combined)": rank_str,
                        }
                    )
                extra_timing_info["duration"] = format_time_metric(timings.duration)
                meta = results.jobs[job_id].meta
                hover = make_job_metadata_hover(job_id, extra_timing_info, meta)

                job_names.append(display_name)
                durations.append(timings.duration)
                hovers.append(hover)
                marker_colors.append(marker_color)

        return go.Bar(
            orientation="h",
            x=durations,
            y=job_names,
            customdata=hovers,
            marker_color=marker_colors,
            hovertemplate="%{customdata}<extra></extra>",
            # traces are all hidden by default
            visible=False,
        )

    def _trace_visibility(self, num_traces: int, trace_idx: int) -> list[bool]:
        """Get trace visibility as a plotly updatemenu button callback for per-group tracing."""
        return [i == trace_idx for i in range(num_traces)]

    def render(self, results: InstrumentationResults) -> str | None:
        """Render a bar chart from the instrumentation results as a HTML fragment.

        If the required job timing (and optionally the metadata) information is not available,
        or there are no jobs, this just returns `None` instead.

        """
        if not self.allow_missing_meta and all(job.meta is None for job in results.jobs.values()):
            return None

        # Group jobs into tests if configured to do so, and compute relevant aggregate information.
        # Tests are ordered by decreasing duration.
        tests = self._compute_test_aggregates(results)
        num_tests = len(tests)
        if num_tests == 0:
            return None

        # Determine item limits & title formatting from the configured max bars & grouping.
        item_type = "tests" if self.group_tests else "jobs"
        if self.max_bars is None or num_tests <= self.max_bars:
            num_visible = num_tests
            title_item_desc = f"{num_tests:,} {item_type}"
        else:
            num_visible = self.max_bars
            title_item_desc = f"top {num_visible} of {num_tests} {item_type}"

        # Bin the tests by category (up to top `num_visible` for each). Ensure that the 'All'
        # category is always ordered first.
        top_by_category = self._get_top_tests_by_category(results, tests, num_visible)
        categories = sorted(top_by_category, key=lambda c: (c != self.ALL_KEY, c))
        num_categories = len(categories)

        if self.color_fn is not None:
            color_map = {key: self.color_fn(key) for key in categories}
        else:
            color_map = make_repeating_color_map(sorted(categories), pc.qualitative.Plotly)

        # Create a bar trace and visibility toggle menu button for each category.
        traces: list[go.Bar] = []
        menu_buttons: list[dict[str, Any]] = []
        for i, key in enumerate(categories):
            test_info = [tests[test_group] for test_group in top_by_category[key]]
            traces.append(self._make_trace(results, test_info, color_map))
            menu_buttons.append(
                {
                    "label": key,
                    "method": "update",
                    "args": [{"visible": self._trace_visibility(num_categories, i)}],
                }
            )
        traces[0].visible = True

        # Hack: display buttons for <= some arbitrary threshold, otherwise a dropdown.
        # A better solution would dynamically determine the type based on the contents & layout.
        updatemenu_type, updatemenu_direction = (
            ("buttons", "right")
            if num_categories <= self.DROPDDOWN_KEY_THRESHOLD
            else ("dropdown", "down")
        )

        # Create the final figure from the constructed list of traces & buttons.
        # Configure extra layout / formatting settings.
        fig = go.Figure(data=traces)
        fig.update_layout(
            template="plotly_white",
            showlegend=False,
            title_text=f"<b>{item_type.capitalize()} by longest duration ({title_item_desc})</b>",
            title_xanchor="center",
            title_y=0.97,
            title_x=0.5,
            margin=self.margins,
            height=DEFAULT_VISUALIZATION_HEIGHT_PX,
            bargap=0.05,
        )
        fig.update_yaxes(autorange="reversed")

        duration_config = PLOTLY_TIMING_AXIS_CONFIG.copy()
        duration_config["title"] = "Duration (s)"
        fig.update_xaxes(showgrid=True, **duration_config)

        # Only render the 'All' button if we have at least 2 other categories to render
        if len(menu_buttons[1:]) == 1:
            menu_buttons = menu_buttons[1:]
        if menu_buttons:
            fig.update_layout(
                updatemenus=[
                    {
                        "buttons": menu_buttons,
                        "type": updatemenu_type,
                        "direction": updatemenu_direction,
                        "showactive": True,
                        # Arbitrary positioning that tends to render well
                        "x": 0.0,
                        "xanchor": "left",
                        "y": 1.055,
                        "bgcolor": "rgba(230,230,230,0.9)",
                        "bordercolor": "rgba(0,0,0,0)",
                        "borderwidth": 0,
                        "pad": {"r": 8, "t": 0, "b": 0},
                        "font_size": 13,
                    }
                ],
            )

        # Assume figure is never large enough to render to PNG because we control the limit here.
        return fig.to_html(**PLOTLY_HTML_FRAGMENT_CONFIG)


class LongestByStatusChart(LongestBarChart):
    """Renders plotly bar chart figures ranking the longest jobs, optionally split by status."""

    def __init__(self, *, max_bars: int | None = None) -> None:
        """Construct a LongestJobsByStatusChart.

        Args:
            max_bars: The maximum number of bars to show for each status (and overall).

        """
        super().__init__(
            max_bars=max_bars,
            category_fn=self._get_job_status,
            color_fn=self._get_status_color,
            allow_missing_meta=True,
        )

    def _get_job_status(self, job: JobInstrumentationResults) -> str:
        """Get the status from a job's recorded metadata, or 'Unknown' if it does not exist."""
        return "Unknown" if job.meta is None else job.meta.status

    def _get_status_color(self, status: str) -> str:
        """Get the (hex code string) color that a given job status should render with."""
        # TODO: can I get the status enum working in the Pydantic models so I can use it directly?
        color_mapping = {
            "Passed": "#04B34F",
            "Failed": "#BF1B00",
            "Killed": "#12436D",
        }
        return color_mapping.get(status, "#808080")


class LongestByToolChart(LongestBarChart):
    """Renders plotly bar chart figures ranking the longest jobs, partitioned by tool."""

    def __init__(
        self,
        *,
        group_tests: bool = False,
        max_bars: int | None = None,
        max_jobs_per_bar: int | None = None,
    ) -> None:
        """Construct a LongestByToolChart.

        Args:
            group_tests: Flag to group jobs of the same test by name. This allows timing results
              for the same tests to be combined in a stacked bar view.
            max_bars: The maximum number of bars to show for each tool (and overall).
            max_jobs_per_bar: If group_tests=True, the maximum number of stacked bars to
              render per bar. If there are too many bars to display, the bottom
              (N - max_jobs_per_bar + 1) jobs will be combined into a single bar.

        """
        self.title = f"Longest {'Tests' if group_tests else 'Jobs'} by Tool"

        super().__init__(
            group_tests=group_tests,
            category_fn=self._get_job_tool,
            max_bars=max_bars,
            max_jobs_per_bar=max_jobs_per_bar,
        )

    def _get_job_tool(self, job: JobInstrumentationResults) -> str:
        """Get the tool from a job's recorded metadata, or 'Unknown' if it does not exist."""
        return "Unknown" if job.meta is None else job.meta.tool


class LongestByBlockChart(LongestBarChart):
    """Renders plotly bar charts ranking the longest jobs, partitioned by block."""

    def __init__(
        self,
        *,
        group_tests: bool = False,
        max_bars: int | None = None,
        max_jobs_per_bar: int | None = None,
    ) -> None:
        """Construct a LongestByBlockChart.

        Args:
            group_tests: Flag to group jobs of the same test by name. This allows timing results
              for the same tests to be combined in a stacked bar view.
            max_bars: The maximum number of bars to show for each block (and overall).
            max_jobs_per_bar: If group_tests=True, the maximum number of stacked bars to
              render per bar. If there are too many bars to display, the bottom
              (N - max_jobs_per_bar + 1) jobs will be combined into a single bar.

        """
        self.title = f"Longest {'Tests' if group_tests else 'Jobs'} by Block"

        super().__init__(
            max_bars=max_bars,
            max_jobs_per_bar=max_jobs_per_bar,
            category_fn=self._get_job_block,
            group_tests=group_tests,
        )

    def _get_job_block(self, job: JobInstrumentationResults) -> str:
        """Get the block from a job's recorded metadata, or 'Unknown' if it does not exist."""
        return "Unknown" if job.meta is None else job.meta.block


# Default height in pixels for a usage/concurrency chart visualization
DEFAULT_USAGE_CHART_HEIGHT_PX: int = 800


class ConcurrencyLineGraph:
    """Renders plotly time series figures showing usage & concurrency info over time."""

    title = "Job Concurrency"

    def __init__(
        self, *, group_fn: Callable[[JobInstrumentationResults], str] | None = None
    ) -> None:
        """Construct a ConcurrencyLineGraph.

        Args:
            group_fn: A function to partition jobs into distinct (non-overlapping) subsets,
              by the category string that is returned. Defaults to `None`, meaning that no
              partitioning is applied.

        """
        self.group_fn: Callable[[JobInstrumentationResults], str] | None = group_fn

    def concurrency_events(
        self, job_timings: dict[str, ConcreteJobTimingMetrics]
    ) -> list[tuple[float, int]]:
        """Retrieve a list of concurrency events (changes in concurrency) for the given timings.

        Args:
            job_timings: A mapping of job IDs to timing metrics to get concurrency for.

        Returns:
            An ordered time-series list of tuples (time in seconds, active concurrent jobs).

        """
        start_times = [(timing.start_time, 1) for timing in job_timings.values()]
        end_times = [(timing.end_time, -1) for timing in job_timings.values()]
        job_events = sorted(start_times + end_times)

        concurrency_events: list[tuple[float, int]] = []
        concurrency = 0
        for event_time, concurrency_diff in job_events:
            concurrency += concurrency_diff  # (cumulative sum so far)
            concurrency_events.append((event_time, concurrency))

        return concurrency_events

    def _build(self, results: InstrumentationResults) -> Figure | None:
        """Build the plotly time series line graph figure for the given results."""
        job_timings = results.job_timings()
        if not job_timings:
            return None

        run_start_time, run_end_time = results.get_run_time_info()
        run_duration = run_end_time - run_start_time

        # Group jobs into subsets keyed by the configured `group_fn`.
        categories: dict[str, list[str]] = defaultdict(list)
        for job_id in job_timings:
            if self.group_fn is not None:
                key = self.group_fn(results.jobs[job_id])
                categories[key].append(job_id)
            else:
                categories["Concurrent Jobs"].append(job_id)
        categories = dict(sorted(categories.items()))
        color_map = make_repeating_color_map(categories, pc.qualitative.Plotly)

        # For each group, get concurrency events and plot them as a trace.
        fig = go.Figure()
        for key, jobs in categories.items():
            subset_timings = {job_id: job_timings[job_id] for job_id in jobs}
            concurrency_events = self.concurrency_events(subset_timings)
            event_times = [event[0] - run_start_time for event in concurrency_events]
            concurrency_vals = [event[1] for event in concurrency_events]

            fig.add_scatter(
                x=event_times,
                y=concurrency_vals,
                name=key,
                mode="lines",
                marker={"color": color_map[key]},
            )

        # Extra layout / formatting settings
        height = min(DEFAULT_USAGE_CHART_HEIGHT_PX, DEFAULT_VISUALIZATION_HEIGHT_PX)
        fig.update_layout(
            template="plotly_white",
            title_text="<b>Job Concurrency over Time</b>",
            title_x=0.5,
            margin={"t": 40},
            height=height,
        )
        fig.update_yaxes(title="Number of Concurrent Jobs", showgrid=True, tickformat=",")
        fig.update_xaxes(range=[0, run_duration], showgrid=True, **PLOTLY_TIMING_AXIS_CONFIG)

        # If we have multiple categories, show information about all series when hovering
        if len(categories) > 1:
            fig.update_layout(hovermode="x unified")

        return fig

    def render(self, results: InstrumentationResults) -> str | None:
        """Render a time series line graph from the instrumentation results as a HTML fragment.

        If the required job timing information is not available (or there are no jobs), just
        returns `None` instead.

        """
        fig = self._build(results)
        if fig is None:
            return None

        # Always render as HTML; we need > 100k jobs to make considering a PNG worthwhile.
        return fig.to_html(**PLOTLY_HTML_FRAGMENT_CONFIG)


class ToolUsageLineGraph(ConcurrencyLineGraph):
    """Time series chart showing concurrent tool usage over the run's lifetime."""

    title = "Tool Concurrency"

    def __init__(self) -> None:
        """Construct a ToolUsageLineGraph."""
        super().__init__(group_fn=self._get_job_tool)

    def _get_job_tool(self, job: JobInstrumentationResults) -> str:
        """Get the tool from a job's recorded metadata, or 'Unknown' if it does not exist."""
        if job.meta is None:
            return "Unknown"

        return job.meta.tool

    def render(self, results: InstrumentationResults) -> str | None:
        """Render a tool usage line graph from the instrumentation results as a HTML fragment.

        If the required job timing or metadata information is not available (or there are no
        jobs), just returns `None` instead.

        """
        if all(job.meta is None for job in results.jobs.values()):
            return None

        fig = self._build(results)
        if fig is None:
            return None

        fig.update_layout(title_text="<b>Tool Concurrency over Time</b>")
        fig.update_legends(title="Tool")

        return fig.to_html(**PLOTLY_HTML_FRAGMENT_CONFIG)


class BreakdownVisualization:
    """Renders pie & bar-chart figures showing job duration breakdown via some grouping."""

    title = "Job Breakdown"

    # Standard layout & formatting configuration
    MIN_PIE_HEIGHT_PX: int = 600
    MAX_BAR_PX: int = 50
    PIE_LABEL_THRESHOLD: float = 0.02  # (percentage in [0,1], i.e. 2%)
    PIE_HOLE_FRACTION: float = 0.6
    PIE_SEGMENT_PULL: float = 0.03
    SUBPLOT_SPACING: float = 0.12

    def __init__(
        self, *, group_type: str, group_fn: Callable[[JobInstrumentationResults], str]
    ) -> None:
        """Construct a BreakdownVisualization.

        Args:
            group_type: The name of the groupings (categorial type) being split on.
            group_fn: A function for splitting jobs into unique groups (returns a string category).

        """
        self.group_type = group_type
        self.group_fn = group_fn

        # Default margin layout information
        self.margins = {"t": 80, "b": 40, "l": 50, "r": 20}

    def _get_color_map(self, categories: dict[str, list[str]]) -> dict[str, Any]:
        """Build a colour map for the chart, using large palettes for more variety as is needed."""
        palette = pc.qualitative.Plotly
        if len(categories) > len(palette):
            extra_colors = [
                pc.qualitative.Bold,
                pc.qualitative.Safe,
                pc.qualitative.Vivid,
                pc.qualitative.D3,
                pc.qualitative.Set1,
                pc.qualitative.Set2,
            ]
            extra_index = 0
            while len(categories) > len(palette) and extra_index < len(extra_colors):
                palette += extra_colors[extra_index]
                extra_index += 1
        return make_repeating_color_map(categories, palette)

    def _build(self, results: InstrumentationResults) -> Figure | None:
        """Build the plotly breakdown (pie & bar chart) for the given results."""
        job_timings = results.job_timings()
        if not job_timings:
            return None

        # Group jobs into subsets keyed by the configured `group_fn`.
        categories: dict[str, list[str]] = defaultdict(list)
        group_durations: dict[str, float] = defaultdict(float)
        for job_id in job_timings:
            key = self.group_fn(results.jobs[job_id])
            categories[key].append(job_id)
            group_durations[key] += job_timings[job_id].duration
        total_duration = sum(group_durations.values())

        categories = dict(sorted(categories.items()))
        color_map = self._get_color_map(categories)

        # Determine the chart dimensions for the different subplots
        pie_chart_height = min(self.MIN_PIE_HEIGHT_PX, DEFAULT_VISUALIZATION_HEIGHT_PX)
        vertical_margins = self.margins.get("t", 0) + self.margins.get("b", 0)
        bars_height = len(categories) * self.MAX_BAR_PX + vertical_margins
        bar_chart_height = min(DEFAULT_VISUALIZATION_HEIGHT_PX * 2 - pie_chart_height, bars_height)
        total_height = pie_chart_height + bar_chart_height
        row_heights = [pie_chart_height / total_height, bar_chart_height / total_height]

        # Draw 2 subplots of the same data - a pie chart on top & a bar chart on bottom.
        fig = make_subplots(
            rows=2,
            cols=1,
            row_heights=row_heights,
            vertical_spacing=self.SUBPLOT_SPACING,
            specs=[[{"type": "pie"}], [{"type": "bar"}]],
        )

        sorted_durations = sorted(group_durations.items(), key=lambda kv: kv[1], reverse=True)
        keys, durations = zip(*sorted_durations, strict=True)
        percentages = [duration / total_duration for duration in durations]
        colors = [color_map[key] for key in keys]
        display_text = [
            f"{key}<br>{pct:.2%}" if pct > self.PIE_LABEL_THRESHOLD else ""
            for key, pct in zip(keys, percentages, strict=True)
        ]
        hovers = [
            (
                f"{self.group_type.capitalize()}: {key}<br>"
                f"Number of Jobs: {len(categories[key])}<br>"
                f"Total Duration: {format_time_metric(duration, omit_zero=True)}<br>"
                f"Percentage: {pct:.2%}"
            )
            for key, duration, pct in zip(keys, durations, percentages, strict=True)
        ]

        fig.add_trace(
            go.Pie(
                labels=keys,
                values=durations,
                text=display_text,
                textinfo="text",
                textposition="outside",
                showlegend=False,
                hole=self.PIE_HOLE_FRACTION,
                pull=self.PIE_SEGMENT_PULL,
                marker={"colors": colors},
                customdata=hovers,
                hovertemplate="%{customdata}<extra></extra>",
            ),
            row=1,
            col=1,
        )

        # Traces must be added individually to allow filtering via the legend.
        texts = [format_time(duration, omit_zero=True) for duration in durations]
        for key, duration, text, color, hover in zip(
            keys, durations, texts, colors, hovers, strict=True
        ):
            fig.add_trace(
                go.Bar(
                    y=[key],
                    x=[duration],
                    name=key,
                    text=[text],
                    orientation="h",
                    textposition="outside",
                    textangle=0,
                    cliponaxis=False,
                    showlegend=True,
                    marker={"color": color},
                    customdata=[hover],
                    hovertemplate="%{customdata}<extra></extra>",
                ),
                row=2,
                col=1,
            )

        # Extra layout / formatting settings
        fig.update_layout(
            template="plotly_white",
            title_text=f"<b>Total job runtime per {self.group_type.lower()}</b>",
            title_x=0.125,
            title_xanchor="left",
            margin=self.margins,
            height=total_height,
            bargap=0.1,
        )
        fig.update_legends(
            title=self.group_type.capitalize(),
            x=1.02,
            y=row_heights[1] * 0.4,
            xanchor="left",
            yanchor="middle",
        )
        fig.update_yaxes(autorange="reversed")
        fig.update_xaxes(showgrid=True, **PLOTLY_TIMING_AXIS_CONFIG)

        return fig

    def render(self, results: InstrumentationResults) -> str | None:
        """Render a breakdown (pie/bar chart) from the instrumentation results as a HTML fragment.

        If the required job timing information is not available (or there are no jobs), just
        returns `None` instead.

        """
        fig = self._build(results)
        if fig is None:
            return None

        return fig.to_html(**PLOTLY_HTML_FRAGMENT_CONFIG)


class ToolBreakdown(BreakdownVisualization):
    """Breakdown pie/bar chart showing aggregated job duration per tool used."""

    title = "Runtime per Tool"

    def __init__(self) -> None:
        """Construct a ToolBreakdown."""
        super().__init__(group_type="tool", group_fn=self._get_job_tool)

    def _get_job_tool(self, job: JobInstrumentationResults) -> str:
        """Get the tool from a job's recorded metadata, or 'Unknown' if it does not exist."""
        if job.meta is None:
            return "Unknown"

        return job.meta.tool

    def render(self, results: InstrumentationResults) -> str | None:
        """Render a per-tool breakdown graph from the instrumentation results as a HTML fragment.

        If the required job timing or metadata information is not available (or there are no
        jobs), just returns `None` instead.

        """
        if all(job.meta is None for job in results.jobs.values()):
            return None

        return super().render(results)


class BlockVariantBreakdown(BreakdownVisualization):
    """Breakdown pie/bar chart showing aggregated job duration per block (variant) tested."""

    title = "Runtime per Block"

    def __init__(self) -> None:
        """Construct a BlockVariantBreakdown."""
        super().__init__(group_type="block", group_fn=self._get_job_block_variant)

    def _get_job_block_variant(self, job: JobInstrumentationResults) -> str:
        """Get the block (variant) from a job's metadata, or 'Unknown' if it does not exist."""
        if job.meta is None:
            return "Unknown"

        return job.meta.block + (f"_{job.meta.block_variant}" if job.meta.block_variant else "")

    def render(self, results: InstrumentationResults) -> str | None:
        """Render a per-block breakdown graph from the instrumentation results as a HTML fragment.

        If the required job timing or metadata information is not available (or there are no
        jobs), just returns `None` instead.

        """
        if all(job.meta is None for job in results.jobs.values()):
            return None

        return super().render(results)


# Register default / built-in instrumentation visualizations
# TODO: maybe look at reworking protocol and just give name here, the class structure is kind of weird
def get_standard_instrumentations(*, uncapped: bool = False) -> list[InstrumentationVisualizer]:
    """TODO"""
    # TODO: this uncapped stuff is a mess, figure out a nicer way.
    return [
        LongestByStatusChart(max_bars=(None if uncapped else 250)),
        LongestByToolChart(max_bars=(None if uncapped else 250)),
        LongestByToolChart(
            group_tests=True,
            max_bars=(None if uncapped else 75),
            max_jobs_per_bar=(None if uncapped else 6),
        ),
        LongestByBlockChart(max_bars=(None if uncapped else 50)),
        LongestByBlockChart(
            group_tests=True,
            max_bars=(None if uncapped else 20),
            max_jobs_per_bar=(None if uncapped else 6),
        ),
        BlockVariantBreakdown(),
        ToolBreakdown(),
        ToolUsageLineGraph(),
        GanttChart(),
        ParallelismChart(),
    ]


# TODO: what should I do with the registry: empty by default? add by default
for standard_vis in get_standard_instrumentations():
    register_instrumentation_visualizer(standard_vis)


# Local testing, TODO remove the below


def _make_fake_results_for_testing(
    num_jobs: int = 500, parallel: bool = True
) -> InstrumentationResults:
    import random
    import string
    from datetime import datetime, timedelta, timezone

    from dvsim.instrumentation import (
        JobInstrumentationMetadata,
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
            _outdir = Path("./real_metrics/generated") / results_path.name.removesuffix(".json")
            _artifacts = render_html_report(
                report, visualizations=get_standard_instrumentations(), outdir=_outdir
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
                _fake_report, visualizations=get_standard_instrumentations(), outdir=_outdir
            )
            print(f"Fake instrumentation report data written under {_outdir}")
