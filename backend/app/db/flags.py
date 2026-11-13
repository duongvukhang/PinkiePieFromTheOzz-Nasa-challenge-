"""Quality and identity flag bitmasks for detections, measurements, and associations."""

from enum import IntFlag


class DetectionFlag(IntFlag):
    """Bitmask flags shared across detections, epoch_measurements, and associations."""

    SATURATED = 1 << 0
    BLENDED = 1 << 1
    EDGE = 1 << 2
    BAD_PIX = 1 << 3
    COSMIC = 1 << 4
    PERSIST = 1 << 5
    GHOST_SPIKE = 1 << 6
    LOW_SNR = 1 << 7
    EXTENDED = 1 << 8
    STREAK = 1 << 9
    USER_EPOCH = 1 << 10
    REPROJECTED = 1 << 11
    AMBIGUOUS_MATCH = 1 << 16
    LOST = 1 << 17
    MERGED = 1 << 18
    USER_OVERRIDE = 1 << 19
    DUPLICATE_PROPOSAL = 1 << 20
