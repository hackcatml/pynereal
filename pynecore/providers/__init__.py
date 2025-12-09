from pathlib import Path

from .ccxt import CCXTProvider
from .capitalcom import CapitalComProvider

# List of available providers
available_providers = tuple(
    p.stem for p in Path(__file__).parent.resolve().glob('*.py') if
    p.name not in ('__init__.py', 'provider.py')
)
