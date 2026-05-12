"""SalesBrain package."""

from .config import SalesBrainConfig, load_config
from .service import SalesBrainService

__all__ = ["SalesBrainConfig", "SalesBrainService", "load_config"]

__version__ = "0.2.0"
