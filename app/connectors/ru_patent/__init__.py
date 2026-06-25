"""Russian patent connectors module.

Includes:
- RospatentConnector: Rospatent Open Data / Open API
- FIPSSearchConnector: FIPS information search system
- FIPSRegistersConnector: FIPS Open Registers for legal status
"""

from app.connectors.ru_patent.rospatent import RospatentConnector
from app.connectors.ru_patent.fips_search import FIPSSearchConnector
from app.connectors.ru_patent.fips_registers import FIPSRegistersConnector

__all__ = [
    "RospatentConnector",
    "FIPSSearchConnector",
    "FIPSRegistersConnector",
]
