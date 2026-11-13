from __future__ import annotations

from dataclasses import dataclass

from .cloud import AirtapCloudCoordinator
from .device import ACInfinityDevice
from .coordinator import ACInfinityDataUpdateCoordinator


@dataclass
class ACInfinityData:
    title: str
    device: ACInfinityDevice
    coordinator: ACInfinityDataUpdateCoordinator


@dataclass
class ACInfinityCloudData:
    """A vent controlled through the AC Infinity cloud (type 48)."""

    title: str
    address: str
    coordinator: AirtapCloudCoordinator
