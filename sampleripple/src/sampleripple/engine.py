"""Orchestration from validated configuration to classified timelines."""

from __future__ import annotations

from dataclasses import dataclass

from .anomalies import Anomaly, detect_anomalies, anomaly_sort_key
from .certificate import GroupSensitivity
from .config import AppConfig
from .contracts import Contract
from .loaders import LoadedSample, load_stream
from .sensitivity import build_sensitivity
from .timeline import GroupTimeline, build_timelines


@dataclass(frozen=True)
class StreamRun:
    contract: Contract
    sample: LoadedSample
    timelines: tuple[GroupTimeline, ...]
    anomalies: tuple[Anomaly, ...]
    sensitivity: tuple[GroupSensitivity, ...]


@dataclass(frozen=True)
class Verification:
    config: AppConfig
    streams: tuple[StreamRun, ...]
    anomalies: tuple[Anomaly, ...]
    sensitivity: tuple[tuple[GroupSensitivity, ...], ...]

    @property
    def input_files(self) -> tuple[str, ...]:
        paths: list[str] = []
        seen: set[str] = set()
        for stream in self.streams:
            for path in stream.sample.files:
                text = stream.contract.display_path(path)
                if text not in seen:
                    seen.add(text)
                    paths.append(text)
        return tuple(paths)


def run_verification(config: AppConfig) -> Verification:
    """Load every configured stream and classify all group timelines."""

    streams: list[StreamRun] = []
    flattened: list[Anomaly] = []
    all_sensitivity: list[tuple[GroupSensitivity, ...]] = []
    for contract in config.streams:
        sample = load_stream(contract)
        timelines = build_timelines(sample.points, contract)
        stream_anomalies: list[Anomaly] = []
        for timeline in timelines:
            stream_anomalies.extend(detect_anomalies(contract, timeline))
        kept = tuple(sorted(stream_anomalies, key=anomaly_sort_key))
        sensitivity = build_sensitivity(contract, timelines)
        all_sensitivity.append(sensitivity)
        streams.append(
            StreamRun(
                contract=contract,
                sample=sample,
                timelines=timelines,
                anomalies=kept,
                sensitivity=sensitivity,
            )
        )
        flattened.extend(kept)
    return Verification(
        config=config,
        streams=tuple(streams),
        anomalies=tuple(flattened),
        sensitivity=tuple(all_sensitivity),
    )
