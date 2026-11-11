"""External archive provider plug-ins and registry."""

from app.providers.base import (
    CatalogSource,
    CircuitBreaker,
    EphemerisPoint,
    FrameRef,
    NewReleaseReport,
    Provider,
    ProviderError,
    ProviderQueryError,
    ProviderRateLimitError,
    ProviderRegistry,
    ProviderUnavailableError,
    RateLimiter,
    ReleaseDescriptor,
    StoredFrame,
    call_with_retry,
    default_registry,
    get_provider,
    list_providers,
    propagate_catalog_sources,
)
from app.providers.gaia import GaiaMirror, GaiaProvider
from app.providers.horizons import HorizonsProvider
from app.providers.mpc import MpcProvider
from app.providers.simbad import SimbadProvider
from app.providers.spherex_irsa import ReleaseProfile, SpherexIrsaProvider
from app.providers.wise import WiseProvider

__all__ = [
    "CatalogSource",
    "CircuitBreaker",
    "EphemerisPoint",
    "FrameRef",
    "GaiaMirror",
    "GaiaProvider",
    "HorizonsProvider",
    "MpcProvider",
    "NewReleaseReport",
    "Provider",
    "ProviderError",
    "ProviderQueryError",
    "ProviderRateLimitError",
    "ProviderRegistry",
    "ProviderUnavailableError",
    "RateLimiter",
    "ReleaseDescriptor",
    "ReleaseProfile",
    "SimbadProvider",
    "SpherexIrsaProvider",
    "StoredFrame",
    "WiseProvider",
    "call_with_retry",
    "default_registry",
    "get_provider",
    "list_providers",
    "propagate_catalog_sources",
]
