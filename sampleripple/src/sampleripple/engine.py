"""Orchestration from validated configuration to classified timelines."""

from __future__ import annotations

from dataclasses import dataclass

from .anomalies import Anomaly, detect_anomalies, anomaly_sort_key
from .config import AppConfig
from .contracts import Contract
from .loaders import LoadedSample, load_stream
from .timeline import GroupTimeline, build_timelines


@dataclass(frozen=True)
class StreamRun:
    contract: Contract
    sample: LoadedSample
    timelines: tuple[GroupTimeline, ...]
    anomalies: tuple[Anomaly, ...]


@dataclass(frozen=True)
class Verification:
    config: AppConfig
    streams: tuple[StreamRun, ...]
    anomalies: tuple[Anomaly, ...]

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
    for contract in config.streams:
        sample = load_stream(contract)
        timelines = build_timelines(sample.points, contract)
        stream_anomalies: list[Anomaly] = []
        for timeline in timelines:
            stream_anomalies.extend(detect_anomalies(contract, timeline))
        kept = tuple(sorted(stream_anomalies, key=anomaly_sort_key))
        streams.append(
            StreamRun(
                contract=contract,
                sample=sample,
                timelines=timelines,
                anomalies=kept,
            )
        )
        flattened.extend(kept)
    return Verification(config=config, streams=tuple(streams), anomalies=tuple(flattened))
