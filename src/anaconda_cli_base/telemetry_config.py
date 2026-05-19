from typing import Optional

from anaconda_cli_base.config import AnacondaBaseSettings


class TelemetryConfig(AnacondaBaseSettings, table_name="telemetry"):
    """Telemetry configuration.

    Reads from [telemetry] in ~/.anaconda/config.toml.
    Environment variables use ANACONDA_TELEMETRY_ prefix.
    """

    endpoint: Optional[str] = None
    anon_usage: bool = True
    skip_internet_check: bool = True
