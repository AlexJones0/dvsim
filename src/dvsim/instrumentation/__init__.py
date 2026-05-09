# Copyright lowRISC contributors (OpenTitan project).
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""DVSim Scheduler Instrumentation."""

from dvsim.instrumentation.base import InstrumentationAggregator, SchedulerInstrumentation
from dvsim.instrumentation.compute import ComputeInstrumentation
from dvsim.instrumentation.factory import InstrumentationFactory
from dvsim.instrumentation.metadata import MetadataInstrumentation
from dvsim.instrumentation.records import (
    ConcreteJobTimingMetrics,
    InstrumentationMetrics,
    InstrumentationResults,
    JobComputeMetrics,
    JobInstrumentationMetadata,
    JobMetrics,
    JobTimingMetrics,
    SchedulerComputeMetrics,
    SchedulerMetrics,
    SchedulerTimingMetrics,
)
from dvsim.instrumentation.runtime import flush, get, set_instrumentation, set_report_path
from dvsim.instrumentation.timing import TimingInstrumentation

__all__ = (
    "ComputeInstrumentation",
    "ConcreteJobTimingMetrics",
    "InstrumentationAggregator",
    "InstrumentationFactory",
    "InstrumentationMetrics",
    "InstrumentationResults",
    "JobComputeMetrics",
    "JobInstrumentationMetadata",
    "JobMetrics",
    "JobTimingMetrics",
    "MetadataInstrumentation",
    "SchedulerComputeMetrics",
    "SchedulerInstrumentation",
    "SchedulerMetrics",
    "SchedulerTimingMetrics",
    "TimingInstrumentation",
    "flush",
    "get",
    "set_instrumentation",
    "set_report_path",
)
